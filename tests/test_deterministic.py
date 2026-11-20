"""Tests for the deterministic half: rules engine, limitation, verifier layer A, decoder date math."""
from datetime import date

import pytest

from rules import engine, load
from rules.limitation import add_years, compute
from rules.validate import validate
from schemas import Advice, CaseFacts, Claim
from verify import check_claim, check_text, drop_failed


def facts(**kw):
    base = dict(summary="x", category="wages")
    base.update(kw)
    return CaseFacts(**base)


# ── rules files are consistent with the corpus ──
def test_rules_validate_clean():
    assert validate() == []


# ── escalation ──
@pytest.mark.parametrize("text,kind", [
    ("Police ne mujhe thane bulaya hai, FIR ho gayi", "criminal"),
    ("My husband beats me and took my salary", "safety"),
    ("मकान मालिक security deposit wapas nahi kar raha", "state_rent"),
    ("I got a summons from the district court", "court"),
    ("His cheque bounced and now he says NI Act notice", "cheque_bounce"),
    ("Bhai ke saath ancestral property ka jhagda", "family_property"),
])
def test_keyword_triggers(text, kind):
    assert kind in engine.keyword_triggers(text)


@pytest.mark.parametrize("text", [
    "The warranty card says 1 year",            # 'warrant' must not match 'warranty'
    "My employer did not pay my salary for March",
    "Amazon refund not received for 3 weeks",
    "Client ne invoice ka payment nahi kiya",
])
def test_no_false_escalation(text):
    assert engine.keyword_triggers(text) == {}


def test_llm_flag_can_add_but_not_remove():
    hits = engine.escalation_triggers("salary not paid", facts(arrest_or_police=True))
    assert "criminal" in hits
    hits = engine.escalation_triggers("police arrested me", facts(arrest_or_police=False))
    assert "criminal" in hits


def test_amount_threshold():
    assert "amount" in engine.escalation_triggers("x", facts(amount=5_000_000))


def test_crisis():
    assert engine.crisis_check("I want to end my life")
    assert engine.crisis_check("ab jeena nahi hai")
    assert not engine.crisis_check("I want my refund")


def test_blocked_tactic():
    assert engine.blocked_tactic("Kya main FIR karwa du taaki dar ke paise de de? pressure banane ke liye")
    assert engine.blocked_tactic("I want to file an FIR to pressure him so he pays")
    assert not engine.blocked_tactic("I want my money back")


# ── context decides evidence, pathways and forbidden words ──
def test_household_worker_context_never_gets_hr():
    f = facts(employer_type="household", has_written_terms=False)
    ctx = engine.choose_context(f)
    assert ctx["id"] == "household_worker"
    ev = " ".join(e["item"] + e["how_to_get"] for e in engine.evidence_for("wages", ctx["id"], []))
    assert "HR" not in ev and "salary slip" not in ev.lower() and "offer" not in ev.lower()
    paths = [p["id"] for p in engine.pathways_for("wages", ctx["id"])]
    assert "hr_escalation" not in paths and paths[0] == "written_demand"
    assert "HR" in engine.avoid_terms(ctx["id"])


def test_formal_employee_gets_hr_path():
    ctx = engine.choose_context(facts(employer_type="company", has_written_terms=True))
    assert ctx["id"] == "formal_employee"
    assert "hr_escalation" in [p["id"] for p in engine.pathways_for("wages", ctx["id"])]


def test_online_purchase_context():
    ctx = engine.choose_context(facts(category="consumer", other_party_type="online_platform"))
    assert ctx["id"] == "online_purchase"


# ── span checks: never keep amounts/dates the user did not say ──
def test_amount_dropped_if_not_in_text():
    f, notes = engine.checked_facts(facts(amount=50000, amount_text="50,000"), "salary nahi mili 3 mahine se")
    assert f.amount is None and notes


def test_amount_kept_when_verbatim():
    f, _ = engine.checked_facts(facts(amount=15000, amount_text="15,000"), "phone 15,000 ka tha")
    assert f.amount == 15000


@pytest.mark.parametrize("s,v", [("15,000", 15000), ("12 hazaar", 12000), ("1.5 lakh", 150000), ("₹9000", 9000)])
def test_amount_parse(s, v):
    assert engine.amount_from_text(s) == v


# ── limitation (code-only date arithmetic) ──
def test_add_years_leap():
    assert add_years(date(2024, 2, 29), 2) == date(2026, 2, 28)


def test_consumer_two_years_from_corpus():
    r = compute("consumer", date(2025, 1, 10), today=date(2026, 11, 20))
    assert r.ref == "CPA2019:s69" and r.deadline == date(2027, 1, 10) and r.status == "soon"


def test_expired_and_urgent():
    assert compute("contract_payment", date(2020, 1, 1), today=date(2026, 11, 20)).status == "expired"
    assert compute("wages", date(2023, 12, 10), today=date(2026, 11, 20)).status == "urgent"


def test_loan_context_uses_article_19():
    assert compute("contract_payment", date(2025, 1, 1), "personal_loan", date(2026, 1, 1)).ref == "LA1963:schedule_art19"


def test_strength_unknown_is_not_true():
    s = engine.strength(facts(), None)
    assert s["band"] == "cannot_assess"
    s = engine.strength(facts(work_or_purchase_proven=True, amount_documented=True, other_side_acknowledged=True,
                              prior_demand_made=True), compute("wages", date(2026, 8, 1), today=date(2026, 11, 20)))
    assert s["band"] == "strong"


# ── verifier layer A ──
CH = {"CPA2019:s69": "69. (1) The District Commission ... shall not admit a complaint unless it is filed within two years from the date on which the cause of action has arisen."}


def test_claim_quote_must_be_verbatim():
    ok = Claim(section="law", tag="Law", text="You must file within two years.", cites=["CPA2019:s69"],
               quote="filed within two years from the date on which the cause of action has arisen")
    bad = ok.model_copy(update={"quote": "filed within three years of the cause of action"})
    assert check_claim(ok, CH) == []
    assert any("quote" in i for i in check_claim(bad, CH))


def test_claim_number_must_be_in_source():
    c = Claim(section="law", tag="Law", text="You have 3 years to file.", cites=["CPA2019:s69"], quote="within two years")
    assert any("'3" in i or "3years" in i for i in check_claim(c, CH))


def test_claim_citing_unknown_chunk_fails():
    c = Claim(section="law", tag="Law", text="x", cites=["CPA2019:s999"], quote="x")
    assert check_claim(c, CH)


def test_law_claim_needs_citation():
    assert check_claim(Claim(section="law", tag="Law", text="The law says you win."), CH)


def test_practical_claim_ok_without_cite():
    assert check_claim(Claim(section="next_steps", tag="Practical", text="Keep screenshots."), CH) == []


@pytest.mark.parametrize("text", ["As held in Ramesh Kumar vs Union of India, ...", "See AIR 2019 SC 1234",
                                  "The Supreme Court has held that refunds are mandatory."])
def test_case_law_blocked(text):
    assert check_text(text, [])


def test_legacy_and_avoid_terms():
    assert check_text("Under the Payment of Wages Act you can claim.", [])
    assert check_text("Please ask HR for your salary slip.", ["HR", "salary slip"])
    assert check_text("Gather your UPI history.", ["HR"]) == []


def test_drop_failed_removes_bad_claims():
    adv = Advice(plain_summary="Ask HR. Keep proof.", claims=[
        Claim(section="next_steps", tag="Practical", text="Keep proof."),
        Claim(section="next_steps", tag="Practical", text="Talk to HR."),
    ])
    from verify import Report
    out, n = drop_failed(adv, Report(passed=False, claim_issues={}), ["HR"])
    assert n == 1 and "HR" not in out.plain_summary


# ── decoder date arithmetic ──
def test_decoder_deadline():
    from nodes_decoder import deadline_status
    assert deadline_status(15, "2026-11-01", date(2026, 11, 20))["status"] == "expired"
    assert deadline_status(15, "2026-11-15", date(2026, 11, 20))["days_left"] == 10
    assert deadline_status(15, None)["status"] == "need_received_date"
    assert deadline_status(None, "2026-11-15")["status"] == "no_deadline_found"


def test_decoder_act_codes():
    from nodes_decoder import _act_code
    assert _act_code("Consumer Protection Act, 2019") == "CPA2019"
    assert _act_code("Consumer Protection Act, 1986") is None
    assert _act_code("Transfer of Property Act") is None


def test_language_detection():
    assert engine.detect_language("Madam ne paise nahi diye, kya karun") == "hinglish"
    assert engine.detect_language("मेरी तनख्वाह तीन महीने से नहीं मिली है, मैं क्या करूं") == "hi"
    assert engine.detect_language("My refund has not arrived") == "en"


def test_simulator_refusal_and_library_only():
    import nodes_simulator as S
    assert S.refuse_reason("help me threaten him")
    assert S.refuse_reason("I will pay on Friday") is None
    s = S.start("wages", "household_worker")
    ids = {b["id"] for b in load("bluffs")["bluffs"]}
    assert s["bluffs"] and all(b["id"] in ids for b in s["bluffs"])


@pytest.mark.parametrize("et", ["shop", "small_business", "contractor", "unknown", None])
def test_non_company_workers_never_get_hr(et):
    ctx = engine.choose_context(facts(employer_type=et))
    assert ctx["id"] != "formal_employee"
    assert "hr_escalation" not in [p["id"] for p in engine.pathways_for("wages", ctx["id"])]
    ev = " ".join(e["item"] + e["how_to_get"] for e in engine.evidence_for("wages", ctx["id"], []))
    assert "HR" not in ev and "salary slip" not in ev.lower()


@pytest.mark.parametrize("s,v", [("9 October 2026", "2026-10-09"), ("October 2026", "2026-10-01"), ("02.11.2026", "2026-11-02"),
                                 ("since Aug 2026", "2026-08-01"), ("last month", None)])
def test_date_from_text(s, v):
    assert engine.date_from_text(s) == v


def test_due_date_filled_from_text():
    f, _ = engine.checked_facts(facts(category="consumer", due_date_text="9 October 2026"), "picked up on 9 October 2026")
    assert f.due_date == "2026-10-09"
