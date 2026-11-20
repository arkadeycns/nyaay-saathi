"""
Opponent Simulator. Code picks bluffs from rules/bluffs.yaml; the LLM only re-voices the
chosen bluff line in the persona's tone (it may not add threats, laws or numbers, and its
line is checked). Coaching is assembled from the library's verified 'truth' lines.
"""
from __future__ import annotations

import re

from llm import get_llm
from rules import engine, load, verified_only
from schemas import SimTurn
from verify import check_text

_VOICE = """You role-play the OTHER SIDE in a practice conversation so an ordinary person in India can rehearse.
Persona: {persona}
Say this point, in your own words and natural tone (Hinglish if the person writes Hinglish), 1-3 sentences:
"{bluff}"
Rules: do not add any new threat, law, section, amount or deadline that is not in that point. No abuse, no slurs,
no intimidation beyond the point itself. If the person's last reply is reasonable, you may acknowledge it briefly
before making the point."""

_REFUSE = re.compile(r"(?i)(threaten|dhamki|scare|darana|harass|blackmail|beat|maar|expose (him|her)|"
                     r"fake (fir|complaint)|jhooth?i (fir|complaint)|abuse)")


def refuse_reason(user_reply: str) -> str | None:
    """We coach calm, lawful replies only."""
    if _REFUSE.search(user_reply or "") or engine.blocked_tactic(user_reply or ""):
        return ("I can't help practise threats, harassment or false complaints. They can backfire and expose you to "
                "legal risk. Let's practise a calm, factual reply instead.")
    return None


def start(category: str, context_id: str | None) -> dict:
    lib = verified_only(load("bluffs")["bluffs"])
    picks = [b for b in lib if b["category"] == category and (b["contexts"] == "all" or context_id in b["contexts"])]
    return {"category": category, "context_id": context_id, "bluffs": picks[:4], "turns": [], "index": 0, "done": not picks}


def _voice(bluff: dict, last_user: str | None) -> str:
    prompt = f"Person's last reply: {last_user}" if last_user else "Start the conversation."
    try:
        line = get_llm().complete(_VOICE.format(persona=bluff["persona"], bluff=bluff["bluff"]),
                                  [{"role": "user", "content": prompt}], role="fast", schema=SimTurn,
                                  max_tokens=600, temperature=0.7).line
    except Exception:  # rate limit etc.: fall back to the library line verbatim
        return bluff["bluff"]
    # The voiced line may not introduce numbers, legal references or case law absent from the bluff.
    new_nums = set(re.findall(r"\d+", line)) - set(re.findall(r"\d+", bluff["bluff"]))
    if new_nums or check_text(line, []) or re.search(r"(?i)\b(section|act|dhara|ipc|bns)\b", line):
        return bluff["bluff"]
    return line


def opponent_turn(session: dict, user_reply: str | None) -> dict:
    s = dict(session)
    s["turns"] = list(session["turns"])
    if user_reply:
        s["turns"].append({"role": "user", "text": user_reply})
    if s["index"] >= len(s["bluffs"]):
        s["done"] = True
        return s
    b = s["bluffs"][s["index"]]
    s["turns"].append({"role": "opponent", "bluff_id": b["id"], "text": _voice(b, user_reply),
                       "escalate": bool(b.get("escalate"))})
    s["index"] += 1
    return s


def coaching(session: dict) -> dict:
    used = [t["bluff_id"] for t in session["turns"] if t["role"] == "opponent"]
    lib = {b["id"]: b for b in session["bluffs"]}
    replies = [t["text"] for t in session["turns"] if t["role"] == "user"]
    tips = []
    if replies and not any(re.search(r"\d", r) for r in replies):
        tips.append("Use specifics: dates, amounts and what was agreed make a reply harder to brush off.")
    if any(len(r) > 400 for r in replies):
        tips.append("Shorter is stronger. Two or three factual sentences are enough.")
    if any(re.search(r"(?i)sorry|maaf", r) for r in replies):
        tips.append("Avoid apologising for things you did not do; it can read as an admission.")
    tips.append("Keep everything in writing and save screenshots after each exchange.")
    return {
        "summary": " ".join(tips),
        "per_bluff": [{"bluff_id": i, "bluff": lib[i]["bluff"], "truth": lib[i]["truth"],
                       "suggested_reply": lib[i]["suggested_reply"], "verified": lib[i].get("verified", True)} for i in used if i in lib],
        # Truth lines are validator-checked quotes (rules/validate.py); tips are code-written Practical text.
        "verified": all(lib[i].get("verified", True) for i in used if i in lib),
    }
