# Failures & Limitations

An honest record of where the system fails, where AI tools failed during
building, and what this prototype cannot do.

## System limitations

### Hard limitations (by design)
1. **No case law or precedent** — by design. LLMs hallucinate case names and citations. This system refuses to cite any case.
2. **Central law only** — state-specific laws (rent, tenancy, land) are explicitly out of scope. The system says so and refers to an advocate.
3. **Fixed corpus snapshot** — law changes. The corpus has an AS_OF date. Anything after that date is not reflected.
4. **No criminal law** — recognised and refused with safe steps.
5. **No cheque-bounce / NI Act** — recognised, deadlines flagged as critical, escalated immediately.
6. **Text input only** — no OCR, no photo input, no voice output.
7. **English, Hinglish, Hindi text only** — no other languages.
8. **Limitation periods are unverified** — marked as such until corpus ingestion confirms them.

### Known failure modes
1. **Retrieval misses** — if the query doesn't match corpus vocabulary, relevant sections may not surface. The retrieval gate catches some of these, but not all.
2. **Hinglish ambiguity** — transliterated Hindi mixed with English is noisy. Query transformation helps but isn't perfect.
3. **Category misclassification** — edge cases between consumer/contract/wages may be misrouted.
4. **Bluff library is incomplete** — the simulator can only use bluffs in the library. Real opponents are more creative.
5. **Template rigidity** — templates cover common formats but may not fit every situation.

### Where you need a real lawyer
- Any criminal matter
- Court summons received
- Limitation deadline approaching (< 30 days)
- Domestic violence or personal safety
- Property, inheritance, family law
- Amount above the escalation threshold
- When this system says "I can't ground this"
- When your case is weak and the stakes are high

## AI tool failures during building
(Updated as the build progresses)

- TBD
