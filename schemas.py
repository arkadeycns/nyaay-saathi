"""Typed data passed between the LLM and the deterministic code."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Tag = Literal["Law", "Official procedure", "Practical"]


# ── Intake (LLM extracts; code checks every value against the user's words) ──
class Event(BaseModel):
    date: Optional[str] = Field(None, description="YYYY-MM-DD if the user gave a day, YYYY-MM-01 if only a month, else null")
    date_text: Optional[str] = Field(None, description="the user's exact words for the date, copied verbatim")
    event: str


class CaseFacts(BaseModel):
    summary: str = Field(description="one plain-English sentence describing the problem")
    category: Literal["consumer", "wages", "contract_payment", "notice_received", "out_of_scope", "unclear"]
    category_reason: str = ""
    user_goal: Optional[str] = Field(None, description="what the user wants: refund, unpaid wages, money back, understand a notice...")
    other_party_name: Optional[str] = None
    other_party_type: Optional[Literal["company", "online_platform", "shop", "small_business", "individual", "household", "platform", "contractor", "government", "unknown"]] = None
    employer_type: Optional[Literal["company", "small_business", "shop", "household", "individual", "platform", "contractor", "government", "unknown"]] = Field(
        None, description="wages only: who employs the user. A family/home that employs a maid, cook, driver, nanny = household")
    has_written_terms: Optional[bool] = Field(None, description="is there any written agreement/offer letter? null if not said")
    consumer_subject: Optional[Literal["goods", "service"]] = None
    contract_subject: Optional[Literal["loan", "work", "goods", "deposit", "other"]] = None
    amount: Optional[float] = Field(None, description="main amount in dispute in rupees, only if the user stated it")
    amount_text: Optional[str] = Field(None, description="the user's exact words for that amount, verbatim (e.g. '15,000', '12 hazaar')")
    events: list[Event] = Field(default_factory=list)
    due_date: Optional[str] = Field(None, description="YYYY-MM-DD when the money became due / the problem arose, only if stated")
    due_date_text: Optional[str] = Field(None, description="verbatim words for due_date")
    evidence_have: list[str] = Field(default_factory=list, description="proof the user says they HAVE")
    prior_steps: list[str] = Field(default_factory=list)
    user_state: Optional[str] = Field(None, description="Indian state/city if mentioned")
    work_or_purchase_proven: Optional[bool] = None
    amount_documented: Optional[bool] = None
    other_side_acknowledged: Optional[bool] = None
    prior_demand_made: Optional[bool] = None
    arrest_or_police: bool = False
    criminal_allegation_against_user: bool = False
    violence_or_threat_to_safety: bool = False
    family_or_property_matter: bool = False
    court_summons_or_case_filed: bool = False
    cheque_bounce: bool = False
    rent_or_tenancy: bool = False
    distress: bool = Field(False, description="user expresses hopelessness or self-harm")
    missing_questions: list[str] = Field(default_factory=list, description="at most 2 short questions whose answers would change the advice")


# ── Generation (LLM drafts claims; verifier checks each one) ──
class Claim(BaseModel):
    section: Literal["where_you_stand", "law", "next_steps"]
    tag: Tag
    text: str = Field(description="one or two plain sentences")
    cites: list[str] = Field(default_factory=list, description="chunk IDs from the provided sources")
    quote: Optional[str] = Field(None, description="exact words copied from the cited chunk that support this claim")


class Advice(BaseModel):
    plain_summary: str = Field(description="2-3 calm sentences: what happened and the honest bottom line")
    claims: list[Claim]
    cannot_assess: list[str] = Field(default_factory=list, description="what you cannot judge from the sources or facts")
    lawyer_needed_when: list[str] = Field(default_factory=list)


class ClaimVerdict(BaseModel):
    id: int
    verdict: Literal["supported", "partially_supported", "unsupported"]
    reason: str


class Verdicts(BaseModel):
    verdicts: list[ClaimVerdict]


class Queries(BaseModel):
    queries: list[str] = Field(description="2-3 short English legal keyword queries")


# ── Notice decoder ──
class Snip(BaseModel):
    value: Optional[str] = None
    snippet: Optional[str] = Field(None, description="exact words copied from the notice that show this value")


class Provision(BaseModel):
    act: str = Field(description="act name as written in the notice")
    section: Optional[str] = None
    snippet: str = Field(description="exact words from the notice where it is cited")


class NoticeFacts(BaseModel):
    sender_type: Literal["advocate_or_party", "company_or_bank", "employer", "court", "police", "government", "unknown"]
    sender: Snip
    recipient: Snip
    notice_date: Snip
    demand_amount: Snip
    demand_action: Snip
    deadline: Snip = Field(description="the deadline as written, e.g. 'within 15 days of receipt'")
    deadline_days: Optional[int] = Field(None, description="number of days if the deadline is 'within N days'")
    threatened_consequences: list[Snip] = Field(default_factory=list)
    provisions: list[Provision] = Field(default_factory=list)
    subject: Literal["money_demand", "consumer", "employment", "cheque_bounce", "eviction_rent", "property_family", "criminal", "court_summons", "unclear"]
    plain_summary: str


class SimTurn(BaseModel):
    line: str = Field(description="what the other side says, 1-3 sentences, in character")
