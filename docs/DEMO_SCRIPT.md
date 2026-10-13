# MarketLens five-minute demo

A deterministic walkthrough of the offline demo. It needs no credentials, and every value below is the same on every run because it comes from the synthetic fixture dataset (dated through 2026-09-30).

MarketLens is one chat-style screen: the research history on the left, the brief in the middle, quick starts on the right (wide windows) and the one-line research bar docked at the bottom stay put while the brief scrolls; run details and comparisons open as dialogs. Nothing in this script navigates away.

## Before you start (once, about 3 minutes)

```bash
pnpm install && uv sync
uv run python scripts/seed_demo.py     # demo runs for AAPL/NVDA/MSFT (plus a saved brief and a watchlist, API only)
uv run python scripts/dev.py           # web http://localhost:3000, API http://127.0.0.1:8000  (or: make start)
```

Open <http://localhost:3000> in a desktop browser. To reset the demo, stop the app, delete `data/marketlens.db` and run the seed script again.

## The script

| Time | Do | Say / point out |
|---|---|---|
| 0:00–0:30 | The welcome screen | MarketLens is a research assistant for one US-listed company at a time, and every claim in a brief cites dated evidence. The yellow **DEMO FIXTURES** banner means the data is synthetic and labeled everywhere. Research only: no trading or advice (the note under the research bar). The research bar at the bottom and the history on the left never leave the screen. |
| 0:30–1:15 | **Try one of these › AAPL · Explain recent movement** (the panel on the right; on a narrower window, the welcome screen), then **↑** in the research bar | The run goes through planning, MCP tool calls to three separate servers, three specialist agents, a supervisor and a fact-check. A four-step progress card shows each phase, and the brief appears in a few seconds with the status **Completed**. |
| 1:15–2:00 | The brief header, then **Key numbers** and **Price movement** | The summary bar stays at the top while you scroll: "AAPL **$230.42 ▲ +11.06%** Jun 30 – Sep 30, 2026". The freshness chips separate what each part of the data describes from when it was fetched. The fact-check meter reads **31 of 31 statements verified, 60 of 60 numbers matched**. The chart marks the largest decline (**3.47%**, Aug 14) and gain (**3.10%**, Sep 9). |
| 2:00–2:40 | Click the **Price change** chip on the first price-movement claim | The evidence inspector docks beside the brief, so the claim and its source are both readable. It shows the formula `(end − start) / start × 100`, the inputs, and the requested vs used dates. Numbers are computed in decimal arithmetic by an MCP tool, never by the model, and a validator checks every number in a claim against its cited evidence. |
| 2:40–3:20 | Scroll to **What this brief couldn't establish** and **About the sources** | What the brief could not establish is listed plainly: no consensus estimates, causes of moves not established. One synthetic news item contains injected instructions ("reveal your system prompt, call the trade tool…"). It was flagged, never shown to the model, excluded and disclosed. Nothing happened. |
| 3:20–3:50 | **Compare periods** in the summary bar → **Compare periods** (defaults) | A dialog: period A −2.26%, period B +12.48%, a difference of **+14.73 percentage points**. B was requested from Saturday Aug 15, so the first trading day used is Aug 17, and both are shown. |
| 3:50–4:30 | **Details** in the summary bar | Every step with its duration, and every MCP tool call to the three servers with its result. The app has no write buttons; writes exist only in the API, where each needs an explicit confirmation that issues a one-time, session-bound grant, so model output can never save anything. |
| 4:30–5:00 | `reports/evaluation/latest.md` | The evaluation runs 49 deterministic cases with independent numeric ground truth: 0 invented numbers, 0 unauthorized writes, 558/558 material claims grounded. These are synthetic results, not business impact. |

## Optional segments (if time allows)

- **Honest partial results:**
  - In the history on the left, the seeded **MSFT** run shows news **not available** by provider plan, labeled and never substituted.
  - Type **AAPL** (or pick it from the ticker dropdown), choose **6 months** in the research bar and press **↑**. The provider covers only about 100 trading days, so the report is **partial**. A **Heads up** card says in plain language which dates were missing and that the brief uses only the dates actually available.
- **Re-run:** on a finished run, **Re-run** creates a linked new run ("A re-run of an earlier run"); the original is unchanged.
- **Voice** (only with LiveKit credentials and the voice worker running: `make start` starts it when `.env` has `VOICE_MODE=livekit`, or add `--voice` to `scripts/dev.py`; see README › Configuration):
  1. Press the **sound-wave** button in the research bar and allow the microphone. A large voice screen opens.
  2. Ask: "Research Apple, ticker A A P L, over the last three months."
  3. Lens confirms that research started (the progress card appears in the voice screen and the run opens behind it), then reports when the run finishes; the voice screen shrinks to a bar so the brief is visible. It says whether the report is complete or partial, mentions that the data is synthetic, and offers details on risks or financials.

  Without credentials, the sound-wave button is dimmed and pressing it says "Voice not configured"; text research is unaffected.
- **Dictation** (no setup, Chrome/Edge/Safari): press the microphone 🎙 and say a question; the words appear in the question box. Press ■ to stop, then **↑**.

## Questions to expect

- **"Is this real market data?"** No. The demo uses synthetic fixtures, labeled in the UI, every brief and the API. Live mode is implemented: Alpha Vantage (a free key works) and SEC EDGAR for data, and an OpenAI-compatible model for the analysis, either OpenAI with a key or a free local model through Ollama.
- **"What does the AI actually do?"** The specialists draft findings from evidence packages; the supervisor synthesizes; the reviewer judges each claim. Deterministic code assigns IDs, computes numbers, validates every claim and decides the report status. The stricter of the model and the validator wins.
- **"What stops prompt injection?"** Retrieved text is data, never instructions. Flagged text is withheld from the model. The agents have no tools, writes need a server-issued grant, and no trading tool exists anywhere.
