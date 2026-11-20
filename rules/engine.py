"""
Deterministic decisions: escalation, context, evidence, pathways, strength, deadlines,
span checks. Everything here is plain code over rules/*.yaml; no LLM calls.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import config
from rules import load, verified_only
from rules.limitation import compute as compute_limitation
from schemas import CaseFacts

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _kw_hit(text: str, kw: str) -> bool:
    if _DEVANAGARI.search(kw):
        return kw in text
    return re.search(r"(?<![\w])" + re.escape(kw.lower()) + r"(?![\w])", text.lower()) is not None


def crisis_check(text: str) -> bool:
    words = load("escalation")["crisis"]["keywords"]
    return any(_kw_hit(text, k) for lang in words.values() for k in lang)


def keyword_triggers(text: str) -> dict[str, list[str]]:
    """trigger id -> matched keywords/patterns. Code-side half of escalation."""
    hits: dict[str, list[str]] = {}
    for tid, rule in load("escalation")["triggers"].items():
        found = [k for lang in rule.get("keywords", {}).values() for k in lang if _kw_hit(text, k)]
        found += [p for p in rule.get("regex", []) if re.search(p, text, re.I)]
        if found:
            hits[tid] = found
    return hits


def escalation_triggers(text: str, facts: Optional[CaseFacts]) -> dict[str, list[str]]:
    """Union of keyword hits and LLM fact flags; the LLM can add but never remove."""
    hits = keyword_triggers(text)
    if facts is not None:
        for tid, rule in load("escalation")["triggers"].items():
            for flag in rule.get("flags", []):
                if getattr(facts, flag, False):
                    hits.setdefault(tid, []).append(f"llm:{flag}")
        if facts.amount and facts.amount > config.CLAIM_ESCALATION_THRESHOLD:
            hits.setdefault("amount", []).append(f"amount {facts.amount:,.0f} > {config.CLAIM_ESCALATION_THRESHOLD:,.0f}")
    return hits


def escalation_message(hits: dict[str, list[str]]) -> dict:
    rules = load("escalation")
    order = ["safety", "criminal", "court", "cheque_bounce", "family_property", "state_rent", "amount", "out_of_corpus"]
    keys = [k for k in order if k in hits] or ["out_of_corpus"]
    steps = list(rules["safe_steps"])
    for k in keys:
        steps = rules["extra_steps"].get(k, []) + steps
    return {"kinds": keys, "reasons": [rules["messages"][k] for k in keys], "safe_steps": steps,
            "matched": {k: hits.get(k, []) for k in keys}}


def blocked_tactic(text: str) -> Optional[dict]:
    for t in load("playbook")["blocked_tactics"]:
        if re.search(t["pattern"], text):
            return t
    return None


# ── Span checks: the LLM may only report values that appear in the source text ──
def _squash(s: str) -> str:
    return re.sub(r"[\s,₹.]+", "", s).lower()


def span_ok(snippet: Optional[str], source: str) -> bool:
    return bool(snippet) and _squash(snippet) in _squash(source)


_HINDI_NUM = {"hazaar": 1_000, "hazar": 1_000, "हजार": 1_000, "हज़ार": 1_000, "lakh": 100_000, "lac": 100_000, "लाख": 100_000, "k": 1_000}


def amount_from_text(amount_text: Optional[str]) -> Optional[float]:
    """Parse '15,000', '₹12k', '12 hazaar', '1.5 lakh' deterministically."""
    if not amount_text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)*)\s*([a-zA-Zऀ-ॿ]+)?", amount_text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = _HINDI_NUM.get((m.group(2) or "").lower(), 1)
    return num * mult


def checked_facts(facts: CaseFacts, user_text: str) -> tuple[CaseFacts, list[str]]:
    """Drop amounts/dates the user never said. Returns cleaned facts + notes for the run log."""
    notes = []
    f = facts.model_copy(deep=True)
    if f.amount is not None:
        parsed = amount_from_text(f.amount_text) if span_ok(f.amount_text, user_text) else None
        if parsed is None or abs(parsed - f.amount) > 0.5:
            notes.append(f"amount {f.amount} dropped: {f.amount_text!r} not found verbatim / mismatch")
            f.amount = None
    if f.due_date and not span_ok(f.due_date_text, user_text):
        notes.append(f"due_date {f.due_date} dropped: {f.due_date_text!r} not in user text")
        f.due_date = None
    for e in f.events:
        if e.date and not span_ok(e.date_text, user_text):
            notes.append(f"event date {e.date} dropped: {e.date_text!r} not in user text")
            e.date = None
    f.events.sort(key=lambda e: e.date or "9999")
    return f, notes


# ── Context, evidence, pathways ──
def choose_context(facts: CaseFacts) -> Optional[dict]:
    for ctx in load("routing")["contexts"]:
        if ctx["category"] != facts.category:
            continue
        if all(getattr(facts, k, None) in allowed for k, allowed in ctx["when"].items()):
            return ctx
    return None


def evidence_for(category: str, context_id: Optional[str], have: list[str]) -> list[dict]:
    ev = load("routing")["evidence"].get(category, {})
    items = list(ev.get("common", [])) + list(ev.get(context_id or "", []))
    have_l = " ".join(have).lower()
    out = []
    for it in items:
        words = [w for w in re.findall(r"[a-z]{4,}", it["item"].lower()) if w not in {"with", "your", "from", "that", "proof", "messages"}]
        status = "have" if words and sum(w in have_l for w in words) >= 2 else "need"
        out.append({**it, "status": status})
    return out


def pathways_for(category: str, context_id: Optional[str]) -> list[dict]:
    costs = load("costs")
    ps = [p for p in verified_only(costs["pathways"]) if category in p["categories"]
          and (p["contexts"] == "all" or (context_id in p["contexts"]))]
    order = costs.get("order", {}).get(context_id or "", [])
    return sorted(ps, key=lambda p: order.index(p["id"]) if p["id"] in order else 100)


def avoid_terms(context_id: Optional[str]) -> list[str]:
    return load("routing")["avoid_terms"].get(context_id or "", [])


def coverage_notes(context_id: Optional[str]) -> list[dict]:
    return load("routing")["coverage_notes"].get(context_id or "", [])


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def deadline_for(facts: CaseFacts, context_id: Optional[str], today: Optional[date] = None):
    trigger = _parse_date(facts.due_date) or next((d for d in (_parse_date(e.date) for e in facts.events) if d), None)
    if not trigger or facts.category not in ("consumer", "wages", "contract_payment"):
        return None
    return compute_limitation(facts.category, trigger, context_id, today)


def strength(facts: CaseFacts, limitation) -> dict:
    """Band from booleans; unknown (None) inputs are counted as unknown, never as true."""
    cfg = load("routing")["strength"]
    vals = {k: getattr(facts, k, None) for k in cfg["inputs"] if k != "within_limitation"}
    vals["within_limitation"] = None if limitation is None else limitation.status != "expired"
    known = sum(v is not None for v in vals.values())
    true = sum(v is True for v in vals.values())
    env = {"known": known, "true": true, "within_limitation": vals["within_limitation"] is not False}
    band = next(b["band"] for b in cfg["bands"] if eval(b["when"], {"__builtins__": {}}, env))  # trusted rules file
    return {"band": band, "inputs": vals,
            "for": [cfg["explain"][k] for k, v in vals.items() if v is True],
            "against": [cfg["explain"][k] for k, v in vals.items() if v is False],
            "unknown": [cfg["explain"][k] for k, v in vals.items() if v is None]}


def legacy_mentions(text: str) -> list[dict]:
    return [a for a in load("legacy_refs")["legacy_acts"] if re.search(a["pattern"], text, re.I)]


_HINGLISH = {"hai", "nahi", "nahin", "mera", "meri", "maine", "mujhe", "kya", "kar", "diya", "liya", "ho", "gaya", "tha", "thi", "ka", "ki", "ke", "ko", "se", "aur", "bhi", "paisa", "paise", "kaam", "abhi", "wapas"}


def detect_language(text: str) -> str:
    if len(_DEVANAGARI.findall(text)) > 10:
        return "hi"
    words = re.findall(r"[a-z]+", text.lower())
    return "hinglish" if words and sum(w in _HINGLISH for w in words) / len(words) > 0.12 else "en"
