"""
Notice Decoder nodes. The LLM extracts (with verbatim snippets) and explains; code checks
every snippet against the notice, triages by rules, looks up cited provisions, and does
all date arithmetic.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import config
from llm import get_llm
from retrieval import get_index
from rules import engine, load
from schemas import Advice, NoticeFacts
from verify import as_feedback, drop_failed, verify_advice

_EXTRACT = """You read a legal notice or demand letter received by an ordinary person in India and extract its contents.
For EVERY field give `value` and `snippet`, where snippet is copied EXACTLY, word for word, from the notice.
If a field is absent, set both to null. Do not interpret or add anything.
- sender_type: court or police only if the document itself is issued by a court/police; a lawyer's letter = advocate_or_party.
- provisions: every Act/section the notice cites, with the exact words where it is cited.
- subject: cheque_bounce if it concerns a dishonoured cheque / Section 138; eviction_rent for rent/eviction/deposit;
  criminal if it is an FIR, police notice or criminal complaint; court_summons if it is a summons from a court.
- plain_summary: two plain sentences on what the notice wants."""


def extract(state: dict) -> dict:
    nf = get_llm().complete(_EXTRACT, [{"role": "user", "content": state["notice_text"][:12000]}],
                            role="generator", schema=NoticeFacts, max_tokens=2000)
    return {"notice": nf.model_dump()}


def span_check(state: dict) -> dict:
    text = state["notice_text"]
    n = NoticeFacts(**state["notice"])
    issues = []
    for name in ("sender", "recipient", "notice_date", "demand_amount", "demand_action", "deadline"):
        s = getattr(n, name)
        if s.value and not engine.span_ok(s.snippet, text):
            issues.append(f"{name}: snippet not in notice; dropped ({s.snippet!r})")
            setattr(n, name, type(s)())
        elif s.value and name == "demand_amount" and re.sub(r"\D", "", s.value) not in re.sub(r"\D", "", s.snippet or ""):
            issues.append(f"demand_amount: value {s.value!r} differs from snippet; using snippet only")
            s.value = s.snippet
    if n.deadline_days is not None and str(n.deadline_days) not in (n.deadline.snippet or "") \
            and _num_word(n.deadline_days) not in (n.deadline.snippet or "").lower():
        issues.append(f"deadline_days {n.deadline_days} not stated in deadline snippet; dropped")
        n.deadline_days = None
    n.threatened_consequences = [t for t in n.threatened_consequences if engine.span_ok(t.snippet, text)]
    kept = [p for p in n.provisions if engine.span_ok(p.snippet, text)]
    issues += [f"provision {p.act} {p.section}: snippet not in notice; dropped" for p in n.provisions if p not in kept]
    n.provisions = kept
    return {"notice": n.model_dump(), "span_issues": issues}


def _num_word(n: int) -> str:
    words = {7: "seven", 10: "ten", 14: "fourteen", 15: "fifteen", 21: "twenty-one", 30: "thirty", 60: "sixty"}
    return words.get(n, "\x00")


def triage(state: dict) -> dict:
    n = NoticeFacts(**state["notice"])
    text = state["notice_text"]
    hits: dict[str, list[str]] = {}
    if n.sender_type in ("court", "police") or n.subject in ("court_summons", "criminal"):
        hits["court" if n.sender_type == "court" or n.subject == "court_summons" else "criminal"] = [f"{n.sender_type}/{n.subject}"]
    if n.subject == "cheque_bounce" or "cheque_bounce" in engine.keyword_triggers(text):
        hits["cheque_bounce"] = ["cheque"]
    if n.subject == "eviction_rent":
        hits["state_rent"] = ["rent/eviction"]
    if n.subject == "property_family":
        hits["family_property"] = ["property/family"]
    if hits:
        return {"outcome": "escalate", "triage": {"cleared": False, **engine.escalation_message(hits)}}
    return {"outcome": None, "triage": {"cleared": True}}


def _act_code(act: str) -> str | None:
    a = act.lower()
    table = {"consumer protection act": "CPA2019", "contract act": "ICA1872", "limitation act": "LA1963",
             "code on wages": "COW2019", "legal services authorities": "LSAA1987"}
    for key, code in table.items():
        if key in a:
            return code if not (code == "CPA2019" and "1986" in a) else None
    return None


def provisions(state: dict) -> dict:
    n = NoticeFacts(**state["notice"])
    idx = get_index()
    checks, sources = [], {}
    for p in n.provisions:
        legacy = engine.legacy_mentions(f"{p.act} {p.snippet}")
        code = _act_code(p.act)
        if legacy:
            a = legacy[0]
            status = "repealed" if a["status"] == "repealed" else "not_in_corpus"
            checks.append({"cited": p.snippet, "status": status, "detail": a.get("now") or a.get("note"), "ref": a.get("ref")})
            if a.get("ref"):
                ch = idx.get_chunk(a["ref"])
                sources[a["ref"]] = _src(ch)
        elif code is None:
            checks.append({"cited": p.snippet, "status": "cannot_verify", "detail": "This Act is not in the five central laws I hold."})
        else:
            m = re.search(r"\d+[A-Z]?", p.section or "")   # "Section 73(1)" -> "73"
            sec = m.group(0) if m else ""
            cid = f"{code}:s{sec}" if sec else None
            ch = idx.get_chunk(cid) if cid else None
            if ch:
                sources[cid] = _src(ch)
                checks.append({"cited": p.snippet, "status": "in_corpus", "ref": cid, "heading": ch.heading})
            else:
                checks.append({"cited": p.snippet, "status": "not_found_in_snapshot",
                               "detail": f"I could not find section {p.section} of this Act in my copy. That does not prove it doesn't exist; my copy is a snapshot."})
    for r in idx.search(f"{n.subject.replace('_', ' ')} {n.plain_summary}", k=4):
        if r.vector_score >= config.RETRIEVAL_GATE_THRESHOLD and r.chunk.chunk_id not in sources:
            sources[r.chunk.chunk_id] = _src(r.chunk)
    return {"provisions": checks, "sources": sources}


def _src(ch) -> dict:
    return {"act": ch.act, "heading": ch.heading, "text": ch.text[: config.CHUNK_PROMPT_CHARS], "full_text": ch.text,
            "url": ch.source_url, "tag": ch.tag}


_EXPLAIN = """You explain a legal notice to a worried person in India with no legal experience. Calm, plain, honest.
Return JSON (schema given). plain_summary: 2-3 sentences on what the notice is and what they want.
claims (4-8), using section:
- where_you_stand: what the sender says the law is, and whether the provided SOURCES actually support that. If a cited
  provision is in SOURCES, compare what it really says with how the notice uses it. Tag "Law" + exact quote + cite.
- law: other relevant statements from SOURCES (tag Law, exact quote, cite).
- next_steps: practical steps (tag Practical): don't panic, note the date you RECEIVED it (not the notice's own date),
  gather documents, consider free legal aid. If a cited Act is repealed per PROVISION CHECKS, say so plainly.
Never decide who is right, never promise outcomes, no case law, no deadlines/day counts (the app computes them),
nothing that is not in SOURCES. Put anything you cannot judge in cannot_assess."""


def explain(state: dict, feedback: str | None = None) -> dict:
    n = NoticeFacts(**state["notice"])
    srcs = "\n\n".join(f"[{cid}] {s['act']} — {s['heading']}\n{s['text']}" for cid, s in state["sources"].items())
    content = (f"NOTICE (extracted): {n.model_dump_json(exclude_none=True)}\n\nPROVISION CHECKS (by code): {state['provisions']}\n\n"
               f"SOURCES:\n{srcs or '(none)'}")
    if feedback:
        content += f"\n\nYOUR PREVIOUS DRAFT FAILED THESE CHECKS; fix or drop those claims:\n{feedback}"
    adv = get_llm().complete(_EXPLAIN, [{"role": "user", "content": content}], role="generator", schema=Advice, max_tokens=2200)
    return {"explanation": adv.model_dump()}


def verify_node(state: dict) -> dict:
    chunks = {cid: s["text"] for cid, s in state["sources"].items()}
    llm = get_llm()
    adv = Advice(**state["explanation"])
    rep = verify_advice(adv, chunks, llm, [], state["notice_text"], allow_legacy=True)
    log = [{"attempt": 1, "passed": rep.passed, "issues": rep.all_issues(), "verdicts": rep.verdicts}]
    if not rep.passed:
        adv = Advice(**explain(state, feedback=as_feedback(rep))["explanation"])
        rep = verify_advice(adv, chunks, llm, [], state["notice_text"], allow_legacy=True)
        log.append({"attempt": 2, "passed": rep.passed, "issues": rep.all_issues(), "verdicts": rep.verdicts})
    dropped = 0
    if not rep.passed:
        adv, dropped = drop_failed(adv, rep, [], allow_legacy=True)
    return {"explanation": adv.model_dump(), "verifier_log": log, "dropped_claims": dropped}


def deadline_status(deadline_days: int | None, received: str | None, today: date | None = None) -> dict:
    """Code-only arithmetic: received date + N days stated in the notice."""
    if deadline_days is None:
        return {"status": "no_deadline_found"}
    if not received:
        return {"status": "need_received_date", "days": deadline_days}
    rec = datetime.strptime(received, "%Y-%m-%d").date()
    due = rec + timedelta(days=deadline_days)
    left = (due - (today or date.today())).days
    return {"status": "expired" if left < 0 else "running", "days": deadline_days, "due": str(due), "days_left": left}


def assemble(state: dict) -> dict:
    n = NoticeFacts(**state["notice"])
    pb = load("playbook")
    kind = pb["notice_kinds"].get(n.sender_type, pb["notice_kinds"]["advocate_or_party"])
    return {"result": {
        "kind": kind,
        "deadline": deadline_status(n.deadline_days, state.get("received_date")),
        "options": pb["notice_options"],
        "reply_warning": pb["reply_warning"],
        "threats": [t.snippet for t in n.threatened_consequences],
        "gather": ["The notice and its envelope / courier slip (proves when it reached you)",
                   "Any agreement, bill, invoice or messages the notice refers to",
                   "Proof of any payments you already made",
                   "A note of the date you received it and how (post, courier, WhatsApp, email)"],
    }}
