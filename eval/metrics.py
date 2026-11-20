"""
Metrics over saved eval outputs. Per-case expectation checks + aggregate quality numbers.

    python -m eval.metrics <run-id>                 # recompute summary for a saved run
    python -m eval.metrics --sample-sheet <run-id>  # fill grading_sheet rows for 10 random full cases
"""
from __future__ import annotations

import csv
import json
import pathlib
import random
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = pathlib.Path(__file__).parent / "outputs"

# Paid-tier price assumption for unit economics (USD per 1M tokens). Not from a live price page.
PRICE_ASSUMPTION = {"note": "TODO_USER_VERIFY against current Groq pricing page",
                    "input_per_m": 0.15, "output_per_m": 0.60}


def outcome_of(state: dict) -> str:
    if state.get("blocked"):
        return "refuse"
    o = state.get("outcome")
    if o in (None, "full") and (state.get("advice") or state.get("explanation") or state.get("coaching")):
        return "full"
    return o or "none"


def visible_text(state: dict) -> str:
    """Everything a user would read (advice, rules-rendered lists, escalation text)."""
    parts = []
    for key in ("advice", "explanation"):
        a = state.get(key) or {}
        parts += [a.get("plain_summary", "")] + [c.get("text", "") for c in a.get("claims", [])]
        parts += a.get("cannot_assess", []) + a.get("lawyer_needed_when", [])
    esc = state.get("escalation") or state.get("triage") or {}
    parts += esc.get("reasons", []) + esc.get("safe_steps", [])
    parts += state.get("questions", []) + [state.get("crisis_message", ""), state.get("blocked") or ""]
    res = state.get("result") or {}
    parts += [e.get("item", "") + " " + e.get("how_to_get", "") for e in res.get("evidence", [])]
    parts += [p.get("name", "") + " " + p.get("risk", "") for p in res.get("pathways", [])]
    nf = state.get("notice") or {}
    for k in ("demand_amount", "deadline", "demand_action"):
        if isinstance(nf.get(k), dict):
            parts.append(nf[k].get("value") or "")
    coach = state.get("coaching") or {}
    parts.append(coach.get("summary", ""))
    return "\n".join(p for p in parts if p)


def _contains(text: str, term: str) -> bool:
    if term.isupper() and len(term) <= 4:   # e.g. "HR": case-sensitive whole word
        return re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text) is not None
    return term.lower() in text.lower()


def _claims(state: dict) -> list[dict]:
    return (state.get("advice") or state.get("explanation") or {}).get("claims", [])


def _deadline_status(state: dict) -> str | None:
    res = state.get("result") or {}
    if isinstance(res.get("deadline"), dict):
        return res["deadline"].get("status")
    if res.get("limitation"):
        return res["limitation"].get("status")
    return None


def evaluate_case(case: dict, rec: dict) -> dict:
    state = rec.get("state", rec)
    out = outcome_of(state)
    text = visible_text(state)
    c: dict[str, bool | None] = {}
    if out in ("skipped", "error"):
        return {"case_id": case["id"], "bucket": case["bucket"], "outcome": out, "checks": {"ran": out != "error"},
                "note": state.get("skipped") or state.get("error")}
    if "expected_outcome" in case:
        c["outcome"] = out == case["expected_outcome"]
    if "must_escalate" in case:
        c["escalated"] = (out == "escalate") == bool(case["must_escalate"])
    if "expected_escalation_kind" in case:
        kinds = (state.get("escalation") or state.get("triage") or {}).get("kinds", [])
        c["escalation_kind"] = case["expected_escalation_kind"] in kinds
    facts = state.get("facts") or {}
    if "expected_category" in case:
        c["category"] = facts.get("category") == case["expected_category"]
    if "expected_context" in case:
        c["context"] = (state.get("context") or {}).get("id") == case["expected_context"]
    if case.get("must_cite_any"):
        cited = {cid for cl in _claims(state) for cid in cl.get("cites", [])}
        c["cites_expected"] = bool(cited & set(case["must_cite_any"]))
    if case.get("must_not_contain"):
        bad = [t for t in case["must_not_contain"] if _contains(text, t)]
        c["no_forbidden_text"] = not bad
        if bad:
            c["_forbidden_found"] = bad  # type: ignore[assignment]
    if case.get("must_contain_any"):
        c["contains_expected"] = any(_contains(text, t) for t in case["must_contain_any"])
    if case.get("expect_legacy_flag"):
        provs = state.get("provisions") or []
        c["legacy_flagged"] = any(p.get("status") in ("repealed", "not_in_corpus") for p in provs) or "repealed" in text.lower()
    if "expect_deadline_status" in case:
        c["deadline_status"] = _deadline_status(state) == case["expect_deadline_status"]
    if case.get("expect_coaching_verified"):
        c["coaching_verified"] = bool((state.get("coaching") or {}).get("verified"))
    if case.get("max_seconds"):
        c["latency"] = rec.get("elapsed_s", 0) <= case["max_seconds"]
    forbidden = c.pop("_forbidden_found", None)
    return {"case_id": case["id"], "bucket": case["bucket"], "outcome": out, "checks": c,
            "passed": all(v is not False for v in c.values()), "forbidden_found": forbidden,
            "elapsed_s": rec.get("elapsed_s")}


def _citation_validity(state: dict) -> tuple[int, int]:
    from verify import check_claim
    from schemas import Claim
    srcs = {cid: s.get("text", "") for cid, s in (state.get("sources") or {}).items()}
    ok = tot = 0
    for cl in _claims(state):
        if cl.get("tag") == "Practical":
            continue
        tot += 1
        try:
            ok += not check_claim(Claim(**cl), srcs)
        except Exception:
            pass
    return ok, tot


def _leakage(state: dict) -> tuple[int, int]:
    """Final claims that the LAST verifier attempt judged unsupported/partial but still shipped."""
    log = state.get("verifier_log") or []
    if not log:
        return 0, len(_claims(state))
    last = log[-1]
    bad = {v["id"] for v in last.get("verdicts", []) if v.get("verdict") in ("unsupported", "partially_supported")}
    if not last.get("passed"):
        # drop_failed removed failing claims; partial ones remain and count as leakage
        bad = {v["id"] for v in last.get("verdicts", []) if v.get("verdict") == "partially_supported"}
    return len(bad), len(_claims(state))


def aggregate(cases: list[dict], results: list[dict], checks: list[dict]) -> dict:
    by_id = {c["id"]: c for c in cases}
    states = [r.get("state", {}) for r in results]
    cv_ok = cv_tot = leak = claims = 0
    for s in states:
        a, b = _citation_validity(s)
        cv_ok, cv_tot = cv_ok + a, cv_tot + b
        l, n = _leakage(s)
        leak, claims = leak + l, claims + n
    esc_expected = [ch for ch in checks if by_id[ch["case_id"]].get("must_escalate")]
    esc_pred = [ch for ch in checks if ch["outcome"] == "escalate"]
    esc_tp = [ch for ch in esc_expected if ch["outcome"] == "escalate"]
    bait = [ch for ch in checks if ch["bucket"] == "B" and ch["outcome"] not in ("skipped", "error")]
    legacy = [ch for ch in checks if "legacy_flagged" in ch["checks"]]
    ctxfit = [ch for ch in checks if "no_forbidden_text" in ch["checks"]]
    cat = [ch for ch in checks if "category" in ch["checks"]]
    outc = [ch for ch in checks if "outcome" in ch["checks"]]
    lat = sorted(r["elapsed_s"] for r in results if r.get("state", {}).get("outcome") not in ("skipped", "error"))
    toks = [sum(c.get("prompt_tokens", 0) + c.get("completion_tokens", 0) for c in r.get("llm_calls", [])) for r in results]
    toks = [t for t in toks if t]
    inp = [sum(c.get("prompt_tokens", 0) for c in r.get("llm_calls", [])) for r in results]
    outp = [sum(c.get("completion_tokens", 0) for c in r.get("llm_calls", [])) for r in results]
    cost = [(i * PRICE_ASSUMPTION["input_per_m"] + o * PRICE_ASSUMPTION["output_per_m"]) / 1e6 for i, o in zip(inp, outp) if i or o]
    rate = lambda a, b: None if not b else round(a / b, 3)  # noqa: E731
    pct = lambda xs, p: None if not xs else xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
    return {
        "cases_run": len(results),
        "cases_passed": sum(1 for ch in checks if ch.get("passed")),
        "skipped": sum(1 for ch in checks if ch["outcome"] == "skipped"),
        "errors": sum(1 for ch in checks if ch["outcome"] == "error"),
        "citation_validity": rate(cv_ok, cv_tot), "law_claims_checked": cv_tot,
        "unsupported_leakage": rate(leak, claims), "claims_total": claims,
        "escalation_recall": rate(len(esc_tp), len(esc_expected)),
        "escalation_precision": rate(len([c for c in esc_pred if by_id[c["case_id"]].get("must_escalate")]), len(esc_pred)),
        "bait_pass_rate": rate(sum(1 for ch in bait if ch.get("passed")), len(bait)),
        "legacy_map_hit_rate": rate(sum(1 for ch in legacy if ch["checks"]["legacy_flagged"]), len(legacy)),
        "context_fit_rate": rate(sum(1 for ch in ctxfit if ch["checks"]["no_forbidden_text"]), len(ctxfit)),
        "category_accuracy": rate(sum(1 for ch in cat if ch["checks"]["category"]), len(cat)),
        "outcome_accuracy": rate(sum(1 for ch in outc if ch["checks"]["outcome"]), len(outc)),
        "latency_p50_s": pct(lat, 0.5), "latency_p95_s": pct(lat, 0.95),
        "tokens_per_session_mean": round(statistics.mean(toks)) if toks else None,
        "est_cost_per_session_usd": round(statistics.mean(cost), 5) if cost else None,
        "price_assumption": PRICE_ASSUMPTION,
    }


def markdown_report(meta: dict, agg: dict, checks: list[dict]) -> str:
    lines = [f"# Eval run {meta['run_id']}", "",
             f"Provider `{meta['provider']}` · models {meta['models']} · corpus `{meta['corpus_hash']}` · AS_OF {meta['as_of']}", "",
             "| Metric | Value |", "|---|---|"]
    for k, v in agg.items():
        if k != "price_assumption":
            lines.append(f"| {k} | {v} |")
    lines += ["", f"Cost uses {agg['price_assumption']} (assumption, not a quote).", "",
              "| Case | Bucket | Outcome | Pass | Failed checks |", "|---|---|---|---|---|"]
    for ch in checks:
        failed = [k for k, v in ch["checks"].items() if v is False]
        extra = f" (found {ch['forbidden_found']})" if ch.get("forbidden_found") else ""
        lines.append(f"| {ch['case_id']} | {ch['bucket']} | {ch['outcome']} | {'✅' if ch.get('passed') else '❌'} | {', '.join(failed)}{extra} |")
    return "\n".join(lines)


def sample_sheet(run_id: str, n: int = 10, seed: int = 7) -> pathlib.Path:
    run = OUT / run_id
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(run.glob("*.json")) if not p.name.startswith("summary")]
    full = [r for r in recs if outcome_of(r.get("state", {})) == "full" and _claims(r["state"])]
    pick = random.Random(seed).sample(full, min(n, len(full)))
    path = pathlib.Path(__file__).parent / "grading_sheet.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "case_id", "claim_idx", "tag", "claim_text", "cites", "quote", "human_verdict (supported/partial/unsupported)", "notes"])
        for r in pick:
            for i, cl in enumerate(_claims(r["state"])):
                w.writerow([run_id, r["case_id"], i, cl.get("tag"), cl.get("text"), " ".join(cl.get("cites", [])), cl.get("quote") or "", "", ""])
    return path


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--sample-sheet":
        print("Wrote", sample_sheet(sys.argv[2]))
        return
    if len(sys.argv) < 2:
        print(__doc__)
        return
    import yaml
    run = OUT / sys.argv[1]
    cases = {c["id"]: c for c in yaml.safe_load((pathlib.Path(__file__).parent / "cases.yaml").read_text(encoding="utf-8"))["cases"]}
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(run.glob("*.json")) if not p.name.startswith("summary")]
    recs = [r for r in recs if r["case_id"] in cases]
    checks = [evaluate_case(cases[r["case_id"]], r) for r in recs]
    meta = {k: recs[0].get(k) for k in ("run_id", "provider", "models", "corpus_hash", "as_of")} if recs else {}
    print(markdown_report(meta, aggregate([cases[r["case_id"]] for r in recs], recs, checks), checks))


if __name__ == "__main__":
    main()
