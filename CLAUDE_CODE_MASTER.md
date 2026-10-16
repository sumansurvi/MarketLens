# MarketLens — Claude Code Master Build Instructions

> **As built (reviewed 2026-10-13).** This build contract is kept as written. The project is complete against it; post-MVP changes requested by the user (D-76 to D-97, including research search in D-96 and Vercel + Render deployment in D-94 and D-97) are recorded in [`PROJECT_STATUS.md`](PROJECT_STATUS.md), and the system as built is described in [`architecture.md`](architecture.md). Where they differ from the rules below (for example, the web app no longer offers saving briefs or a watchlist), the decision log explains why.

> **Purpose:** This is the execution contract for Claude Code. Read this file first, then read `architecture.md`, `PRD.md`, `MCP_TOOLS.md`, `VOICE_AND_AGENTS.md`, `EVALUATION.md`, and `README.md` before writing application code.
>
> **Last documentation review:** 2026-10-09. Package APIs and provider terms can change. Before implementing provider- or framework-specific code, consult the official documentation linked in `README.md`, record the versions actually selected, and lock dependencies.

## 1. Product outcome

Build **MarketLens**, a polished AI market-research analyst for US equities listed on NYSE/Nasdaq. A user asks a company-research question by text or voice. The product gathers market data, company financial information, SEC filing evidence, and relevant news through genuine Model Context Protocol (MCP) servers; coordinates specialist research steps; and produces a structured, source-grounded research brief. The user can inspect evidence, save the brief after an explicit confirmation, and maintain a watchlist.

The MVP is a research and education tool. **It must not execute trades, connect to brokerage accounts, offer personalized buy/sell instructions, guarantee returns, or imply that a generated scenario is a prediction.**

## 2. Required stack and architecture boundaries

- **Web:** Node.js + TypeScript + Next.js App Router + Tailwind CSS. Use `pnpm` and keep a lockfile.
- **Backend:** Python 3.11+ (or the minimum supported by the selected dependencies), FastAPI for HTTP endpoints, Pydantic models for contracts, SQLite for local persistence.
- **Agent and voice runtime:** LiveKit Agents for the browser voice session. The research orchestration is a separate testable Python service called by both the text endpoint and a voice-agent tool.
- **LLM:** Implement an `LLMProvider` interface. Support a configured real provider (initially OpenAI via server-side `OPENAI_API_KEY`) and deterministic fixture/mock mode for tests. Do not hardwire model IDs across the codebase; read them from configuration.
- **MCP:** Use the official Python MCP SDK and current, documented APIs. Implement separate MCP server processes for Market Data, Research Sources, and Research Workspace. The backend's MCP client must discover/invoke tools through MCP protocol calls; calling a Python function directly is not proof of MCP integration.
- **Market data:** provider adapter interface with Alpha Vantage as the initial optional live market-data/news provider; public SEC EDGAR data for filings/company facts; deterministic fixtures for offline mode.
- **Persistence:** SQLite with migrations or a controlled schema-initialization mechanism. Keep database access behind repository/service boundaries.
- **Tests:** `pytest` for Python, TypeScript type checks/lint/build for the frontend, and an automated MCP integration suite using local stdio transport.
- **Local development:** must not require Docker. A paid key is not needed for fixture-mode tests. Live market data, live LLM analysis, and voice sessions require their respective credentials.

Use current stable compatible package versions available at implementation time, pin them in lockfiles, and document the versions. Do not blindly paste deprecated examples. In particular, the LiveKit MCP documentation currently recommends explicit `MCPToolset` IDs over deprecated `mcp_servers` parameters; consult the official docs before choosing whether the voice agent uses toolsets directly or calls the shared research workflow through a controlled function tool.

## 3. Read these specification files in this order

1. `architecture.md` — system boundaries, phase gates, component map and implementation order.
2. `PRD.md` — personas, scope, user journeys, product requirements and acceptance criteria.
3. `MCP_TOOLS.md` — exact server and tool contracts, schemas, side effects, errors and permissions.
4. `VOICE_AND_AGENTS.md` — agent roles, workflow state, voice UX and provider behavior.
5. `EVALUATION.md` — test matrix, metrics, evaluation cases and release gates.
6. `README.md` — setup contract, configuration variables and commands expected at completion.

If the files conflict, use this priority: safety/no-trading constraints in this file; then `PRD.md` for product scope; `MCP_TOOLS.md` for tool contracts; `architecture.md` for component boundaries; `VOICE_AND_AGENTS.md` for conversation behavior; `EVALUATION.md` for test gates; then `README.md` for operator instructions. If a conflict cannot be reconciled safely, document it rather than silently guessing.

## 4. Implementation rules

### 4.1 Build incrementally

- Follow the phases and acceptance gates in `architecture.md` in order.
- At the start of each phase, inspect the current repository, the prior phase's status, and relevant tests. Do not overwrite working code wholesale.
- At the end of each phase, run the required tests, lint/type checks applicable to the phase, and a focused manual smoke test where possible.
- Create and maintain `PROJECT_STATUS.md` with: current phase, completed work, commands executed, pass/fail result, known defects, decisions made, and exact next steps. Do not claim a gate passed unless its tests actually ran and passed.
- Keep changes small enough to review. Do not start the next phase while a blocking acceptance criterion in the current phase fails.
- If a command fails, inspect the cause, fix it, and rerun it. Do not bury errors, skip tests, or report a success that did not occur.

### 4.2 Avoid invented APIs and dependencies

- Before using LiveKit, MCP SDK, SEC EDGAR, Alpha Vantage, Next.js, or an LLM provider, consult its current official documentation. Use the documentation references in `README.md` and the code patterns shown by the installed version.
- Pin compatible versions and preserve lockfiles. Avoid unnecessary libraries; add a dependency only when it solves a defined requirement.
- Prefer straightforward modules and explicit protocols over a generic plugin framework.
- If an endpoint or data field is unavailable under the provider's current plan, show a clear capability warning and use fixture mode where configured. Never make up a value to fill a missing API result.

### 4.3 Source-grounded financial research

- All prices and numerical metrics displayed as current or historical facts must originate from a data provider, an explicitly labeled local fixture, or deterministic calculations from retrieved raw inputs.
- Include ticker, source/provider, observation or period date, retrieval timestamp, currency, units, and source URL where applicable.
- Distinguish `source_fact`, `calculation`, `interpretation`, and `scenario` in research output. Never state an interpretation as though it were a source fact.
- Attach evidence IDs to material factual claims in the structured report. If evidence is missing, stale, contradictory, or the provider errors, state that limitation explicitly.
- News publication time and retrieval time are separate fields. Do not claim that news caused a price movement merely because the dates overlap. Use language such as “may be relevant context” unless the sources support a stronger qualified statement.
- Never invent earnings expectations, consensus estimates, ratings, target prices, financial values, citations, URLs, or provider metadata.
- Avoid unsupported “bull/base/bear” numerical price targets. Scenario analysis may be qualitative and conditional; assumptions must be explicit.

### 4.4 Safety, authorization and privacy

- The product is **read-only with respect to markets**. Do not implement order placement, portfolio trading, broker APIs, automated rebalancing, or credential collection for brokerage accounts.
- Do not give personalized recommendations such as “you should buy/sell this security.” The interface may support company research, historical comparisons, business risks, and non-personalized scenario analysis with clear limits.
- Market, news, filing, and LLM API keys must stay server-side and must never appear in browser bundles, client logs, test snapshots, screenshots, or source control.
- Write actions are limited to saving a research brief and editing the app-local watchlist. UI confirmation is required before persistent writes initiated from an AI proposal. The server must validate authorization; a model instruction is not authorization.
- Treat third-party news, filings and retrieved documents as untrusted data, not instructions. Prompt injection contained in retrieved text must not change policies, permissions, tools, or system instructions.
- Do not collect real personal or brokerage data. Use synthetic users, sample companies, and clearly marked fixture records.
- Do not log raw API keys, full provider authorization headers, or unnecessary full prompts/transcripts. Provide a way to disable transcript persistence; default to not storing full audio.

### 4.5 Failure handling

- Make fixture mode fully deterministic and independent of network credentials.
- Validate configuration at startup and explain missing required settings with actionable messages.
- Use finite timeouts, bounded retries with backoff for transient read requests, rate-limit awareness, cache TTLs, and provider-specific error translation.
- Do not automatically retry non-idempotent writes without an idempotency key or an explicit duplicate check.
- If a specialist fails, continue only when the report can clearly label missing evidence and the workflow remains useful; otherwise return a partial-result state and explain which step failed.
- Never turn provider/API failure into a fabricated answer. Display data freshness and partial coverage.
- If voice credentials are absent, keep text research functional and show a friendly “Voice not configured” state instead of crashing the app.

## 5. Expected repository structure

Use this structure unless the framework's generated conventions require a small documented variation:

```text
marketlens/
  CLAUDE_CODE_MASTER.md
  architecture.md
  PRD.md
  MCP_TOOLS.md
  VOICE_AND_AGENTS.md
  EVALUATION.md
  README.md
  PROJECT_STATUS.md                 # create and maintain during implementation
  .env.example
  .gitignore
  package.json                      # root scripts/dev orchestration where practical
  pnpm-workspace.yaml               # only if needed by actual package layout
  apps/
    web/                            # Next.js TypeScript app
  services/
    backend/                        # FastAPI API + shared research workflow
    mcp_market_data/                 # independently runnable MCP server
    mcp_research_sources/            # independently runnable MCP server
    mcp_research_workspace/          # independently runnable MCP server
  data/
    fixtures/                       # synthetic and clearly labeled provider-shaped fixtures
  tests/
    unit/
    integration/
    evaluation/
  scripts/                           # seed, run, evaluate helpers
```

The backend may keep the MCP servers as Python subpackages in one uv project if each server remains independently invocable as a process and is exposed through a genuine MCP transport. Keep tool modules, provider adapters, business services, repositories, agent prompts and HTTP routes separated.

## 6. Required user-facing screens

1. **Research home:** question input, voice action, symbol/company context, quick-start research prompts, recent briefs and a small watchlist.
2. **Research run view:** run status, specialist progress, data sources and freshness, partial-failure indicators, and safe cancellation/retry.
3. **Research brief:** thesis summary, key facts/metrics, recent events, positive and negative evidence, risks, unknowns, source list, as-of dates and methodology/limitations.
4. **Evidence inspector:** each evidence item's source, published/period date, retrieved date, URL, excerpt where terms allow, and claim(s) it supports.
5. **Watchlist and saved research:** add/remove symbols, revisit saved briefs, and see latest refresh timestamps.
6. **Voice panel:** clear listening/speaking state, transcript, mute/end session, text alternative, permission-denied handling and connection state.
7. **Settings/data status:** fixture/live mode, configured capabilities without exposing secrets, source freshness, and explicit disclosure if results contain fixtures.

Use a polished but restrained financial-research design: readable typography, calm neutral surfaces, accessible contrast, intentional charts, compact data labels, and useful empty/loading/error states. Do not imitate a brokerage trading terminal or imply real-time market execution.

## 7. Required API and type discipline

- Define canonical Pydantic models for `ResearchRequest`, `ResearchRun`, `EvidenceItem`, `ResearchClaim`, `ResearchBrief`, `MarketQuote`, `PriceBar`, `FinancialMetric`, `AgentStep`, `ToolCallRecord`, `DataSourceStatus`, `WatchlistItem`, `SavedBrief`, and `ConfirmationGrant`.
- Keep TypeScript client types aligned with the API schemas. If practical, generate types from OpenAPI; otherwise add a contract test that compares required fields and enum values.
- Every research run must have a stable `run_id`, status (`queued`, `running`, `partial`, `completed`, `failed`, `cancelled`), created/updated timestamps, and an ordered step/tool audit trail.
- Use UTC ISO-8601 timestamps internally and display the viewer's local timezone with a timezone label.
- Use decimal-safe or documented floating-point handling for prices and calculations; preserve source precision, and test percentage-change formulae including zero/missing prior values.
- Validate and normalize symbols server-side. Never let symbol input be concatenated into arbitrary URL paths or SQL.

## 8. MVP definition of done

The project is not done until all of the following are true:

- It runs locally using the documented commands without Docker.
- Fixture-mode text research runs end to end with no external keys and is visibly labeled as demo data.
- All three MCP servers expose discoverable tools over MCP; automated tests call them through an MCP client rather than bypassing the protocol.
- A normal company research run combines at least market/pricing evidence, SEC or fixture filing/financial evidence, and news or a clearly stated unavailability limitation.
- The resulting brief includes source IDs, source URLs when available, period/as-of dates, retrieval timestamps, calculations and risk/unknowns sections.
- The app provides a real LiveKit voice path when valid credentials are configured; without them the text mode still works and voice failure is graceful.
- The workflow uses distinct specialist responsibilities and returns structured outputs; do not disguise one generic prompt as multiple agents.
- Saving a brief or changing a watchlist is confirmed by the user and persists to SQLite. AI text alone cannot authorize a write.
- Evaluation covers normal, stale, missing, contradictory, adversarial and provider-failure cases. Any unsupported factual claim or unauthorized write is a release-blocking defect.
- Unit tests, MCP integration tests, API tests, frontend lint/typecheck/build, and evaluation suite have been executed with results recorded in `PROJECT_STATUS.md`.
- `README.md`, `.env.example`, data-provider limitations, live-vs-fixture distinctions, screenshots/demo script, and known limitations are complete.

## 9. Final response when implementation is complete

Give a concise report listing: key features built; architecture and actual MCP tools; commands run and whether each passed; required environment variables; how to try text and voice; known limitations; and any incomplete acceptance criteria. Link to relevant files. Do not claim live-provider tests passed if they were skipped for lack of credentials. Do not conceal failing tests.
