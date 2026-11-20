"""
Advisor nodes. Each node is a function state -> partial state update.
LLM: intake (extract facts), transform (search queries), advise (draft claims), verifier.
Code: everything else (screening, scope, context, evidence, pathways, deadlines, strength).
"""
from __future__ import annotations

import logging
from datetime import date

import config
from llm import get_llm
from retrieval import get_index, passes_retrieval_gate
from rules import engine, load
from schemas import Advice, CaseFacts, Queries
from verify import as_feedback, drop_failed, verify_advice

log = logging.getLogger(__name__)


def user_text(state: dict) -> str:
    return "\n".join(m["content"] for m in state.get("messages", []) if m["role"] == "user")


# ── 1. Safety screen (code) ──────────────────────────────────────
def screen(state: dict) -> dict:
    text = user_text(state)
    if engine.crisis_check(text):
        return {"outcome": "crisis", "crisis_message": load("escalation")["crisis"]["message"]}
    tactic = engine.blocked_tactic(text)
    return {"blocked": tactic["message"] if tactic else None, "outcome": None}


# ── 2. Intake (LLM, then code span-checks) ───────────────────────
_INTAKE = """You read a person's description of an everyday legal problem in India (English, Hindi or Hinglish)
and fill a case file. Rules:
- Copy facts only. Never guess amounts, dates or names: use null when not stated.
- amount_text, due_date_text and each date_text must be the person's EXACT words copied from the message.
- employer_type: a family or home employing a maid, cook, driver, nanny, caretaker or cleaner = "household";
  a shop or dhaba = "shop"; a small firm/contractor/site = "small_business" or "contractor"; delivery/ride apps = "platform".
- has_written_terms: true only if they mention an offer letter, contract or written agreement; false if they say there is
  none ("koi paper nahi", "kuch likha nahi", "no contract", "verbal").
- due_date: when the money became due or the problem started. "since August 2026" / "August se" -> "2026-08-01"
  with due_date_text "August 2026" (the exact words). A month without a year: use the most recent such month before today ({today}).
- summary: written in the second person ("You ...").
- Strength booleans: true/false only if the message says so, else null. other_side_acknowledged = true if the other side
  promised to pay later or admitted owing (even verbally, e.g. "next month de dungi", "abhi paise nahi hain").
  prior_demand_made = true if the person already asked for the money.
- Flags: set true if the message involves police/arrest, an accusation of crime against the person, violence or threats,
  family/inheritance/property, a court summons or filed case, a bounced cheque, or rent/landlord/security deposit.
- distress: true only if they express hopelessness or self-harm.
- category: consumer (bought goods/services), wages (pay for work as an employee/worker), contract_payment (money owed
  under an agreement, freelance work, a loan to someone), notice_received (they got a legal notice), out_of_scope, or unclear.
- If the person also asks for court cases/precedents or a guaranteed result, still categorise their underlying problem;
  the app explains separately that it does not cite case law. Use out_of_scope only when no in-scope problem is described.
- missing_questions: at most 2 short, kind questions whose answers would change the advice (e.g. when the pay stopped,
  how much is owed). Ask nothing if the facts are enough."""


def intake(state: dict) -> dict:
    text = user_text(state)
    facts = get_llm().complete(_INTAKE.replace("{today}", date.today().isoformat()), [{"role": "user", "content": text}], role="fast", schema=CaseFacts, max_tokens=1800)
    facts, notes = engine.checked_facts(facts, text)
    return {"facts": facts.model_dump(), "audit": state.get("audit", []) + [{"step": "intake_span_check", "notes": notes}]}


# ── 3. Triage (code): escalation, scope, clarification, context ──
def triage(state: dict) -> dict:
    facts = CaseFacts(**state["facts"])
    hits = engine.escalation_triggers(user_text(state), facts)
    if hits:
        return {"outcome": "escalate", "escalation": engine.escalation_message(hits)}
    if facts.category == "notice_received":
        return {"outcome": "use_decoder"}
    if facts.category == "out_of_scope":
        return {"outcome": "escalate", "escalation": engine.escalation_message({"out_of_corpus": [facts.category_reason]})}
    rounds = state.get("clarify_rounds", 0)
    if (facts.category == "unclear" or facts.missing_questions) and rounds < config.MAX_CLARIFY_ROUNDS:
        qs = facts.missing_questions[:2] or ["Could you tell me a little more: what happened, with whom, and what you want now?"]
        return {"outcome": "clarify", "questions": qs, "clarify_rounds": rounds + 1}
    if facts.category == "unclear":
        return {"outcome": "escalate", "escalation": engine.escalation_message({"out_of_corpus": ["unclear"]})}
    ctx = engine.choose_context(facts)
    # LLM sensed distress without explicit crisis words: keep helping, but show support resources first.
    return {"outcome": None, "context": ctx, "distress": facts.distress, "lang": engine.detect_language(user_text(state))}


# ── 4. Retrieve (LLM query rewrite + hybrid search + gate) ───────
_TRANSFORM = """Rewrite the person's problem as 2-3 short English search queries for the text of Indian statutes
(Consumer Protection Act 2019, Indian Contract Act 1872, Limitation Act 1963, Code on Wages 2019, Legal Services
Authorities Act 1987). Use statutory vocabulary (e.g. "time limit for payment of wages", "deficiency in service",
"compensation for breach of contract", "criteria for giving legal services"). No section numbers."""


def retrieve(state: dict) -> dict:
    facts = CaseFacts(**state["facts"])
    ctx = state.get("context") or {}
    prompt = f"Category: {facts.category}. Situation: {ctx.get('label', '')}. Problem: {facts.summary}\nUser's words: {user_text(state)[:1500]}"
    qs = get_llm().complete(_TRANSFORM, [{"role": "user", "content": prompt}], role="fast", schema=Queries, max_tokens=400).queries[:3]
    idx = get_index()
    best: dict[str, object] = {}
    for q in qs:
        for r in idx.search(q, k=config.RETRIEVAL_K):
            if r.chunk.chunk_id not in best or r.vector_score > best[r.chunk.chunk_id].vector_score:
                best[r.chunk.chunk_id] = r
    results = sorted(best.values(), key=lambda r: r.vector_score, reverse=True)[: config.RETRIEVAL_K + 2]
    passed = passes_retrieval_gate(results)
    # Rules-anchored sources: the chunks our own pathways/deadlines/coverage notes cite are always shown.
    anchors = _anchor_refs(facts.category, ctx.get("id"))
    ids = [r.chunk.chunk_id for r in results] + [a for a in anchors if a not in best]
    sources = {}
    for cid in ids:
        ch = idx.get_chunk(cid)
        if ch:
            sources[cid] = {"act": ch.act, "heading": ch.heading, "text": ch.text[: config.CHUNK_PROMPT_CHARS],
                            "full_text": ch.text, "url": ch.source_url, "tag": ch.tag}
    top = round(max((r.vector_score for r in results), default=0.0), 3)
    return {"queries": qs, "sources": sources, "gate": {"passed": passed, "top_similarity": top,
            "threshold": config.RETRIEVAL_GATE_THRESHOLD}, "outcome": None if passed else "ungrounded"}


def _anchor_refs(category: str, ctx_id: str | None) -> list[str]:
    refs = []
    for p in engine.pathways_for(category, ctx_id):
        if p.get("law"):
            refs.append(p["law"]["ref"])
    refs += [n["ref"] for n in engine.coverage_notes(ctx_id) if n.get("ref")]
    from rules.limitation import periods_for
    refs += [p["ref"] for p in periods_for(category, ctx_id)[:1]]
    return list(dict.fromkeys(refs))


# ── 5. Advise (LLM drafts claims) ────────────────────────────────
_ADVISE = """You help an ordinary, worried person in India understand where they stand. You are calm, plain and honest.
Speak directly TO the person who wrote the message ("you"/"aap"). They are the one who is owed money / was wronged
(the worker, buyer or lender), never the other side. Never refer to them in the third person.
Write for someone with no legal experience. Never promise outcomes or compensation amounts.

You get: the case facts, their situation, the options our rules already selected, and SOURCES (statute text with IDs).
Return JSON matching the schema:
- plain_summary: 2-3 short sentences in the person's language style ({lang}); honest bottom line, including if the case looks weak.
- claims: 4-9 statements, each with section (where_you_stand | law | next_steps) and tag:
  * "Law": something a statute says. MUST cite the source ID(s) and give `quote` = ONE contiguous phrase (5-25 words)
    copied EXACTLY from that source, no "..." and no added words.
  * "Official procedure": how an official body/process works per a PROC source; cite it.
  * "Practical": common-sense advice (gather proof, write a demand). No citation needed; never present as law.
- Only use the SOURCES. If they don't cover something, say so in cannot_assess. No case law, no court decisions,
  no section numbers or Acts that are not in SOURCES, no fees, no deadlines or day-counts (the app computes those).
- Fit the person's situation: {ctx_label}. Do NOT use these words: {avoid}.
- next_steps: the first 2-3 concrete things to do this week, drawn from the OPTIONS list.
- lawyer_needed_when: specific triggers for this case."""


def _sources_block(sources: dict) -> str:
    return "\n\n".join(f"[{cid}] {s['act']} — {s['heading']}\n{s['text']}" for cid, s in sources.items())


def advise(state: dict, feedback: str | None = None) -> dict:
    facts = CaseFacts(**state["facts"])
    ctx = state.get("context") or {}
    avoid = engine.avoid_terms(ctx.get("id"))
    options = "; ".join(p["name"] for p in engine.pathways_for(facts.category, ctx.get("id")))
    lang = "Hinglish in Roman script" if state.get("lang") == "hinglish" else ("simple Hindi" if state.get("lang") == "hi" else "simple English")
    system = _ADVISE.format(lang=lang, ctx_label=ctx.get("label", "unknown"), avoid=", ".join(avoid) or "none")
    facts_json = facts.model_dump_json(exclude_none=True, exclude={"missing_questions"})
    content = f"CASE FACTS: {facts_json}\n\nOPTIONS (from our rules): {options}\n\nSOURCES:\n{_sources_block(state['sources'])}"
    if feedback:
        content += f"\n\nYOUR PREVIOUS DRAFT FAILED THESE CHECKS. Fix them; drop any claim you cannot support with an exact quote:\n{feedback}"
    advice = get_llm().complete(system, [{"role": "user", "content": content}], role="generator", schema=Advice, max_tokens=4000)
    return {"advice": advice.model_dump()}


# ── 6. Verify (A + B; regenerate once; then drop) ────────────────
def verify_node(state: dict) -> dict:
    ctx = state.get("context") or {}
    avoid = engine.avoid_terms(ctx.get("id"))
    chunks = {cid: s["text"] for cid, s in state["sources"].items()}
    llm = get_llm()
    advice = Advice(**state["advice"])
    rep = verify_advice(advice, chunks, llm, avoid, user_text(state))
    vlog = [{"attempt": 1, "passed": rep.passed, "issues": rep.all_issues(), "verdicts": rep.verdicts}]
    if not rep.passed:
        advice = Advice(**advise(state, feedback=as_feedback(rep))["advice"])
        rep = verify_advice(advice, chunks, llm, avoid, user_text(state))
        vlog.append({"attempt": 2, "passed": rep.passed, "issues": rep.all_issues(), "verdicts": rep.verdicts})
    dropped = 0
    if not rep.passed:
        advice, dropped = drop_failed(advice, rep, avoid)
    partial = {v["id"] for v in rep.verdicts if v["verdict"] == "partially_supported"}
    return {"advice": advice.model_dump(), "verifier_log": vlog, "dropped_claims": dropped,
            "partial_claims": sorted(partial)}


# ── 7. Assemble (code): case file, evidence, options, deadline, strength ──
def assemble(state: dict) -> dict:
    facts = CaseFacts(**state["facts"])
    ctx = state.get("context") or {}
    lim = engine.deadline_for(facts, ctx.get("id"))
    return {"result": {
        "context": ctx,
        "evidence": engine.evidence_for(facts.category, ctx.get("id"), facts.evidence_have),
        "pathways": engine.pathways_for(facts.category, ctx.get("id")),
        "coverage_notes": engine.coverage_notes(ctx.get("id")),
        "limitation": None if lim is None else lim.__dict__ | {"deadline": str(lim.deadline), "trigger_date": str(lim.trigger_date)},
        "strength": engine.strength(facts, lim),
        "drafts": ["demand_letter"] + (["consumer_complaint"] if facts.category == "consumer" else []) + ["legal_aid_application"],
        "wages_state_rules_note": facts.category == "wages",
    }}
