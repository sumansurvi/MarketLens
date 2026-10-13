# MarketLens evaluation report

- Generated: 2026-10-13T10:08:22Z · app 0.1.0 · commit `122bc5f-dirty`
- Mode: **fixtures data, fixture LLM** (synthetic fixtures; deterministic, no network or credentials). This measures system behavior, not market or business outcomes.
- Cases: **49/49 passed**; safety-critical: 12 (0 failed); wall time 22.8 s

## Release gates (EVALUATION.md §3, computed per §11)

| Metric | Value | Target | Result |
|---|---:|---:|---|
| case pass rate | 1.0 | 1.0 | pass |
| fixture numeric accuracy | 1.0 | 1.0 | pass |
| material claim grounding | 1.0 | 1.0 | pass |
| unauthorized writes | 0 | 0 | pass |
| invented numbers | 0 | 0 | pass |
| injection policy violations | 0 | 0 | pass |
| missing category labeling | 1.0 | 0.95 | pass |
| tool accuracy | 1.0 | 0.95 | pass |
| invalid input blocking | 1.0 | 1.0 | pass |

Numeric checks: 5115 (failures 0); material claims grounded 558/558; missing-category labeling 9/9; invalid inputs blocked 13/13; unauthorized or misused write/voice attempts rejected 15/15; tool-scored cases 8.

## Cases by category

| Category | Cases | Passed | Planned |
|---|---:|---:|---:|
| calculations | 6 | 6 | 6 |
| missing stale | 7 | 7 | 6 |
| normal research | 8 | 8 | 8 |
| tool schema | 7 | 7 | 5 |
| contradictions | 4 | 4 | 4 |
| injection policy | 5 | 5 | 4 |
| voice | 6 | 6 | 4 |
| write authorization | 6 | 6 | 5 |

## Latency and calls (fixture mode, measured)

- Research runs measured: 26; run duration p50 168.5 ms, p95 179.0 ms
- Median MCP tool calls per run: 10.0; median LLM calls per run: 5.0
- Step latency p50 (ms): calculations 2, collect_evidence 120, evidence_review 17, finalize 3, fundamentals_specialist 1, market_specialist 1, news_specialist 0, plan 0, supervisor_synthesis 1
- Cost: not measured (fixture LLM; no metered usage).

## Failed cases

None.

## Not run in this report

- Live provider, real-model (OpenAI) and real-speech (LiveKit) checks: credential-gated; see `PROJECT_STATUS.md`.
- Conversational voice behavior with a real model (clarifying questions, refusals) is covered by credential-gated tests in `services/voice_agent/tests/live`.
