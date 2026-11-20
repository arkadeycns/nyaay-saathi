"""
Eval runner: executes eval/cases.yaml live and saves RAW, UNEDITED final states.

    python -m eval.run_eval                       # all cases, active provider
    python -m eval.run_eval --provider gemini     # swappability run on a second provider
    python -m eval.run_eval --bucket C --sleep 12 # one bucket
    python -m eval.run_eval --case A01_househelp_unpaid
    python -m eval.run_eval --limit 3 --langsmith

Outputs: eval/outputs/<run-id>/<case>.json (+ summary.json, summary.md).
Nothing in the saved states is edited; metrics are computed from them separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import traceback
from datetime import datetime

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

CASES = pathlib.Path(__file__).parent / "cases.yaml"
OUT = pathlib.Path(__file__).parent / "outputs"
DEFAULT_FOLLOWUP = "I don't know more than this."


def load_cases() -> list[dict]:
    return yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]


def corpus_hash() -> str:
    h = hashlib.sha256()
    for f in sorted((ROOT / "corpus" / "processed").glob("*.jsonl")):
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


# ── per-flow runners: each returns the final state dict, untouched ──
def run_advisor(case: dict) -> dict:
    from graph import advisor_graph, run
    turns = list(case.get("turns") or [case.get("input", "")])
    messages: list[dict] = []
    state: dict = {"clarify_rounds": 0}
    transcript = []
    for i in range(config.MAX_CLARIFY_ROUNDS + 1):
        messages.append({"role": "user", "content": turns[i] if i < len(turns) else DEFAULT_FOLLOWUP})
        state = run(advisor_graph(), {"messages": list(messages), "clarify_rounds": state.get("clarify_rounds", 0), "audit": []})
        transcript.append({"turn": i, "outcome": state.get("outcome")})
        if state.get("outcome") != "clarify":
            break
        messages.append({"role": "assistant", "content": "\n".join(state.get("questions", []))})
    state["_turns"] = transcript
    return state


def run_decoder(case: dict) -> dict:
    from graph import decoder_graph, run
    return run(decoder_graph(), {"notice_text": case["input"], "received_date": case.get("received_date"), "audit": []})


def run_simulator(case: dict) -> dict:
    import nodes_simulator as S  # written separately; guarded by caller
    out: dict = {"refusals": []}
    session = S.start(case["category"], case.get("context"))
    session = S.opponent_turn(session, None)
    for reply in case.get("user_replies", []):
        reason = S.refuse_reason(reply)
        if reason:
            out["refusals"].append({"reply": reply, "reason": reason})
            continue
        if session.get("done"):
            break
        session = S.opponent_turn(session, reply)
    out["session"] = session
    out["coaching"] = S.coaching(session)
    out["outcome"] = "refuse" if out["refusals"] else "full"
    return out


def run_voice(case: dict) -> dict:
    path = ROOT / case["audio_path"]
    if not path.exists():
        return {"outcome": "skipped", "skipped": "no audio: user must record " + case["audio_path"]}
    from llm import get_llm
    text = get_llm().transcribe(path.read_bytes(), case.get("language"), suffix=path.suffix)
    st = run_advisor({"turns": [text]})
    st["_transcript"] = text
    return st


RUNNERS = {"advisor": run_advisor, "decoder": run_decoder, "simulator": run_simulator, "voice": run_voice}


def _langsmith_log(cases: list[dict], results: list[dict], run_id: str) -> str:
    """Optional: push synthetic cases + results to LangSmith as a dataset/experiment."""
    if not config.LANGSMITH_ENABLED:
        return "LangSmith skipped (set LANGSMITH_TRACING=true and LANGSMITH_API_KEY)."
    try:
        from langsmith import Client
        from eval.metrics import evaluate_case
        client = Client()
        name = "legal-ai-eval"
        ds = next(iter(client.list_datasets(dataset_name=name)), None) or client.create_dataset(name, description="Synthetic eval cases (no real user data)")
        existing = {e.metadata.get("case_id") for e in client.list_examples(dataset_id=ds.id) if e.metadata}
        for c in cases:
            if c["id"] not in existing:
                client.create_example(inputs={"case": c}, outputs={k: v for k, v in c.items() if k.startswith(("expected", "must", "expect"))},
                                      dataset_id=ds.id, metadata={"case_id": c["id"], "bucket": c["bucket"]})
        proj = f"legal-ai-eval-{run_id}"
        for c, r in zip(cases, results):
            checks = evaluate_case(c, r)
            import uuid
            rid = uuid.uuid4()
            client.create_run(id=rid, name=c["id"], run_type="chain", project_name=proj, inputs={"case_id": c["id"]},
                              outputs={"outcome": r.get("outcome"), "checks": checks}, extra={"metadata": {"bucket": c["bucket"]}})
            # Each pass/fail check becomes a LangSmith feedback score, so runs can be filtered and compared.
            for key, val in (checks.get("checks") or checks).items():
                if isinstance(val, bool):
                    client.create_feedback(rid, key=key, score=int(val))
        return f"LangSmith: dataset '{name}', project '{proj}' logged."
    except Exception as exc:  # never fail the eval because tracing failed
        return f"LangSmith logging failed: {exc}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    ap.add_argument("--bucket")
    ap.add_argument("--case")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=8.0, help="seconds between cases (Groq free tier: 8K tokens/min per model)")
    ap.add_argument("--langsmith", action="store_true")
    ap.add_argument("--resume", help="run id to continue: cases already saved there are kept as-is, not re-run")
    args = ap.parse_args()

    if args.provider:
        config.ACTIVE_PROVIDER = args.provider
    import llm
    cases = load_cases()
    if args.bucket:
        cases = [c for c in cases if c["bucket"] == args.bucket.upper()]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if args.limit:
        cases = cases[: args.limit]

    run_id = args.resume or f"{datetime.now():%Y%m%d_%H%M%S}_{config.ACTIVE_PROVIDER}"
    out_dir = OUT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": run_id, "provider": config.ACTIVE_PROVIDER, "models": config.get_provider().models,
            "corpus_hash": corpus_hash(), "as_of": config.AS_OF, "gate_threshold": config.RETRIEVAL_GATE_THRESHOLD}
    from eval.metrics import evaluate_case, aggregate, markdown_report

    results, checks = [], []
    ran = 0
    for i, case in enumerate(cases):
        saved = out_dir / f"{case['id']}.json"
        if args.resume and saved.exists():
            rec = json.loads(saved.read_text(encoding="utf-8"))  # keep earlier raw output untouched
            checks.append(evaluate_case(case, rec))
            results.append(rec)
            continue
        if ran:
            time.sleep(args.sleep)
        ran += 1
        print(f"[{i + 1}/{len(cases)}] {case['id']} ({case['flow']})", flush=True)
        llm.clear_run_log()
        t0 = time.time()
        rec: dict = {"case_id": case["id"], "bucket": case["bucket"], "flow": case["flow"], "started": datetime.now().isoformat(), **meta}
        try:
            state = RUNNERS[case["flow"]](case)
        except ModuleNotFoundError as exc:
            state = {"outcome": "skipped", "skipped": f"module missing: {exc}"}
        except Exception as exc:
            state = {"outcome": "error", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["llm_calls"] = llm.get_run_log()
        rec["state"] = state
        (out_dir / f"{case['id']}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        chk = evaluate_case(case, rec)
        checks.append(chk)
        results.append(rec)
        fails = [k for k, v in chk["checks"].items() if v is False]
        print(f"    outcome={state.get('outcome')!s:<11} {rec['elapsed_s']:>6}s  {'PASS' if not fails else 'FAIL ' + ','.join(fails)}", flush=True)

    agg = aggregate(cases, results, checks)
    (out_dir / "summary.json").write_text(json.dumps({"meta": meta, "aggregate": agg, "cases": checks}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    md = markdown_report(meta, agg, checks)
    if args.langsmith:
        md += "\n\n" + _langsmith_log(cases, [r["state"] | {"elapsed_s": r["elapsed_s"]} for r in results], run_id)
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"\nRaw outputs: {out_dir}")


if __name__ == "__main__":
    main()
