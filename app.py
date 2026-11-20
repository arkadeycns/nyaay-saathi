"""
Streamlit UI only. All decisions happen in graph.py / nodes_*.py / rules/; this file renders them.
Nothing is persisted: state lives in st.session_state for the browser tab only.
"""
from __future__ import annotations

import html
import io
import json
import os
import pathlib
import re
from datetime import date

import streamlit as st
from jinja2 import Environment, FileSystemLoader

import config
import graph
from llm import LLMError, get_llm
from rules import load

ROOT = pathlib.Path(__file__).parent
st.set_page_config(page_title="Nyaya Saathi", page_icon="⚖️", layout="wide")

CSS = """
<style>
.block-container{max-width:1100px;padding-top:4.5rem}  /* clears Streamlit's fixed top bar (~3.75rem) */
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.72rem;font-weight:600;margin-right:6px;vertical-align:middle}
.tag-Law{background:#e3edff;color:#1c3f94}.tag-Official{background:#e5f6ec;color:#17643a}.tag-Practical{background:#f1efe9;color:#5c5240}
.tag-unv{background:#fde8e8;color:#9b1c1c}
.band{display:inline-block;padding:4px 12px;border-radius:14px;font-weight:700}
.band-strong{background:#d9f2e3;color:#14532d}.band-moderate{background:#fff3cd;color:#7a5a00}
.band-weak{background:#fde2e1;color:#8a1c1c}.band-cannot_assess{background:#eceff3;color:#334}
.claim{margin:.35rem 0 .1rem 0;line-height:1.45}
mark{background:#fff1a8;padding:0 2px}
.hl{background:#fff1a8;border-radius:3px;padding:0 2px}
@media (max-width:640px){.block-container{padding-left:16px;padding-right:16px}}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)

BUSY = "The free AI service is busy or unreachable right now (rate limit). Please wait a minute and try again."
SECTION_TITLES = {"where_you_stand": "Where you stand", "law": "What the law says", "next_steps": "What you can do this week"}


# ── helpers ──────────────────────────────────────────────────────
def tag_html(tag: str) -> str:
    cls = "Official" if tag.startswith("Official") else tag
    return f'<span class="tag tag-{cls}">{html.escape(tag)}</span>'


def safe_run(fn, *a, **kw):
    """Run a graph/LLM call; turn provider failures into a calm message instead of a stack trace."""
    try:
        return fn(*a, **kw)
    except (LLMError, EnvironmentError) as exc:
        st.error(BUSY if "rate" in str(exc).lower() or "respond" in str(exc).lower() else f"{BUSY}\n\nDetail: {exc}")
    except Exception as exc:  # never show a raw traceback to a worried user
        st.error(f"Something went wrong on our side. Please try again. ({type(exc).__name__})")
    return None


def run_graph(g, state: dict) -> dict | None:
    with st.status("Working on it… this can take 20–40 seconds", expanded=True) as box:
        out = safe_run(graph.run, g, state, on_step=lambda node, label: box.write(f"✓ {label}"))
        box.update(label="Done" if out else "Stopped", state="complete" if out else "error", expanded=False)
    return out


def show_source(cid: str, sources: dict, quote: str | None = None) -> None:
    s = sources.get(cid)
    if not s:
        from retrieval import get_index
        ch = get_index().get_chunk(cid)
        if not ch:
            st.caption(f"{cid}: not found")
            return
        s = {"act": ch.act, "heading": ch.heading, "full_text": ch.text, "url": ch.source_url}
    text = html.escape(s.get("full_text") or s.get("text", ""))
    for part in re.split(r"\.\.\.|…", quote or ""):
        words = html.escape(part.strip()).split()
        if len(words) >= 2:  # match across the PDF's line breaks
            pat = re.compile(r"\s+".join(re.escape(w) for w in words), re.I)
            text = pat.sub(lambda m: f"<mark>{m.group(0)}</mark>", text, count=1)
    st.markdown(f"**{cid}** — {html.escape(s['act'])}: {html.escape(s.get('heading', ''))}")
    st.markdown(f"<div style='white-space:pre-wrap;font-size:.85rem'>{text}</div>", unsafe_allow_html=True)
    if s.get("url"):
        st.caption(f"Source: {s['url']}")


def render_claims(claims: list[dict], sources: dict, partial: list[int] | None = None) -> None:
    by = {}
    for i, c in enumerate(claims):
        by.setdefault(c["section"], []).append((i, c))
    for sec in ("where_you_stand", "law", "next_steps"):
        if sec not in by:
            continue
        st.markdown(f"#### {SECTION_TITLES[sec]}")
        for i, c in by[sec]:
            extra = ' <span class="tag tag-unv">partly supported</span>' if partial and i in partial else ""
            st.markdown(f'<div class="claim">{tag_html(c["tag"])}{html.escape(c["text"])}{extra}</div>', unsafe_allow_html=True)
            if c.get("cites"):
                with st.expander("Show the exact law text (" + ", ".join(c["cites"]) + ")"):
                    for cid in c["cites"]:
                        show_source(cid, sources, c.get("quote"))


def render_escalation(esc: dict) -> None:
    st.warning("**I need to stop here and point you to a person who can help.**\n\n" + "\n\n".join(esc.get("reasons", [])))
    st.markdown("**Safe next steps** " + tag_html("Practical"), unsafe_allow_html=True)
    for s in esc.get("safe_steps", []):
        st.markdown(f"- {s}")


def render_verifier(log: list[dict], dropped: int) -> None:
    with st.expander("How this answer was checked"):
        st.caption("Every legal statement must quote the law text it relies on. Code checks the quote and numbers; "
                   "a second AI model from a different family checks the meaning. Failed statements are rewritten once, then removed.")
        if dropped:
            st.info(f"{dropped} statement(s) were removed because they couldn't be verified.")
        for a in log or []:
            st.markdown(f"Attempt {a['attempt']}: {'passed ✅' if a['passed'] else 'issues found'}")
            for iss in a.get("issues", [])[:8]:
                st.caption(f"• {iss}")


# ── drafts ───────────────────────────────────────────────────────
_jinja = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=False, trim_blocks=True)
DRAFT_NAMES = {"demand_letter": "Letter asking for payment", "consumer_complaint": "Consumer complaint outline",
               "legal_aid_application": "Free legal aid application (DLSA)", "reply_to_notice": "Reply to a notice"}


def draft_form(key: str, kinds: list[str], facts: dict, verified_points: list[str]) -> None:
    with st.expander("📝 Prepare a document (you check and edit every fact)"):
        kind = st.selectbox("Document", kinds, format_func=lambda k: DRAFT_NAMES[k], key=f"{key}_kind")
        c1, c2 = st.columns(2)
        v = {
            "user_name": c1.text_input("Your name", key=f"{key}_un"),
            "user_address": c1.text_input("Your address / phone", key=f"{key}_ua"),
            "other_name": c2.text_input("Other side's name", facts.get("other_party_name") or "", key=f"{key}_on"),
            "other_address": c2.text_input("Other side's address", key=f"{key}_oa"),
            "amount": c1.text_input("Amount (₹)", f"{facts['amount']:,.0f}" if facts.get("amount") else "", key=f"{key}_am"),
            "since": c2.text_input("Since / date", facts.get("due_date_text") or "", key=f"{key}_si"),
            "pay_by": c1.text_input("Pay by (date)", key=f"{key}_pb"),
            "district": c2.text_input("District", key=f"{key}_di"),
        }
        # Not pre-filled from the AI summary: the letter must be the person's own first-person account.
        v["facts"] = st.text_area("What happened, in your own words (first person, only true facts)", key=f"{key}_fa",
                                  placeholder="e.g. I worked at your home as a cook from June 2024. My pay was ₹9,000 a month. "
                                              "I have not been paid for August, September and October 2026.")
        use_law = st.checkbox("Include the verified legal points from the answer above", value=False, key=f"{key}_law",
                              disabled=not verified_points)
        v["legal_points"] = verified_points if use_law else []
        v["today"] = date.today().strftime("%d %b %Y")
        text = _jinja.get_template(f"{kind}.jinja2").render(**v)
        st.text_area("Draft", text, height=320, key=f"{key}_out")
        st.download_button("Download draft (.txt)", text, file_name=f"{kind}.txt", key=f"{key}_dl")


# ── sidebar ──────────────────────────────────────────────────────
def sidebar() -> None:
    with st.sidebar:
        st.markdown("## ⚖️ Nyaya Saathi")
        st.caption("Plain-language help with everyday legal problems in India")
        st.info("**Information, not legal advice.** For courts, police, safety or large amounts, talk to an advocate or the "
                "District Legal Services Authority (free, NALSA helpline 15100).")
        st.markdown(f"**Law snapshot:** {config.AS_OF}")
        with st.expander("What law I know"):
            for f in sorted((ROOT / "corpus" / "processed").glob("*.jsonl")):
                rows = f.read_text(encoding="utf-8").splitlines()
                st.caption(f"{json.loads(rows[0])['act']} — {len(rows)} sections/clauses" if rows else f.stem)
        with st.expander("Scope and limits"):
            st.markdown("- Central law only: consumer, contract, limitation, Code on Wages, legal aid\n"
                        "- English, Hindi, Hinglish text; voice input (not output)\n"
                        "- **Not covered:** state law (rent, tenancy, deposits), criminal, family, property, cheque bounce, "
                        "case law\n- No photos/scans (text PDFs only)")
        with st.expander("Privacy"):
            p = config.get_provider()
            st.caption("Nothing is saved after you close this tab. Your text is sent to the AI provider to generate answers. "
                       + p.data_note + " Please avoid sharing ID numbers or bank details.")
            st.caption(f"Provider: {p.name} · {', '.join(f'{k}: {m}' for k, m in p.models.items())}")


# ── advisor tab ──────────────────────────────────────────────────
def advisor_tab() -> None:
    ss = st.session_state
    fixture = os.getenv("UI_FIXTURE")  # dev/demo only: render a saved advisor state without calling the LLM
    if fixture and "messages" not in ss:
        saved = json.loads(pathlib.Path(fixture).read_text(encoding="utf-8"))
        ss.messages = [m for m in saved["messages"] if m["content"] != "(questions)"]
        ss.adv_state = saved
        ss.messages.append({"role": "assistant", "content": saved["advice"]["plain_summary"], "render": "result"})
    ss.setdefault("messages", [])
    ss.setdefault("clarify_rounds", 0)
    ss.setdefault("adv_state", None)
    if not ss.messages:
        st.markdown("#### Tell me what happened, in your own words")
        st.caption("For example: *“Maine online phone liya, kharab nikla, refund 3 hafte se nahi aaya”* or "
                   "*“I work as a cook in a house, my salary for 3 months is unpaid.”* Mention amounts and dates if you know them.")
    for i, m in enumerate(ss.messages):
        with st.chat_message(m["role"]):
            if m.get("render") == "result" and ss.adv_state:
                render_advice(ss.adv_state)
            else:
                st.markdown(m["content"])
    live = st.container()  # new turns render here, above the input, while the graph runs
    if ss.get("adv_error"):
        st.error(ss.pop("adv_error"))
    voice_box()
    prompt = st.chat_input("Describe your problem…", key="adv_in")
    if ss.get("voice_text_ready"):
        prompt, ss.voice_text_ready = ss.voice_text_ready, None
    if prompt:
        ss.messages.append({"role": "user", "content": prompt})
        with live:
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                msgs = [{"role": m["role"], "content": m["content"]} for m in ss.messages]
                out = run_graph(graph.advisor_graph(), {"messages": msgs, "clarify_rounds": ss.clarify_rounds, "audit": []})
        if out:
            handle_advisor_outcome(out)
        else:  # keep the conversation consistent: drop the unanswered turn, tell them to resend
            ss.messages.pop()
            ss.adv_error = BUSY + " Your last message was not sent; please send it again."
        st.rerun()
    if ss.messages and st.button("Start over", key="reset_adv"):
        for k in ("messages", "clarify_rounds", "adv_state"):
            ss.pop(k, None)
        st.rerun()


def handle_advisor_outcome(out: dict) -> None:
    ss = st.session_state
    ss.clarify_rounds = out.get("clarify_rounds", ss.clarify_rounds)
    oc = out.get("outcome")
    if oc == "crisis":
        text = out["crisis_message"]
    elif oc == "escalate":
        e = out["escalation"]
        text = ("**I need to stop here and point you to someone who can help.**\n\n" + "\n\n".join(e["reasons"]) +
                "\n\n**Safe next steps (Practical):**\n" + "\n".join(f"- {s}" for s in e["safe_steps"]))
    elif oc == "clarify":
        n = len(out["questions"])
        text = f"Thank you for explaining. {'Two quick questions' if n > 1 else 'One quick question'} so I don't guess:\n\n" + "\n".join(f"- {q}" for q in out["questions"])
    elif oc == "use_decoder":
        text = "It sounds like you received a notice. Please paste it in the **Notice Decoder** tab, and I'll go through it line by line."
    elif oc == "ungrounded":
        text = ("I couldn't find the law for this in the central Acts I hold, so I won't guess.\n\n**Safe next steps (Practical):**\n"
                + "\n".join(f"- {s}" for s in load("escalation")["safe_steps"]))
    else:
        ss.adv_state = out
        ss.messages.append({"role": "assistant", "content": out["advice"]["plain_summary"], "render": "result"})
        return
    if out.get("blocked"):
        text = f"⚠️ {out['blocked']}\n\n" + text
    ss.messages.append({"role": "assistant", "content": text})


def render_advice(s: dict) -> None:
    adv, res, sources = s["advice"], s["result"], s.get("sources", {})
    if s.get("blocked"):
        st.warning(s["blocked"])
    if s.get("distress"):
        st.info("It sounds like this is weighing on you heavily. If you need someone to talk to, Tele-MANAS (free, 24×7) "
                "is at **14416** or 1-800-891-4416.")
    st.markdown(adv["plain_summary"])
    sb = res["strength"]
    st.markdown(f'<span class="band band-{sb["band"]}">How strong your position looks: {sb["band"].replace("_", " ")}</span>'
                f' <span class="tag tag-Practical">computed from your facts</span>', unsafe_allow_html=True)
    cols = st.columns(3)
    for col, title, items in zip(cols, ("Helps you", "Works against you", "Not known yet"), (sb["for"], sb["against"], sb["unknown"])):
        col.caption(f"**{title}**\n\n" + ("\n".join(f"- {x}" for x in items) or "—"))
    lim = res.get("limitation")
    if lim:
        box = {"expired": st.error, "urgent": st.error, "soon": st.warning}.get(lim["status"], st.success)
        box(f"**Time limit:** {lim['message']}\n\nCounted by code from the date you gave ({lim['trigger_date']}, {lim['trigger']}). "
            f"Please confirm that date. Law: “{lim['quote']}” [{lim['ref']}]")
    else:
        st.info("**Time limit:** I couldn't work out a date from what you said, so I can't count the deadline. Time limits apply, so don't wait.")
    for n in res.get("coverage_notes", []):
        st.markdown(f'<div class="claim">{tag_html(n["tag"])}{html.escape(n["text"])}</div>', unsafe_allow_html=True)
    render_claims(adv["claims"], sources, s.get("partial_claims"))
    if res.get("wages_state_rules_note"):
        st.caption("The Code on Wages, 2019 is in force from 21 Nov 2025; state rules under it may vary.")
    st.markdown("#### Proof to gather " + tag_html("Practical"), unsafe_allow_html=True)
    for e in res["evidence"]:
        mark = "✅" if e["status"] == "have" else "⬜"
        st.markdown(f"{mark} **{e['item']}**{' (important)' if e.get('required') else ''}  \n"
                    f"<small>How: {html.escape(e['how_to_get'])} · Why: {html.escape(e['why'])}</small>", unsafe_allow_html=True)
    st.markdown("#### Your options, side by side " + tag_html("Practical"), unsafe_allow_html=True)
    st.caption("Money and time figures are rough practical estimates, not quotes.")
    # Cards, not a table: a table truncates on phones, where most users will be.
    for i, p in enumerate(res["pathways"], 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {p['name']}**  \n💰 {p['money']} · ⏱️ {p['time']} · 😟 {p['emotional']}  \n"
                        f"<small>Risk: {html.escape(p['risk'])}</small>", unsafe_allow_html=True)
            if p.get("note"):
                st.caption(p["note"])
    st.markdown("#### When you need a real lawyer")
    for x in adv.get("lawyer_needed_when", []):
        st.markdown(f"- {x}")
    if adv.get("cannot_assess"):
        st.markdown("**What I can't judge from here:** " + "; ".join(adv["cannot_assess"]))
    render_verifier(s.get("verifier_log", []), s.get("dropped_claims", 0))
    verified_law = [f"{c['text']} [{', '.join(c['cites'])}]" for c in adv["claims"] if c["tag"] == "Law"]
    draft_form("adv", res.get("drafts", ["demand_letter"]), s.get("facts", {}), verified_law)


_HL = re.compile(r"(₹\s?[\d,]+|\b\d[\d,]*(?:\.\d+)?\s*(?:rs|rupees|rupaye|hazaar|hazar|lakh|k)?\b|"
                 r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b[A-Z][a-z]+\b)", re.I)


def voice_box() -> None:
    ss = st.session_state
    with st.expander("🎙️ Speak instead of typing (Hindi / English, up to 2 minutes)"):
        lang = st.radio("Language", ["Auto", "Hindi", "English"], horizontal=True, key="v_lang")
        audio = st.audio_input("Record", key="v_audio")
        if audio is not None and st.button("Convert to text", key="v_go"):
            data = audio.getvalue()
            if len(data) > 4_000_000:  # ~2 min of 16 kHz wav
                st.warning("That recording is longer than about 2 minutes. Please record a shorter message.")
            else:
                txt = safe_run(get_llm().transcribe, data, {"Hindi": "hi", "English": "en"}.get(lang), suffix=".wav")
                if txt is not None:
                    ss.v_text = txt
        if ss.get("v_text") is not None:
            edited = st.text_area("Check and correct the text (amounts, dates and names are highlighted below)", ss.v_text, key="v_edit")
            st.markdown("<small>" + _HL.sub(lambda m: f"<span class='hl'>{m.group(0)}</span>", html.escape(edited)) + "</small>",
                        unsafe_allow_html=True)
            if st.button("Use this text", key="v_use"):
                ss.voice_text_ready, ss.v_text = edited, None
                st.rerun()


# ── decoder tab ──────────────────────────────────────────────────
def decoder_tab() -> None:
    ss = st.session_state
    st.markdown("#### Got a legal notice or demand letter? Paste it here")
    st.caption("I'll explain what it is, what it isn't, what they want and by when, and check every law it cites.")
    up = st.file_uploader("Or upload a PDF (text PDFs only, not photos/scans)", type=["pdf"], key="dec_pdf")
    if up is not None and ss.get("dec_pdf_name") != up.name:
        from pypdf import PdfReader
        ss.dec_pdf_name = up.name
        ss.dec_text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(up.getvalue())).pages).strip()
        if not ss.dec_text:
            st.warning("I couldn't read any text in that PDF. It's probably a scan or photo, which I can't read yet. Please type or paste the text.")
    # A form commits the text and date together; plain widgets lose uncommitted text on reruns.
    with st.form("dec_form"):
        text = st.text_area("Notice text", height=220, key="dec_text")
        rec = st.date_input("When did you receive it?", value=None, max_value=date.today(), format="DD/MM/YYYY", key="dec_rec")
        go = st.form_submit_button("Explain this notice", type="primary")
    if go:
        if len(text.strip()) < 40:
            st.warning("Please paste the full text of the notice (at least a few lines).")
        else:
            ss.dec_state = run_graph(graph.decoder_graph(), {"notice_text": text, "received_date": rec.isoformat() if rec else None, "audit": []})
    s = ss.get("dec_state")
    if s:
        render_decoder(s)


def _snip(label: str, f: dict) -> None:
    if f and f.get("value"):
        st.markdown(f"**{label}:** {html.escape(str(f['value']))}  \n<small>From the notice: “{html.escape(f.get('snippet') or '')}”</small>",
                    unsafe_allow_html=True)


def render_decoder(s: dict) -> None:
    n = s["notice"]
    if s.get("outcome") == "escalate":
        render_escalation(s["triage"])
        return
    res, exp = s["result"], s["explanation"]
    st.markdown(f"### What this is\n{res['kind']['is']}  \n{html.escape(n.get('plain_summary', ''))}")
    st.markdown("### What it is not " + tag_html("Practical"), unsafe_allow_html=True)
    for x in res["kind"]["is_not"]:
        st.markdown(f"- {x}")
    st.markdown("### What they want and by when")
    for label, k in (("From", "sender"), ("Dated", "notice_date"), ("Amount demanded", "demand_amount"),
                     ("Action demanded", "demand_action"), ("Deadline as written", "deadline")):
        _snip(label, n.get(k))
    d = res["deadline"]
    if d["status"] == "need_received_date":
        st.warning(f"They give {d['days']} days. Enter the date you received it above so I can count the days left.")
    elif d["status"] == "running":
        st.info(f"Counting {d['days']} days from the date you received it: **{d['due']}** ({d['days_left']} days left). Counted by code; their exact wording matters, so check it.")
    elif d["status"] == "expired":
        st.error(f"Their {d['days']}-day period appears to have ended on {d['due']}. That doesn't by itself mean you've lost anything, but get advice soon (DLSA: 15100).")
    if res.get("threats"):
        st.markdown("**What they threaten:** " + " · ".join(f"“{html.escape(t)}”" for t in res["threats"]))
    st.markdown("### What they say the law is")
    labels = {"in_corpus": "✅ Found in my copy of the law", "repealed": "⚠️ Repealed law",
              "cannot_verify": "❔ Not a law I hold, so I can't verify it", "not_in_corpus": "❔ A law I don't cover",
              "not_found_in_snapshot": "❔ Not found in my copy"}
    for p in s.get("provisions", []):
        st.markdown(f"**“{html.escape(p['cited'])}”** — {labels.get(p['status'], p['status'])}"
                    + (f": {html.escape(p['detail'])}" if p.get("detail") else ""))
        if p.get("ref"):
            with st.expander(f"Read {p['ref']}"):
                show_source(p["ref"], s.get("sources", {}))
    if not s.get("provisions"):
        st.caption("The notice doesn't cite any specific law.")
    render_claims(exp["claims"], s.get("sources", {}))
    st.markdown("### Your options and the risk of each " + tag_html("Practical"), unsafe_allow_html=True)
    for o in res["options"]:
        st.markdown(f"- **{o['name']}** — {o['risk']}")
    st.markdown("### What to gather " + tag_html("Practical"), unsafe_allow_html=True)
    for g in res["gather"]:
        st.markdown(f"- {g}")
    render_verifier(s.get("verifier_log", []), s.get("dropped_claims", 0))
    st.warning(res["reply_warning"])
    facts = {"other_party_name": (n.get("sender") or {}).get("value"), "summary": ""}
    draft_form("dec", ["reply_to_notice", "legal_aid_application"], facts, [])


# ── simulator tab ────────────────────────────────────────────────
def simulator_tab() -> None:
    ss = st.session_state
    st.markdown("#### Practise talking to the other side")
    st.caption("Practice, not prediction: real people vary. The other side here only uses lines from a reviewed library; "
               "it cannot invent legal threats.")
    try:
        import nodes_simulator as S
    except ImportError:
        st.info("The simulator is being set up. Please try again shortly.")
        return
    cats = {"consumer": "Shop / online purchase", "wages": "Unpaid pay for work", "contract_payment": "Money owed under an agreement"}
    c1, c2 = st.columns(2)
    cat = c1.selectbox("Your situation", list(cats), format_func=cats.get, key="sim_cat")
    ctxs = [c for c in load("routing")["contexts"] if c["category"] == cat]
    ctx = c2.selectbox("More precisely", ctxs, format_func=lambda c: c["label"], key="sim_ctx")
    if st.button("Start practice", type="primary"):
        sess = safe_run(S.start, cat, ctx["id"])
        if sess is not None:
            ss.sim = safe_run(S.opponent_turn, sess, None) or sess
    sess = ss.get("sim")
    if not sess:
        return
    left, right = st.columns([3, 2])
    with left:
        for t in sess["turns"]:
            with st.chat_message("assistant" if t["role"] == "opponent" else "user", avatar="🧑‍💼" if t["role"] == "opponent" else None):
                st.markdown(t["text"])
        if sess.get("done"):
            co = safe_run(S.coaching, sess)
            if co:
                st.success("**Coaching summary**\n\n" + co["summary"])
                if not co.get("verified"):
                    st.caption("Some of this summary could not be verified and was trimmed.")
        else:
            reply = st.chat_input("Your reply…", key="sim_in")
            if reply:
                why = S.refuse_reason(reply)
                if why:
                    st.warning(why)
                else:
                    ss.sim = safe_run(S.opponent_turn, sess, reply) or sess
                    st.rerun()
    with right:
        cur = next((b for b in sess["bluffs"] if b["id"] == next((t.get("bluff_id") for t in reversed(sess["turns"]) if t.get("bluff_id")), None)), None)
        if cur:
            st.markdown("**What's actually true**" + ("" if cur.get("verified", True) else ' <span class="tag tag-unv">UNVERIFIED DRAFT</span>'),
                        unsafe_allow_html=True)
            for t in cur["truth"]:
                st.markdown(f'<div class="claim">{tag_html(t["tag"])}{html.escape(t["text"])}</div>', unsafe_allow_html=True)
                if t.get("ref"):
                    with st.expander(f"Source {t['ref']}"):
                        show_source(t["ref"], {}, t.get("quote"))
            st.markdown(f"**If you believe it:** {cur['risk_if_you_believe_it']}  \n**If you push back:** {cur['risk_if_you_call_it']}")
            st.markdown(f"**A reply you could use:** _{cur['suggested_reply']}_")


# ── main ─────────────────────────────────────────────────────────
sidebar()
st.markdown("### ⚖️ Nyaya Saathi: understand your legal problem, honestly")
t1, t2, t3 = st.tabs(["💬 Advisor", "📄 Notice Decoder", "🥊 Practise with the other side"])
with t1:
    advisor_tab()
with t2:
    decoder_tab()
with t3:
    simulator_tab()
