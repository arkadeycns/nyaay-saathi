"""
Deterministic limitation arithmetic. Periods come only from rules/limitation.yaml,
where each row quotes the corpus. The LLM never computes or states a deadline.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Optional

from rules import load


@dataclass(frozen=True)
class LimitationResult:
    period_id: str
    ref: str
    quote: str
    trigger: str
    trigger_date: date
    deadline: date
    days_left: int
    status: str          # ok | soon | urgent | expired
    message: str


def add_years(d: date, years: int) -> date:
    """d + years, mapping 29 Feb to 28 Feb in non-leap target years."""
    y = d.year + years
    day = min(d.day, calendar.monthrange(y, d.month)[1])
    return d.replace(year=y, day=day)


def periods_for(category: str, context: str | None = None) -> list[dict]:
    rows = [p for p in load("limitation")["periods"] if category in p["categories"]]
    specific = [p for p in rows if context and context in p.get("contexts", [])]
    general = [p for p in rows if not p.get("contexts")]
    return specific + general


def compute(category: str, trigger_date: date, context: str | None = None,
            today: date | None = None) -> Optional[LimitationResult]:
    rows = periods_for(category, context)
    if not rows:
        return None
    p = rows[0]
    today = today or date.today()
    deadline = add_years(trigger_date, p["years"])
    days_left = (deadline - today).days
    warn = load("limitation")["warn_days"]
    if days_left < 0:
        status = "expired"
        msg = (f"On the date you gave, the {p['years']}-year period appears to have ended {abs(days_left)} days ago "
               f"({deadline:%d %b %Y}). Late filing is sometimes allowed for sufficient cause, but you should "
               f"speak to an advocate or the DLSA quickly.")
    elif days_left <= warn["urgent"]:
        status, msg = "urgent", f"Only {days_left} days left (deadline {deadline:%d %b %Y}). Get help now."
    elif days_left <= warn["soon"]:
        status, msg = "soon", f"{days_left} days left (deadline {deadline:%d %b %Y}). Start soon."
    else:
        status, msg = "ok", f"About {days_left // 30} months left (deadline {deadline:%d %b %Y})."
    return LimitationResult(p["id"], p["ref"], p["quote"], p["trigger"], trigger_date, deadline, days_left, status, msg)


def days_until(deadline: date, today: date | None = None) -> int:
    return (deadline - (today or date.today())).days
