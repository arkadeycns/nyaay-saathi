# 3-minute walkthrough: script and shot list

Record with Loom (screen + mic), browser at full width. Speak slowly. Before recording, run each demo once so rate limits are warm, and keep a second tab with a finished result in case Groq throttles live.

| Time | Screen | Say (roughly) |
|---|---|---|
| 0:00–0:20 | App home, sidebar visible | "This is an AI legal helper for everyday problems in India: unpaid wages, refunds, notices. Its one rule is that it never states law it can't show you, word for word." |
| 0:20–1:10 | **Advisor**: type the house-help message in Hinglish ("Main 2 saal se ek ghar mein kaam karti hoon… 27000…"). Show the step-by-step status, answer the clarifying question, then show the result. | "It asks at most two questions. Notice it understood she's a household worker: no 'ask HR', no 'salary slips'. Her checklist is UPI history, the watchman as a witness, the employer's address. The deadline and the strength band are computed by code, not the model. Every Law line has an exact quote. Open one and you see the actual section text, highlighted." |
| 1:10–1:25 | Expand "How this was checked" | "Here it shows the verifier removed statements it couldn't prove, and tells the user." |
| 1:25–1:40 | Type: "My landlord is not returning my deposit" | "Rent is state law, which I don't cover, so it says so plainly and gives only safe steps. Same for police, court summons and cheque bounce." |
| 1:40–2:15 | **Notice Decoder**: paste the sample notice citing the 1986 Act + ICA s73; set the received date | "Every detail it extracts must appear word for word in the notice. It flags that the sender cites a repealed Act, shows the real section 73, and counts the days left from when you received it." |
| 2:15–2:40 | **Simulator**: household worker, one exchange | "You can rehearse. The other side's lines come from a reviewed library, so the AI can't invent threats. The side panel shows what the law actually says, and the risks of believing the bluff or calling it." |
| 2:40–3:00 | Terminal: `pytest` green, then `eval/outputs/<run>/summary.md` | "654 chunks tested verbatim, 55 tests, and 42 eval cases with raw unedited outputs, including the ones that fail. Thanks." |
