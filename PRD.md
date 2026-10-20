# MarketLens — Product Requirements Document (PRD)

> **As built (reviewed 2026-10-16).** This is the original product brief, kept as written. The MVP is implemented; later product decisions changed some requirements, each recorded in [`PROJECT_STATUS.md`](PROJECT_STATUS.md) and summarized in [`architecture.md`](architecture.md) §2.2:
> - **FR-13, FR-14, FR-21, FR-25** (save a brief, watchlist, data-status screen, watchlist timestamps): removed from the web app on the user's request (D-81). The confirmed-write API, the workspace MCP server and `/api/data-status` still implement them.
> - **Workspace:** one chat-style screen with the research bar at the bottom, a searchable ticker dropdown, quick starts on the right and per-section Copy/Share (D-78, D-81 to D-87); a PDF button instead of the Share menu (D-91); an "Explore MarketLens" quick guide (D-92); search across past research (D-96). Built for desktop and laptop browsers (D-93).
> - **Deployment:** one Vercel project on the free Hobby plan (D-98): web app and internal backend as Vercel Services, research runs on Vercel Queues, Postgres from the Marketplace, an optional shared site password, voice off when hosted. The earlier split with the backend on Render (D-94) remains an alternative. It is still one shared workspace without user accounts.
> - **FR-03 news:** Alpha Vantage news is premium; live mode falls back to Yahoo Finance's public headline feed (D-86). **Freshness:** financial figures come only from the latest reporting period (D-85).
> - **FR-10 voice:** works with LiveKit Cloud and was run with spoken sessions (2026-10-12). The assistant is called **Lens** and has a large voice screen (D-88 to D-90). Dictation in the research bar was added (D-83).

**Version:** 1.0  
**Status:** Approved MVP brief  
**Market:** US equities listed on NYSE/Nasdaq  
**Product type:** AI-assisted equity research workspace with text and voice interaction  
**Primary user:** Curious investor/researcher who needs faster, source-backed company research  
**Last updated:** 2026-10-09

## 1. Executive summary

MarketLens is a voice-first and text-enabled AI market research analyst that assembles a cited company-research brief from market data, company fundamentals, SEC filings, and news. Its value is not a fluent answer alone: it makes source retrieval, evidence quality, uncertainty, calculations, specialist work and limitations visible. It supports repeatable research and saved briefs without executing trades or providing personalized investment instructions.

## 2. Problem statement

A company-research question often requires users to visit multiple sources, reconcile different reporting periods, check whether a recent article is actually relevant to a price move, and manually turn raw facts into a coherent brief. General-purpose chat can produce plausible but unsupported financial narratives, while a chart alone does not explain business fundamentals or news context.

**Product hypothesis:** A research workflow that combines structured market data, filings, dated news, specialist analysis, and evidence validation will help users produce an initial research brief more efficiently and with clearer awareness of what is known, unknown, stale, or contradictory.

This hypothesis must be evaluated with repeatable test scenarios and user feedback; the project must not claim measured time savings before measuring them.

## 3. Product vision and positioning

**One-line positioning:** “Ask a question. Get a research brief you can verify.”

**Product promise:** MarketLens organizes available evidence and explains it with citations, date labels, and explicit uncertainty, through either text or voice.

**Differentiator:** The UI exposes the actual research steps, MCP tool calls, retrieved evidence and validation outcomes rather than hiding them behind a single chat answer.

## 4. Personas

### Persona A — Independent market researcher (primary)

- Follows a small set of US-listed companies.
- Wants a concise but verifiable view of recent company and market developments.
- Is comfortable reading figures but does not want to manually collect every source.
- Needs clear dates, units, citations and caveats before using a report in their own research.

**Needs:** consistent company briefing, historical comparisons, source traceability, clear unknowns, ability to ask follow-up questions.

### Persona B — Busy professional using voice

- Wants to ask questions while multitasking and then inspect the full report on screen.
- Often starts with an imprecise request and follows up with “compare to last quarter,” “what evidence supports that?” or “what is missing?”

**Needs:** natural conversational clarification, persistent ticker/report context within a session, short spoken summaries and a full written evidence view.

### Persona C — AI PM / interviewer (evaluation persona)

- Wants to inspect product judgment and implementation rather than accept marketing claims.
- Looks for a clear problem, measured outcomes, agent boundaries, tool integration, evaluation strategy, safety gates and thoughtful trade-offs.

**Needs:** visible architecture, real MCP integration, repeatable evaluation results, reproducible demo and honest limitations.

## 5. Jobs to be done

- “When I ask why a company has been in the news, help me assemble a dated evidence trail rather than assert an unverified cause.”
- “When I research a company, combine price history, fundamentals, filing context and news without hiding unavailable data.”
- “When I ask by voice, let me clarify or correct the research question naturally and inspect the results afterward.”
- “When a report is useful, let me save it and revisit it with its original as-of dates and sources.”

## 6. MVP scope

### In scope

1. US-listed company symbol lookup/validation for the initial provider coverage; fixture coverage for a small demo universe such as AAPL, MSFT and NVDA.
2. Text-based research requests and reports.
3. Optional browser-based voice interaction through LiveKit Agents when configured.
4. Genuine MCP client/server integrations with three independently runnable servers: market data, research sources and workspace.
5. Data retrieval for available price history/quotes, company financial metrics and SEC filing/company facts, plus recent news where provider access is available.
6. Deterministic calculations such as percent change and period comparison.
7. Specialist research tasks: market, news/events, fundamentals, evidence/risk review, coordinated by a supervisor.
8. Structured reports with source IDs/URLs, published/period dates, retrieval timestamps, data mode, positive and negative evidence, risks, unknowns and methodology.
9. Evidence inspector and visible agent/tool execution trace.
10. SQLite-backed saved briefs and watchlist with explicit confirmation for persistent writes.
11. Fixture mode for deterministic tests and no-credential demo.
12. Evaluation harness, documented failure behavior and basic product metrics.

### Out of scope for MVP

- Placing trades, routing orders, broker account access, trading algorithms or auto-rebalancing.
- Personalized investment advice, suitability assessment, portfolio allocation, guaranteed forecasts, or unqualified buy/sell ratings.
- Options, crypto, forex, fixed-income, private-company valuation, global equities outside the chosen MVP coverage, and real-time exchange-grade data guarantees.
- Multi-user organization tenancy, enterprise SSO, role-based billing, customer-managed keys or regulated institutional deployment.
- Paid-news redistribution or persistent storage of full copyrighted article text.
- Push/email/SMS market alerts in the first MVP unless the complete background job and delivery path is implemented and tested.
- Arbitrary web browsing, autonomous shell execution, generic filesystem tools, or unbounded web-fetch tools exposed to agents.

## 7. Core user journeys

### Journey 1 — Company research by text

1. User enters a ticker (e.g. AAPL) and asks: “Review the last three months of movement, recent news, financial performance and key risks.”
2. UI confirms normalized symbol and data mode, and starts a run.
3. The supervisor plans evidence categories and runs market, news and fundamentals tasks.
4. MCP servers retrieve data and return normalized source metadata.
5. Specialists produce typed findings; evidence reviewer checks claims against evidence.
6. UI displays progress and then a structured brief with source links and “as of” labels.
7. User opens a claim/source in the evidence inspector and sees source, date, excerpt/data and retrieval timestamp.
8. User can request a follow-up or save the report after a confirmation click.

### Journey 2 — Voice research

1. User enables microphone and starts a LiveKit session.
2. User asks: “Give me a research briefing on Nvidia and explain what changed recently.”
3. Agent clarifies the timeframe if essential; otherwise it uses a clearly stated default timeframe.
4. Voice agent invokes the shared research workflow and gives progress feedback.
5. UI receives the transcript, research steps and cited brief; the agent speaks a short summary, not an unbounded full report.
6. User asks “What are the strongest risks?” and the agent answers from the current brief/evidence context.
7. User ends the session; voice errors do not erase the report or disable text interaction.

### Journey 3 — Save report and maintain watchlist

1. User clicks “Save brief.”
2. UI shows the report title, ticker, creation time, data mode and a confirmation action.
3. Backend validates the pending action and issues/consumes a one-time, expiring, session/run-bound grant.
4. The backend invokes the workspace write through MCP; the server verifies the grant and persists the record.
5. UI reports the actual saved brief ID and allows reopening the saved record.
6. Watchlist add/remove actions follow server-side validation and show success/failure.

### Journey 4 — Missing/contradictory data

1. A provider endpoint is unavailable, a source is stale, or news and price periods do not align.
2. Workflow records an explicit step-level failure or contradiction.
3. Final report says what evidence could be retrieved, what could not, and which claims were withheld or qualified.
4. The app never silently fills missing fields with fabricated data or disguises stale fixtures as live information.

## 8. Functional requirements

| ID | Requirement | Priority | Acceptance evidence |
|---|---|---|---|
| FR-01 | User can submit a ticker and natural-language research question. | P0 | API/UI test for valid and invalid ticker. |
| FR-02 | App validates and normalizes symbol input server-side. | P0 | Unit tests; invalid input returns a clear 4xx. |
| FR-03 | App gathers market, company/filing and news evidence where available. | P0 | Fixture integration test and live-provider test when keys exist. |
| FR-04 | All provider results retain source, date, retrieval timestamp and fixture/live status. | P0 | Schema/API tests. |
| FR-05 | Three real MCP servers expose typed tools and can be called through MCP. | P0 | MCP tool-list/call integration suite. |
| FR-06 | Supervisor coordinates at least three specialist tasks and a reviewer. | P0 | Orchestration trace/evaluation test. |
| FR-07 | Material factual claims link to evidence IDs; numerical values are verified. | P0 | Evidence-validation test. |
| FR-08 | Missing/stale/conflicting sources are identified in report. | P0 | Evaluation cases. |
| FR-09 | Text mode works without voice credentials and in fixture mode without paid APIs. | P0 | Clean-environment smoke test. |
| FR-10 | Voice mode works with valid provider credentials. | P1 | Credentialed manual end-to-end test. |
| FR-11 | Voice agent calls shared workflow; no duplicate market logic is maintained. | P0 | Service-level test and architecture inspection. |
| FR-12 | User can inspect source metadata and supporting evidence. | P0 | UI test/manual demo. |
| FR-13 | User can save a brief after explicit confirmation. | P0 | Negative tests for absent/expired/reused grant. |
| FR-14 | User can add/remove list entries and view saved briefs. | P1 | Persistence/API/UI tests. |
| FR-15 | Run progress, steps, errors and cancellation status are visible. | P1 | Workflow tests. |
| FR-16 | User sees live-vs-fixture mode and latest data timestamps. | P0 | UI test. |
| FR-17 | App records safe tool/agent traces with latency and result status. | P0 | Audit-log tests without secrets. |
| FR-18 | The interface explicitly describes the research-only/no-trading nature of the product. | P0 | UI content test/manual verification. |

## 9. Non-functional requirements

- **Reliability:** deterministic tests run offline with no external credentials.
- **Correctness:** numerical outputs use deterministic formulas and source-derived inputs; every material factual claim links to evidence or is explicitly marked unverified.
- **Latency:** record total run time and per-step latency; do not promise a fixed response time until measured. Use progress events for long runs.
- **Safety:** zero unauthorized writes in test suite; no trading tools or brokerage credentials.
- **Security:** server-side secrets only, allowlisted MCP tools, bounded timeouts/retries/concurrency, request validation and safe error handling.
- **Accessibility:** keyboard navigable, clear focus, contrast, transcripts and a text interaction alternative.
- **Maintainability:** provider adapters, research workflow, tool contracts, repositories, voice adapter and UI remain independently testable.
- **Observability:** each research run has a stable ID; record status, source coverage, tool calls, errors and step timing.
- **Transparency:** source mode, dates, units, provider restrictions and limitations are visible.

## 10. Research brief content contract

Every report should contain:

1. **Header:** company/ticker, run timestamp, report data mode, latest observation/as-of dates.
2. **Executive summary:** concise and balanced; not a buy/sell recommendation.
3. **Observed market movement:** period, starting/ending values, percentage change, and any limitations around volume/adjustments.
4. **Fundamentals:** reported values with units, currency, reporting period and comparative basis.
5. **Recent events/news:** headline/source, publication timestamp, retrieved timestamp, relevance note and evidence ID.
6. **Positive evidence and negative evidence:** clearly separated, source-linked.
7. **Risks and unknowns:** including missing data categories and uncertainty around causal attribution.
8. **Researcher interpretation:** explicitly labeled as analysis rather than source fact.
9. **Optional scenarios:** qualitative/conditional with stated assumptions; no guaranteed outcome or unsubstantiated target price.
10. **Sources and methodology:** URLs, source names, publication/period dates, retrieved timestamps and deterministic calculations performed.
11. **Disclaimer:** informational research, not personalized financial advice; no trade execution.

## 11. Error and empty states

- **Unknown symbol:** ask user to verify the ticker; do not guess a similar security silently.
- **No data for date range:** explain provider coverage and offer a narrower/available interval.
- **Provider rate limit:** show which source is delayed/unavailable, cache status if used, and retry guidance.
- **SEC mapping/fetch issue:** preserve other evidence, flag filing data unavailable, and avoid pretending financial metrics were SEC-verified.
- **LLM failure:** return a safe error or a basic deterministic data summary only if implemented and clearly labeled; do not fabricate a full report.
- **Specialist timeout:** partial report with missing category and failed step.
- **Voice not configured:** text research remains active with setup instructions.
- **Microphone denied/disconnected:** give retry and text alternatives.
- **Save action failure:** preserve the report in the UI and explain that it was not saved.

## 12. Product metrics and hypotheses

Track a small metric tree instead of collecting many vanity measures:

- **North-star candidate:** percentage of started research runs that result in a user-inspected, successfully completed or clearly partial research brief.
- **Workflow success:** run completion rate; median/95th percentile duration; user cancellation rate; provider coverage rate.
- **Evidence quality:** percentage of material factual claims with valid evidence references; fixture numeric accuracy; citation URL integrity; stale-source disclosure rate; unsupported-claim rate.
- **Tool quality:** correct tool selection and argument accuracy on evaluation cases; MCP error rate; duplicate write rate; unauthorized write rate.
- **Voice quality:** successful session connection rate, speech turn completion, interruption recovery, voice-to-report completion and time to first response.
- **Engagement:** saved brief rate, follow-up question rate, watchlist return usage.

Do not send personal data or raw audio to analytics. Label these as measured values only after an instrumented run or user study. A case study may report test-set results but must identify them as synthetic evaluation, not real customer impact.

## 13. MVP release checklist

- [ ] Complete text research report in fixture mode.
- [ ] Three MCP servers discovered and called through protocol.
- [ ] Live provider adapters have explicit error/rate/coverage handling.
- [ ] SEC filing/company data source links are preserved.
- [ ] Structured evidence, claims and reviewer outcome shown in UI.
- [ ] Voice-to-research end-to-end works when configured; text-only fallback works without voice.
- [ ] Save brief and watchlist writes require server-validated confirmation.
- [ ] No broker/trading integration or personalized buy/sell logic exists.
- [ ] Mandatory evaluation suite passes; report stored.
- [ ] README and `.env.example` match actual code and commands.

## 14. Implementation traceability addendum (Phase 0, 2026-10-09)

Phase 0 cross-checked `CLAUDE_CODE_MASTER.md` §6 (screens) and `architecture.md` (Phase 4–5 scope) against this PRD. The features below were named there but missing from §8, so they are recorded here so the PRD stays the single list of MVP features. They add no scope beyond those documents.

| ID | Requirement | Priority | Source | Acceptance evidence |
|---|---|---|---|---|
| FR-19 | Retry a failed/partial run and re-run a completed brief's research with the same parameters as a new run; the original run and any saved brief stay unchanged. | P1 | master §6.2, architecture Phase 5 | API/workflow test |
| FR-20 | Compare price movement across two user-selected periods using deterministic calculations with the actual observation dates used. | P1 | architecture Phase 5, PRD §6 item 6 | Calculation unit tests + UI check |
| FR-21 | Settings/data-status screen: data/LLM/voice mode, configured-capability booleans (never secrets), provider freshness and fixture disclosure. | P0 | master §6.7 | API/UI test |
| FR-22 | Quick-start research prompts on Research home. | P2 | master §6.1 | UI check |
| FR-23 | Local product-analytics events (run start/completion, report save, watchlist change, voice connection success/failure, source-category success/failure) without personal data, secrets or audio. | P1 | architecture Phase 5, PRD §12 | Analytics test asserting no secrets |
| FR-24 | Transcript persistence is configurable and off by default; raw audio is never stored. | P0 | master §4.4, VOICE §7 | Config/unit test |
| FR-25 | Watchlist shows each symbol's latest research/refresh timestamp. | P2 | master §6.5 | UI check |
