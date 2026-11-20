"""
Three-layer verifier. A response never ships an unverified legal claim.

A (code)  every Law/Procedure claim cites a retrieved chunk; its quote is an exact substring
          of that chunk; numbers/durations in the claim appear in the cited text; no case law;
          no repealed-act citations; no words that don't fit the user's context; no blocked tactics.
B (LLM)   a verifier model from a different family judges each cited claim against ONLY the
          cited chunk text: supported / partially_supported / unsupported.
C         the retrieval gate runs earlier (retrieval.passes_retrieval_gate).

Failed claims trigger one regeneration with the problems fed back; anything still failing
is dropped and the user is told how many statements were removed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from rules import engine
from rules.validate import squash
from schemas import Advice, Claim, Verdicts

_CASE_LAW = [
    re.compile(r"\b[A-Z][\w.&]+(?:\s+[A-Z][\w.&]+){0,5}\s+(?:v\.|vs\.?|versus)\s+[A-Z]"),
    re.compile(r"\bAIR\s+\d{4}\b|\(\d{4}\)\s*\d+\s*SCC\b|\b\d{4}\s+SCC\s+(?:OnLine\s+)?\w+|\bSCR\b|\bMANU/"),
    re.compile(r"(?i)\b(supreme court|high court|apex court|ncdrc|national commission)\s+(has\s+)?(held|ruled|observed|decided)"),
    re.compile(r"(?i)\bin the (landmark )?case of\b|\bprecedent\b"),
]


@dataclass
class Report:
    passed: bool = True
    claim_issues: dict[int, list[str]] = field(default_factory=dict)
    global_issues: list[str] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)

    def all_issues(self) -> list[str]:
        out = list(self.global_issues)
        for i, iss in self.claim_issues.items():
            out += [f"claim {i}: {x}" for x in iss]
        return out


_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60, "ninety": 90, "seventh": 7, "do": 2, "teen": 3, "saat": 7}
_UNIT = r"(days?|din|months?|mahine|mahina|years?|saal|varsh|times|guna|lakh|crore|weeks?|hafte)"
_UNITS_NORM = {"din": "day", "mahine": "month", "mahina": "month", "saal": "year", "varsh": "year", "guna": "time", "hafte": "week"}


def _durations(text: str) -> set[tuple[int, str]]:
    """(number, unit) pairs, e.g. 'three years' / '3 saal' -> (3, 'year'). Used to catch wrong time limits."""
    out = set()
    for m in re.finditer(rf"(?i)\b(\d+|{'|'.join(_WORDNUM)})\s*(?:working\s+)?{_UNIT}\b", text):
        n = m.group(1).lower()
        num = int(n) if n.isdigit() else _WORDNUM[n]
        unit = _UNITS_NORM.get(m.group(2).lower(), m.group(2).lower().rstrip("s"))
        out.add((num, unit))
    return out


def check_claim(claim: Claim, chunks: dict[str, str], user_text: str = "") -> list[str]:
    """Layer A for one claim. `chunks` = chunk_id -> text of everything the model was shown."""
    issues = []
    if claim.tag == "Practical":
        if claim.cites and any(c not in chunks for c in claim.cites):
            issues.append("cites a source that was not provided")
        return issues
    if not claim.cites:
        return ["legal/procedure statement without a citation"]
    missing = [c for c in claim.cites if c not in chunks]
    if missing:
        return [f"cites {missing} which were not among the provided sources"]
    cited_text = " ".join(chunks[c] for c in claim.cites)
    if claim.tag == "Law":
        if not claim.quote:
            issues.append("Law claim without a supporting quote")
        elif not all(squash(part) in squash(cited_text) for part in re.split(r"\.\.\.|…", claim.quote) if part.strip()):
            issues.append(f"quote not found verbatim in {claim.cites}: {claim.quote[:80]!r}")
    # Durations/limits stated in a legal claim must be in the cited text (catches "3 years" vs "two years").
    src = _durations(cited_text)
    for num, unit in _durations(claim.text):
        if (num, unit) not in src:
            issues.append(f"states '{num} {unit}' which is not in the cited text")
    # Other numbers (amounts, percentages) must be in the cited text or be the user's own figures.
    for n in re.findall(r"\d+(?:,\d+)*", claim.text):
        d = n.replace(",", "")
        if d not in re.sub(r"[,\s]", "", cited_text) and d not in re.sub(r"[,\s]", "", user_text):
            issues.append(f"states the number {n} which is not in the cited text or your facts")
    return issues


def check_text(text: str, avoid: list[str], allow_legacy: bool = False) -> list[str]:
    issues = []
    for pat in _CASE_LAW:
        m = pat.search(text)
        if m:
            issues.append(f"case-law reference not allowed: {m.group(0)!r}")
    for act in ([] if allow_legacy else engine.legacy_mentions(text)):
        issues.append(f"mentions {act['name']} ({act['status']}); use the corpus act instead")
    for term in avoid:
        if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I):
            issues.append(f"uses '{term}', which does not fit this person's situation")
    t = engine.blocked_tactic(text)
    if t:
        issues.append(f"suggests a blocked tactic ({t['id']})")
    return issues


_B_SYSTEM = """You check an AI legal assistant's statements against the source text it cited.
For each numbered statement, decide using ONLY the given source text (no outside knowledge):
- supported: the source text says this, or it is a faithful plain-language paraphrase
- partially_supported: the gist is right but it adds a detail, condition or certainty the text does not contain
- unsupported: the source does not say this, or says something different
Be strict about numbers, time limits, who can do what, and words like "must", "will", "always".
Mark unsupported if the cited provision covers a narrower or different situation than the statement applies it to.
Return JSON: {"verdicts": [{"id": <n>, "verdict": "...", "reason": "<one short sentence>"}]}"""


def layer_b(claims: list[tuple[int, Claim]], chunks: dict[str, str], llm) -> list[dict]:
    if not claims:
        return []
    blocks = []
    for i, c in claims:
        src = "\n".join(f"[{cid}] {chunks[cid][:2500]}" for cid in c.cites if cid in chunks)
        blocks.append(f"### Statement {i}\n{c.text}\nSOURCE TEXT:\n{src}")
    out = llm.complete(_B_SYSTEM, [{"role": "user", "content": "\n\n".join(blocks)}], role="verifier",
                       schema=Verdicts, max_tokens=1500, temperature=0)
    return [v.model_dump() for v in out.verdicts]


def verify_advice(advice: Advice, chunks: dict[str, str], llm, avoid: list[str], user_text: str = "",
                  allow_legacy: bool = False) -> Report:
    """allow_legacy: the decoder must be able to say "they cite the 1986 Act, which is repealed"."""
    rep = Report()
    whole = advice.plain_summary + "\n" + "\n".join(c.text for c in advice.claims)
    rep.global_issues = check_text(whole, avoid, allow_legacy)
    for i, c in enumerate(advice.claims):
        iss = check_claim(c, chunks, user_text)
        if iss:
            rep.claim_issues[i] = iss
    to_b = [(i, c) for i, c in enumerate(advice.claims) if c.tag != "Practical" and i not in rep.claim_issues]
    try:
        rep.verdicts = layer_b(to_b, chunks, llm)
    except Exception as exc:  # verifier down => treat cited claims as unverified, never as verified
        rep.global_issues.append(f"verifier unavailable: {str(exc)[:120]}")
        for i, _ in to_b:
            rep.claim_issues.setdefault(i, []).append("not checked by verifier")
    for v in rep.verdicts:
        if v["verdict"] == "unsupported":
            rep.claim_issues.setdefault(v["id"], []).append(f"verifier: unsupported ({v['reason']})")
    rep.passed = not rep.claim_issues and not rep.global_issues
    return rep


def drop_failed(advice: Advice, rep: Report, avoid: list[str], allow_legacy: bool = False) -> tuple[Advice, int]:
    """Final fallback: remove failing claims and any sentence still containing a forbidden pattern."""
    kept = [c for i, c in enumerate(advice.claims) if i not in rep.claim_issues and not check_text(c.text, avoid, allow_legacy)]
    summary = advice.plain_summary
    if check_text(summary, avoid, allow_legacy):
        summary = " ".join(s for s in re.split(r"(?<=[.!?])\s+", summary) if not check_text(s, avoid, allow_legacy))
    dropped = len(advice.claims) - len(kept)
    return advice.model_copy(update={"claims": kept, "plain_summary": summary}), dropped


def as_feedback(rep: Report) -> str:
    return json.dumps(rep.all_issues()[:15], ensure_ascii=False)
