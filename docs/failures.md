# Failures and known limitations

Kept as found. Cases are not edited to pass.

## Eval failures (run `20261120_220100_groq`, interim)
| Case | What failed | Cause | Status |
|---|---|---|---|
| A05_online_refund | No deadline shown | Intake model copied the date words ("9 October 2026") but left the ISO date null | Fixed: deterministic `engine.date_from_text` fallback + tests; needs re-run |
| A08_decoder_repealed_section | expected `running`, got `expired` | Test-design error: received 12 Nov + 7 days = 19 Nov, which is before today (20 Nov). The code's arithmetic is right | Case expectation is time-dependent; to fix in the next suite version |
| A01 (smoke run) | Error | Groq 429 still throttled after 5 retries during the verifier's regeneration | Retries now wait max(retry-after, exponential); 7 attempts |
| A03 (smoke run) | "HR", "salary slip" in output for a shop helper | Shop worker without stated written terms fell through to the formal-employee context | Fixed: contexts keyed on employer type; catch-all is informal; regression tests |

## Known limitations
1. **Partial support ships with a flag.** About 19% of law claims are judged "partially supported" by verifier B. They are shown with a warning, not removed. A stricter setting would drop them, giving shorter but safer answers.
2. **Verifier can over-drop.** True statements without a verbatim quote are removed.
3. **Coverage of household work under the Code on Wages is unsettled** by the statute text ("establishment"). The tool says so and points to the DLSA instead of guessing.
4. **Corpus quality.** The ICA 1872 copy is a state transcription with typos. CPA 2019 is the text as enacted. India Code couldn't be downloaded automatically.
5. **Free-tier latency.** 20–60 s per answer, sometimes a friendly "busy, try again" message. Gemini free models returned 503 "high demand" and timeouts during the build.
6. **Voice** is untested on real recordings (cases F01–F04 need audio files). No OCR.
7. **Out of scope by design:** state law (rent and deposits), criminal, family and property, cheque bounce, case law. These are escalated with safe steps.
8. **Time-dependent outputs.** Deadlines depend on today's date, so re-running an eval later can change the expected status.
