# Nyaay Saathi: an honest AI legal helper for everyday problems in India

**Code:** github.com/arkadeycns/nyaay-saathi · **App:** APP_URL · **Raw outputs:** `eval/outputs/` in the repo · **Video:** VIDEO_URL

## 1. The problem I picked, and why
**Problem 2, the AI lawyer.** I kept picturing one person: middle-class, never been to a court, scared of lawyers, and willing to let a ₹15,000 refund or three months of unpaid salary go rather than "get into trouble". A chatbot that sounds confident is the wrong tool for that person, because they can't tell a real section from an invented one. So the question I set myself was not "can a model give legal advice" but **"can I let a model help without ever letting it make up law?"**

## 2. How I approached it, and how I used AI tools
I treated each AI tool as a teammate with a different job, and kept the decisions for myself.

- **Framing (Gemini 4 Neon).** I first used Gemini to write a prompt that would make another model *interview me* instead of jumping into code ([share link](https://share.gemini.google/cTQ8IVXBGhSH)). I wanted my own angle, not a generic design.
- **Discovery and design (Claude Sonnet 5.5, [conversation](https://claude.ai/share/59a748d1-826f-4dd4-b433-9be47a046b04)).** That interview is where most of the product came from. I refused to pick a single niche: a niche "is a good testing prototype but not a working deployable one". I asked for **tactics, not just law**: what a scared person can actually do, and what each option costs "financially and mentally (and time)". I asked for two modes, an advisor and an **opponent simulator**, and for "each and every suggestion" to be based on cited official sources. I also changed my mind when the argument was better. I had asked for continuous retrieval and a single LLM verifier; I dropped continuous retrieval ("that's my bad") and agreed that an LLM verifier alone "is not enough". I asked the model to audit the plan against the brief (11 of 16 points covered), then decided the gaps myself. **Voice in. Rent and deposit law out**, because sending someone to a state-law advocate anyway makes that advice useless. A **notice decoder** as the differentiator. A **log of every AI-tool failure**. The session ended with a standalone build prompt.
- **First prototype (Gemini Flash 3.8).** Fast, and it looked complete, but when I tested it properly it wasn't honest. The "eval outputs" came from a silent mock engine (0 s per answer). The law was paraphrased from memory instead of taken from the Acts. And a house-help whose employer stopped paying her was told to "go to HR".
- **Rebuild to a deployable product (Claude Opus 5.5).** I pointed out the HR failure and asked for a real rebuild: official sources only, current free models checked against provider docs, LangSmith tracing (opt-in only), a proper UI and a 40+ case eval. I made the calls on privacy, deployment and what to cut when time ran out.

**What I learned:** the AI tools were most useful when I asked them to argue with me, and most dangerous when their output *looked* finished. Every serious bug was caught by reading real outputs, not by trusting a passing test.

## 3. Assumptions and scope
- **Law:** central law only, as a fixed snapshot from official government-hosted PDFs (source and hash recorded in `corpus/MANIFEST.md`): Consumer Protection Act 2019 plus its 2021 jurisdiction rules, Contract Act 1872, Limitation Act 1963, Code on Wages 2019, Legal Services Authorities Act 1987.
- **Out of scope, but recognised and referred with safe steps:** state law (rent, deposits), criminal, family and property matters, cheque bounce, and any case law.
- **Input:** English, Hinglish and Hindi text, plus voice input. No OCR, no voice output. Free tiers only (Groq + Streamlit Cloud); Gemini or OpenRouter work with one config change.

## 4. How it works: code decides, the LLM drafts
Three tabs: **Advisor** (chat → case file → verified advice), **Notice Decoder** (what the letter is, what it isn't, the deadline, and whether the law it quotes is real), **Opponent Simulator** (practise against bluffs from a reviewed library).

| Done by code (rules you can audit) | Done by the LLM |
|---|---|
| Crisis, police, court and cheque-bounce triggers; blocked tactics such as "FIR to scare him" (the LLM can add flags, never remove them) | Turning a messy Hinglish story into a case file |
| Keeping amounts and dates only if the user's exact words contain them | Rewriting the story into search terms (right section found in 30/30 rewritten queries vs 17/30 raw) |
| The person's **situation** (household worker, shop worker, online buyer…) → their evidence list, option order and banned words (e.g. no "HR" for a house-help) | Explaining in plain words, as tagged claims: Law / Official procedure / Practical |
| Deadlines, strength band, cost table, drafts | Voicing the opponent's tone (lines come from the library) |
| **Verifier A:** every quote is verbatim, every number or time limit is in the cited text, no case law or repealed Acts | **Verifier B:** a different model family checks each claim against only its cited text |

A claim that fails verification is regenerated once; if it still fails, it is removed and the user is told. **Human in the loop:** the user confirms every fact before a draft is made, edits the voice transcript, and gives the date the notice actually arrived.

## 5. How it would scale
Retrieval runs locally (ONNX embeddings, committed index), so the only per-user cost is LLM tokens: about **8,700 tokens per advice session**, roughly **$0.002 (≈₹0.20)** at an assumed paid Groq price (TODO_USER_VERIFY against Groq's price page). Adding a law means adding a PDF and a source line; tests stop paraphrased law from slipping in. State rent law would be the next pack, one state at a time. With budget: a paralegal review queue for drafts, DLSA hand-offs, saved case files with consent, more Indian languages.

## 6. How I know it's good, and where it fails
- **Corpus:** a test proves all 654 chunks are word-for-word from the official PDFs, and a validator proves every legal line in the rules quotes the corpus exactly. **61 tests** cover the deterministic code.
- **End-to-end eval:** I wrote 42 cases: realistic, hallucination bait, escalation, messy Hinglish, Hindi, simulator and voice. **Only 15 ran**: time ran out, and Groq's free tier has a daily token cap that the eval shares with the live app. Cases 16–42 have no outputs; none were hand-picked. Raw, unedited outputs are in the repo.

| Metric (15 cases) | Value |
|---|---|
| Passed every check | 10 / 15 (2 of the 5 failures are rate-limit errors, not wrong answers) |
| Law claims with a real citation and an exact quote | 92% (25 claims) |
| Should have gone to a lawyer and did / went there unnecessarily | 100% recall / 67% precision |
| Informal workers told "HR" or "salary slip" | 0 |
| Repealed Acts caught (e.g. a notice citing CPA 1986) | 100% |
| Typical answer time | 35 s (slow end 5 min, mostly free-tier rate-limit waits) |

- **Where it fails:**
  - A refund question that also asked for a Supreme Court case was escalated instead of helped. Fixed after the run.
  - The model sometimes copied a date but left it unparsed, so no deadline appeared. Now parsed in code.
  - About 16% of legal claims ship marked "partially supported". They are shown with a warning, not hidden.
  - Whether a private home counts as an "establishment" under the Code on Wages isn't settled by the text, so the tool says so and points to free legal aid.
  - The Contract Act copy is a state transcription with typos. Voice has not been tested on real recordings.
- **Where AI tools failed while building** (all in `docs/build_log.md`): the prototype's corpus was paraphrased from memory; a rank-based retrieval gate let nonsense through; retired Gemini model IDs; hidden reasoning tokens used up the output budget; a verifier returned the wrong JSON key; and the advice once addressed the employer instead of the worker.
