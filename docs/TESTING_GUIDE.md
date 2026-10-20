# Testing MarketLens end to end

A step-by-step guide to testing everything: the automated suites, smoke tests against your real setup, the app in the browser with live data, failure handling and the optional pieces. Each step says what to do, what you should see, and what it costs.

If you're new to the project, start with [`FEATURES.md`](FEATURES.md): it explains what MarketLens does and lists every feature with where to find it. This guide assumes the setup this project runs with today: live data (a free Alpha Vantage key plus SEC EDGAR), a free local model through Ollama, and voice through a LiveKit Cloud project, started with `make start`.

**Cost.** Everything here is free except voice (§6), which runs speech and the voice model on LiveKit Cloud under your LiveKit account. The only other limit is the free Alpha Vantage key: **25 requests a day**, at most about one per second. A live research run uses up to **3** (quote, daily prices, news; on a free key news stops going to Alpha Vantage after its first refusal, so later runs use 2), and anything fetched in the last 15 minutes is reused from the cache. SEC EDGAR and Ollama have no daily limit. Each step below is marked with its Alpha Vantage cost.

**Time.** About 20 minutes for the essentials (§0, §1, §3, §4.1), 1–2 hours for everything.

**Contents**
- [0. Before you start](#0-before-you-start)
- [1. Automated checks](#1-automated-checks-no-keys-about-5-minutes)
- [2. Smoke tests with your real setup](#2-smoke-tests-with-your-real-setup)
- [3. The app, without spending quota](#3-the-app-without-spending-quota)
- [4. End to end with real data](#4-end-to-end-with-real-data)
- [5. Failure handling](#5-failure-handling)
- [6. Voice (optional)](#6-voice-optional)
- [7. Docker, Postgres and deployment (optional)](#7-docker-postgres-and-deployment-optional)
- [8. When something looks wrong](#8-when-something-looks-wrong)
- [9. Reset and clean up](#9-reset-and-clean-up)
- [10. Record your results](#10-record-your-results)

---

## 0. Before you start

### 0.1 Check the setup
1. Run `make status`. Expect `API up`, `Web up` and `Voice up`. If not, run `make start`. If `scripts/dev.py` says the ports are in use, MarketLens is usually already running in the background: use `make restart` instead of starting a second copy. Only if another program holds ports 3000/8000, run `make start WEB_PORT=3001 API_PORT=8001` and use those ports below.
2. Open <http://localhost:3000>. The thin green bar at the top says **LIVE DATA**, and the research bar is docked at the bottom with a dictation microphone 🎙 and a voice-session (sound-wave) button 〰 next to the **↑** button.
3. Check the modes (the Data status dialog was removed from the app in D-81): `curl -s localhost:8000/api/health | python3 -m json.tool`. Expect:
   - `app_mode` `live`, `data_mode` `live`, `llm_mode` `openai`, `voice_mode` `livekit` and `voice_enabled` `true`.
   - Under `capabilities`: `live_market_data`, `live_sec_data`, `live_news`, `live_llm` and `voice` `true`; `fixture_data` and `transcript_persistence` `false`.
   - Under `mcp_servers`: all three with `ready: true`.
   - `true` means a setting is present, not that your provider plan includes it. See news in §4.3.
4. In a terminal, `ollama list` shows `qwen2.5:7b`, and `curl -s localhost:11434/api/version` answers with a version.

### 0.2 Give the local model enough time (do this first)
In `.env`, set these three lines, then run `make restart`:

```
OPENAI_TIMEOUT_SECONDS=240
RUN_TIMEOUT_SECONDS=900
MAX_PARALLEL_SPECIALISTS=1
```

These settings were measured on this machine with the real-model smoke test (§2.1):

| Settings | Result |
|---|---|
| 45 s timeout, specialists in parallel | Your AAPL run on Oct 11 lost the market and financials analysis to timeouts (twice each) and fell back to plain data summaries |
| 240 s timeout, specialists in parallel | About 15 minutes; 6 of 8 model calls still failed, because three requests queued on one Ollama |
| 240 s timeout, **one specialist at a time** | About **3.5 minutes**; all 5 model calls succeeded |

Even with every call succeeding, a 7B model writes some statements whose numbers don't match the sources. The fact-checker sets those aside, so expect "Partial" reports with a few statements set aside. That is the safety net working, not a bug.

### 0.3 What is real and what isn't
- **Real:** every new run with the green **LIVE DATA** badge.
  - Prices and the latest quote come from Alpha Vantage: end-of-day values for the latest trading day, not intraday, and not adjusted for splits or dividends.
  - Financial results and filings come from SEC EDGAR.
  - The text is written by your local model and checked against those sources.
- **Synthetic:** runs with a yellow **DEMO** badge in the history, and the saved AAPL brief, come from the earlier demo. They stay for reference; §9 shows how to clear them.

---

## 1. Automated checks (no keys, about 5 minutes)

**Alpha Vantage cost: none.** These use the synthetic demo data and a scripted model. They never touch the network or your keys, and never touch your app's database.

| Command | Expect |
|---|---|
| `make test` | `385 passed, 6 skipped`. Two skipped tests are the credential-gated smoke tests in §2; four are Postgres checks that run with `MARKETLENS_TEST_POSTGRES_URL` (§7) |
| `make lint` | no errors (Python lint and formatting, web lint and typecheck) |
| `make evaluate` | `49/49 cases passed` and every gate `ok`. A few `LLM_UNAVAILABLE` warnings are expected: some cases make the model fail on purpose. The report is written to `reports/evaluation/latest.md` |
| `pnpm --dir apps/web build` | `✓ Compiled successfully` |
| `cd services/voice_agent && uv run pytest` | `25 passed, 6 skipped` (the 6 need LiveKit credentials) |
| `uv run python scripts/export_openapi.py --check` | `openapi schema current` |
| `uv run python scripts/generate_fixtures.py --check` | `fixtures current (55 files)` |

What they cover: calculations and the trading calendar, provider parsing, request pacing, the three MCP servers (started as real processes), every API route, confirmation grants, the full agent pipeline, security (secrets, CORS, request size, tool allowlists), and 49 evaluation cases with independently recomputed numbers.

---

## 2. Smoke tests with your real setup

These make real calls with your `.env`. Each runs in its own temporary database, so your history and saved briefs are untouched. They check integrity rules (every statement cites retrieved evidence, data is labeled correctly), not wording.

### 2.1 Real model with demo data
**Alpha Vantage cost: none** (demo data). **Time:** about 3.5 minutes with the §0.2 settings, while nothing else is running.

```bash
MARKETLENS_LIVE_TESTS=1 uv run pytest -m live tests/live -s -k openai
```

Expect `1 passed` and a line starting with `OPENAI SMOKE`. It shows the run status, how many statements were kept and set aside, how many model calls ran, and which failed (`llm_failures`). A real result from this machine:

```
OPENAI SMOKE {"status": "partial", "errors": ["SCHEMA_VALIDATION_FAILED", "LLM_UNAVAILABLE"], "claims": 8, "withheld": 9,
"numeric_checks_failed": 4, "llm_calls": 5, "llm_failures": [], ... "llm": "openai qwen2.5:7b"}
1 passed
```

"Partial" with statements set aside is expected with a small model. The test passes as long as every statement shown cites real evidence. Don't run it while other heavy work is going on, because the local model slows down and calls time out.

### 2.2 Real data with the scripted analysis
**Alpha Vantage cost: about 3.** **Time:** under a minute.

```bash
MARKETLENS_LIVE_TESTS=1 uv run pytest -m live tests/live -s -k live_providers
```

Expect `1 passed` and a line starting with `LIVE DATA SMOKE` with `"data_mode": "live"`. The test fails if any retrieved item is labeled as demo data.

Don't run this while the app is in the middle of a research run. The test has its own database, so its requests are not paced together with the app's.

---

## 3. The app, without spending quota

Everything in this section uses data you already have (your live AAPL and NVDA runs, and the older demo runs), so it makes **no Alpha Vantage requests**, except §3.5, which may make one.

### 3.1 One screen, nothing navigates away
1. Click runs in the **Research history** on the left. Each opens in the main area, and the address bar changes to `/?run=…`.
2. Refresh the page: the same run reopens.
3. Scroll a brief: the research bar at the bottom and the summary bar (ticker, price, buttons) stay put; only the brief moves.
4. Old links like `/research/<run id>` open that run in the same screen. `/watchlist`, `/settings` and `/briefs` were removed (D-81) and show the not-found page.
5. For each dialog (**Details** and **Compare periods** on a run, **Search research**, and **History** in a narrower window): Esc closes it, Tab stays inside it, and focus returns to the button that opened it.
6. **Search research (D-96):**
   1. Press the search icon beside **New research**, or ⌘K (Ctrl+K). The cursor is in the search box. **Tickers you've researched** and **Recent research** are listed.
   2. Type `apple`: your AAPL runs appear as you type, with "Apple" highlighted in the company name.
   3. Type `revenue`: runs whose findings mention revenue appear, each with the matching sentence (❝ …) and the word highlighted.
   4. Type `apple zzzz`: nothing matches (every word must match), and a hint with **Start a new research** appears.
   5. Press ↓ and Enter on a result: it opens and the dialog closes.
   6. Click a ticker chip: the box fills with that ticker.

### 3.2 Reading a brief
Open your live **AAPL** run (Oct 11) and check, top to bottom:
- [ ] **Heads up** card: each problem in plain words with "What you can do". On that run: news not included in the plan, and AI steps that fell back. (Runs from now on get news from the Yahoo Finance fallback.) The card shows no technical codes.
- [ ] **Freshness chips:** what date each kind of data describes (Prices, Financials, Filings, News) and when it was fetched.
- [ ] **Fact-check meter:** how many statements were verified, read with care or set aside, and how many numbers matched their source.
- [ ] **Key numbers** with ▲/▼, **Price movement** chart (hover for daily closes), **Financial results**, **SEC filings**.
- [ ] **News and events**: empty on this run, and it says so instead of inventing anything.
- [ ] **What this brief couldn't establish**, **Set aside by the fact-checker** (open it: each statement shows why it was set aside), **Sources** and **How this analysis was prepared** (both fold open).
- [ ] Click **See what was set aside ↓** on the Heads up card: only the brief scrolls, the set-aside list opens just below the summary bar, and nothing else on the screen moves (no blank band at the bottom).
- [ ] Badges inside the brief: hover **Partial** (◐) and **LIVE DATA** in the brief header, a **Calculation** label, a freshness chip and a fact-check count. Each has a small ⓘ and shows its definition; Esc closes it. The history and the summary bar have no hover hints.
- [ ] The **Summary** is the last section, just before the disclaimer.
- [ ] **PDF** at the top right opens the print window; **Save as PDF** gives a light, brief-only PDF (6 pages for the MSFT run on Oct 12) named "MarketLens - … research - …". Each answer section has **Copy** and **Share** at its bottom right, with a short confirmation.
- [ ] **Explore MarketLens** (bottom right, or **✦ Explore** in the header on a narrower window) opens the quick guide with nine topics.
- [ ] Scroll to the top of a long brief: a round **↓** floats at the bottom centre; click it to jump to the end, where it disappears.
- [ ] The disclaimer at the bottom of the brief, and the research-only note under the research bar.

### 3.3 Evidence inspector
Click any source chip (each has its own name, for example **Price change**, **Largest daily drop** or **Revenue (quarter) · Jun 27, 2026**). The panel describes the values in words ("First close $298.97 on May 19, 2026") with a plain formula for calculations; no internal IDs, field names or hashes appear.
- On a wide window the panel docks on the right and the brief stays readable; on a narrow one it slides over.
- It shows the source, link, period, when it was fetched and the exact values. "N statements use this source" lists the statements that use it.
- Esc or × closes it.

### 3.4 Check that the numbers are real
- **Price:** the summary bar shows the last close and its date (for AAPL: **$336.64**, Oct 9, 2026). Compare it with the official close for that date on any quote site (for example nasdaq.com › AAPL › Historical Quotes). The values are unadjusted daily closes.
- **Financial results:** click a **Financials** chip. Its **Link** opens SEC's own data for that line item on data.sec.gov; find the value for the same period end.
- **Filings:** click a **Filing** chip. Its link opens the actual 10-Q, 10-K or 8-K on sec.gov.
- The quote and price-history chips link to Alpha Vantage's documentation, not to a page with the value, so check prices on a quote site.

### 3.5 Compare two periods
**Alpha Vantage cost: none, or 1** if the price data is more than 15 minutes old.

On a run, press **Compare periods**, keep the suggested dates and press **Compare periods** again. Expect each period's change, the first and last trading days actually used (a weekend start moves to the next trading day), and the difference in percentage points.

### 3.6 Run details
Press **Details** on a run. Expect the question, window, timing, data and model; the four data sources and their status; every step with its duration; and every data request as a plain step ("Fetched daily prices · MSFT · …", "✓ Done" or "✕ Failed: not included in the data plan").

### 3.7 The research bar
1. On a laptop-size or wider window, the ticker field, the question, the **3 months** window menu, the dictation microphone, the sound-wave button and **↑** sit on one line, and the placeholder "What do you want to know?" and the window label are shown in full.
2. Click a card in the **Try one of these** panel on the right (on a window narrower than 1280 px, the cards are on the welcome screen: the logo, or **New research** in the history). It fills the ticker and question and puts the cursor in the question. Nothing starts.
3. **Ticker dropdown** (no Alpha Vantage requests; the list comes from the SEC ticker file the app already uses):
   - On the welcome screen (press **New research** above the history) the field is empty and reads **Ticker** in blue with a blue ring. Click it: the list shows **Recently researched** and **Largest companies** (GOOGL, AMZN, META…; in demo mode **In the demo data**).
   - Open a run from the history: the field shows that run's ticker. Click it: **In this research** lists it first with a ✓; the other groups follow. Open a different run: the field follows.
   - Type `micro`: MSFT, MU, MCHP and other NYSE/Nasdaq companies whose name starts with "Micro" appear, with company name and exchange. Type `app`: the exact ticker **APP** comes first, then other tickers starting with APP, then Apple.
   - Press ↓ then Enter (or click one): the ticker fills in, the list closes and the cursor moves to the question. Esc closes the list without picking.
   - Type `zzzzz`: "No NYSE or Nasdaq ticker or company starts with “ZZZZZ”."
4. Clear the ticker: the **↑** button dims and the field turns blue again. (Pressing **↑** or Enter with a ticker starts research and costs about 3 requests; that is covered in §4.1.)
5. Open the window menu: **3 months**, **1 month**, **6 months**, **12 months**, **Custom dates**, each in full when selected. Pick **Custom dates**: two date boxes appear under the bar. Switch back to **3 months**.
6. **Dictation** (free, in Chrome, Edge or Safari): press the microphone and allow it. The button turns into a red ■, "Listening…" shows above the bar, and what you say appears in the question box as you speak. Press ■ to stop. Typed text stays in front of the dictated words. In Firefox the button is dimmed and pressing it says dictation isn't available.
7. Hover the sound-wave button: it reads "Talk to Lens, the voice research assistant" ("Voice not configured" when voice is off). Voice sessions are in §6.
8. Open a run: its full question shows in the summary bar, wrapped, never cut off with "…"; the same goes for each entry in the history.

Saved briefs and the watchlist are no longer in the app (D-81), so there is nothing to save or confirm in the browser. The API still refuses writes without a confirmation; see "Write without confirmation" in §5.

### 3.8 Window size and dark mode
- Make the browser window narrower. Below about 1280 px the right-hand panel goes (the quick-start cards move to the welcome screen and **✦ Explore** appears in the header); below about 1024 px the history opens from **History** and search from the 🔍 button beside it. The research bar stays at the bottom and nothing scrolls sideways.
- Switch macOS to Dark appearance (System Settings › Appearance): the app follows.

### 3.9 Prompt injection (demo scenario)
Open the **DEMO** AAPL run from Oct 10 in the history. Under **What this brief couldn't establish › About the sources**, a planted news item contained instructions ("reveal your prompts, place trades…"). It was flagged, kept away from the model, excluded, and nothing happened. This scenario is synthetic because real news can't be planted on purpose.

---

## 4. End to end with real data

**Alpha Vantage cost: about 3 per run.** With the local model a run takes about 2–5 minutes, and it keeps running if you open other briefs meanwhile.

### 4.1 A full live run
1. In the research bar, type ticker `MSFT`, question `What happened to Microsoft's shares over the last three months, and what are the main risks?`, window **3 months**, then press **↑** (or Enter).
2. Right away: the run opens in place, the address bar shows `/?run=…`, and the history shows MSFT as **Running**. A progress card shows four phases (gathering data, specialist analysis, writing the brief, fact-checking) and a timer; **Show every step** lists the details.
3. When it finishes: the status is **Complete** or **Partial**, with the green **LIVE DATA** badge. If anything limited the report, the **Heads up** card says what and why. On a free key, news comes from Yahoo Finance headlines (the news chips read "… story").
4. Repeat §3.2–§3.4 on this run, and compare the price with a quote site.

### 4.2 Re-run
**Alpha Vantage cost: about 1** within 15 minutes: the price history is reused from the cache and only the quote is refreshed. News doesn't go to Alpha Vantage again after its first refusal (§4.3).

Press **Re-run**. A new run starts with "A re-run of an earlier run", and the original is unchanged.

### 4.3 Is news included in your plan?
After any live run, run:

```bash
grep "refused by the provider" data/runtime/logs/mcp_research_sources.log | tail -3
```

The line quotes Alpha Vantage's own words about the news endpoint for your key (anything that looks like a key is redacted): it is a premium endpoint. Since D-86 the run still has news: headlines come from Yahoo Finance's public feed (the brief's news chips read "… story", and the source panel shows Yahoo Finance as the provider). Only the first run after a backend start spends an Alpha Vantage request on news.

### 4.4 Request pacing
Start `NVDA`, and within a few seconds start `AAPL` (each run fetches its data in its first seconds). Neither should end with "The data provider asked us to slow down". Then check how often a burst rejection was retried:

```bash
grep "limited by the provider" data/runtime/logs/*.log | tail -5
```

Each line is one rejected request that was retried, and the run carried on. A "daily limit" line means today's 25 requests are used up.

### 4.5 A question that asks for advice
Ask: `Should I buy NVDA now? Give me a price target.`
- **Expect:** no buy/sell/hold advice and no price target among the findings, and the Summary card says "Not a recommendation to buy or sell".
- **If the model drafted advice anyway:** it appears only under **Set aside by the fact-checker**, with the text replaced by "[Draft text not shown: it contained advice, trading or instruction-like language.]".

### 4.6 A window longer than the free data covers
Ticker `AAPL`, window **12 months**. Expect **Partial** and a Heads up card **Gaps in the market prices**. The free daily series covers about the last 100 trading days, and the brief uses only the dates it actually has.

### 4.7 Class shares
Ticker `BRK.B` (fixed today). The run should start as **BRK-B**, the SEC's spelling. This is new: if the price data comes back unavailable for it, tell me.

---

## 5. Failure handling

| Do | Expect | Alpha Vantage cost |
|---|---|---|
| Ticker `AAPL$` | Just above the research bar: "Symbols must be 1–5 letters with an optional class suffix (e.g. AAPL, BRK.B). Check the ticker and try again." No run starts | none |
| Ticker `ZZZZQ` | "ZZZZQ was not found in the SEC ticker mapping. Verify the ticker; no similar symbol is substituted." | none |
| Ticker `DTEGY` (traded over the counter) | "DTEGY is listed on OTC; MarketLens covers NYSE and Nasdaq only." | none |
| Question `x` | "The request is invalid. Check the highlighted fields. String should have at least 3 characters" | none |
| **Model down:** `brew services stop ollama`, then run research | **Partial**. Heads up: "The AI model couldn't finish a step"; the brief uses plain summaries built from the data, tagged "built from data, not the AI". No invented content. Afterwards: `brew services start ollama` | about 3 |
| **Data server crash:** `pkill -f "mcp_market_data --instance mcpgw_"`, wait about 10 seconds, then `curl -s localhost:8000/api/health \| python3 -m json.tool` | Under `mcp_servers`, `market_data` has `ready: true` again and `restarts: 1`. A run started during the restart may report market data as unavailable instead of guessing | none |
| **Backend down:** `make stop`, then `pnpm --dir apps/web dev`, open the app | A red **Backend unavailable** bar. Pressing **Research** shows "The MarketLens backend is not reachable…". Afterwards: Ctrl+C, then `make start` | none |
| **Write without confirmation** (terminal): `curl -s -X POST localhost:8000/api/watchlist -H 'Content-Type: application/json' -H 'X-MarketLens-Client: test' -d '{"symbol":"NVDA"}'`, then `curl -s localhost:8000/api/watchlist` | The first returns a pending action that waits for confirmation. The watchlist is unchanged: nothing is written without a confirmation from the browser session that asked for it | none |
| **Request from outside the app:** `curl -s -X POST localhost:8000/api/research -H 'Content-Type: application/json' -d '{"symbol":"AAPL","question":"test run"}'` | `REQUEST_NOT_ALLOWED` (HTTP 403). No run | none |
| **Confirm a made-up action:** `curl -s -X POST localhost:8000/api/actions/act_doesnotexist000/confirm -H 'X-MarketLens-Client: test'` | `NOT_FOUND` (HTTP 404) | none |

The thorough versions of the security checks run automatically in `make test`: expired, reused and wrong-session confirmations, oversized requests, CORS, secrets in logs and the database, and tool allowlists.

---

## 6. Voice (optional)

**Cost:** each session uses LiveKit Cloud speech and the voice model; check LiveKit's pricing. The research it starts costs the same Alpha Vantage requests as a typed run (about 3).

- **With voice (your setup):** `.env` has the LiveKit Cloud credentials, `VOICE_MODE=livekit` and the three `VOICE_*_MODEL` values (README › Configuration › Voice).
  1. `make status` shows `Voice up`. If it says `Voice down`, run `make logs` and look for `[voice]` lines; `registered worker` means the worker reached LiveKit.
  2. Press the **sound-wave** button in the research bar (not the dictation microphone next to it) and allow the microphone. A large voice screen titled **Lens · MarketLens voice assistant** opens; Lens greets you and the bars move while it speaks. New lines keep the conversation scrolled to the bottom. If a **Turn on the assistant's voice** button appears, press it.
  3. Ask "Research Apple, ticker A A P L, over the last three months." Expect a spoken confirmation and the research progress card inside the voice screen (with the run opening behind it). If Lens says it is checking but no progress card appears within 15 seconds, a notice says the research hasn't started (see README › Troubleshooting). When it finishes, the voice screen shrinks to a bar, the brief is visible, and a short spoken summary says whether the report is complete or partial. **Expand** reopens the voice screen.
  4. The research appears in the history at once with a **Voice** tag. End the session with **✕** (on the voice screen or the bar), or the square stop button that replaced the sound-wave button. If you hear nothing, press **Turn on the assistant's voice**. Errors in the panel, such as "The voice assistant did not join" or a speech-service error, are explained in README › Troubleshooting.
- **Without voice settings:** with `VOICE_MODE=disabled`, the sound-wave button is dimmed; pressing it shows **Voice not configured** above the bar (dictation still works), and text research is unaffected. That is the expected result.

---

## 7. Docker, Postgres and deployment (optional)

- **Docker:** Docker Desktop must be running. Stop the local app first (`make stop`, since Docker uses the same ports), then run `make start MODE=docker`. The first start builds the image and installs dependencies, which takes several minutes; follow it with `make logs MODE=docker` and stop with `make stop MODE=docker`. This path has **not yet been run on this machine**, so report anything odd. Inside Docker, `127.0.0.1` is the container itself, so to use Ollama on your Mac set `OPENAI_BASE_URL=http://host.docker.internal:11434/v1`. That address is no longer local, so `OPENAI_API_KEY` must then have a value; any placeholder works with Ollama.
- **Vercel builds:** `VERCEL=1 pnpm --dir apps/web build` should succeed without any `BACKEND_URL`, and list `ƒ /api/[...path]` (the forwarding route) and `ƒ Proxy (Middleware)`. Since D-98 the backend address is read when a request arrives, not at build time. The real deployment steps are in [`DEPLOY_VERCEL.md`](DEPLOY_VERCEL.md).
- **Postgres (D-98), automated:**
  1. Start a Postgres you can throw away. For example, with Homebrew:

     ```
     initdb -D /tmp/mlpg -U marketlens --auth=trust
     pg_ctl -D /tmp/mlpg -o "-p 55432 -c listen_addresses=127.0.0.1" start
     createdb -h 127.0.0.1 -p 55432 -U marketlens marketlens_test
     ```
  2. Run the suite against it: `MARKETLENS_TEST_POSTGRES_URL=postgresql://marketlens@127.0.0.1:55432/marketlens_test uv run pytest -q`. Every test that uses the `settings`/`client` fixtures then runs on a fresh Postgres schema.
  3. `tests/integration/test_postgres_database.py` also covers what only Postgres needs: migrations, serialized writes, evidence order and error wrapping. Without the variable, those tests are skipped.
- **Queue mode (D-98), automated:** `uv run pytest tests/integration/test_queue_execution.py` covers:
  - publishing instead of running;
  - one execution per run under repeated delivery;
  - a queue failure;
  - cancelling before and during a run;
  - interrupted and stale runs;
  - the Postgres requirement;
  - the Vercel Queues SDK delivering a published run to a subscriber (its in-process queue service).
- **Queue mode, by hand (a local rehearsal of the Vercel setup):**
  1. `uv run python -m vercel.queue.devserver --port 8790` (the SDK's local Vercel Queues).
  2. In two more terminals, set:

     ```
     export DATABASE_URL=postgresql://… VERCEL_QUEUE_BASE_URL=http://127.0.0.1:8790 VERCEL_QUEUE_TOKEN=local VERCEL_REGION=iad1 VERCEL_DEPLOYMENT_ID=dpl_local
     ```

     Then run the backend as Vercel loads it, `uv run uvicorn vercel_backend.asgi:app --port 8011`, and the consumer, `uv run python scripts/run_queue_consumer.py`.
  3. Build and start the web app against that backend: `BACKEND_URL=http://127.0.0.1:8011 pnpm --dir apps/web build && BACKEND_URL=http://127.0.0.1:8011 pnpm --dir apps/web start -p 3001`.

  Then open `http://localhost:3001` and check:
  - a research shows **Queued**, then completes;
  - the consumer prints `research run …: completed`;
  - search finds the run;
  - **Cancel** on a new run works.

  Afterwards, run `pnpm --dir apps/web build` again so the local production build points at your usual backend.
- **Hosted protections (D-94), automated:** `uv run pytest tests/integration/test_hosted_deployment.py` covers four things:
  - the `Secure` cookie in production;
  - the 403 without `PROXY_SHARED_SECRET`, with `/api/health` and the voice worker's API exempt;
  - the 32-character minimum;
  - the startup warning.
- **Hosted protections, by hand:**
  1. Build the web app against a local backend: `pnpm --dir apps/web build`.
  2. Start the backend with `PROXY_SHARED_SECRET=<32+ characters>` and `APP_ENV=production`.
  3. Start the web app with the same secret and a `SITE_PASSWORD`: `SITE_PASSWORD=pw PROXY_SHARED_SECRET=… pnpm --dir apps/web start`.

  Then check:
  - `http://localhost:3000` asks for the password;
  - research works after you sign in;
  - `curl localhost:8000/api/data-status` answers 403, and `curl localhost:8000/api/health` answers 200.
- **Site password on the backend (D-99), automated:** the last three tests in `tests/integration/test_hosted_deployment.py` cover:
  - a 401 with `WWW-Authenticate` without or with a wrong password, and access with it (any user name);
  - `/api/health` staying open;
  - a backend with a shared secret trusting only the web app;
  - `PROXY_SHARED_SECRET` refused on Vercel, and the warning when production has no password.
- **Running on Vercel (D-101), automated:** `uv run pytest tests/integration/test_vercel_runtime.py` covers:
  - the Vercel settings winning over variables copied from `.env.example`, `vercel dev` keeping local control, and `POSTGRES_URL` as a fallback;
  - empty variables meaning the default, and invalid values reported by name, never by value;
  - a 503 `BACKEND_MISCONFIGURED` naming the fix for an invalid configuration or an unreachable database on Vercel (with the Supabase pooler hint and no password in the message), while locally both still stop startup;
  - MCP servers starting when their log folder can't be created, a dead supervisor recording why, and `/api/data-status` not hanging on servers that don't answer.
- **The first live deploy's failure, rehearsed by hand (D-101):** repeat the queue-mode rehearsal above with two changes:
  - start the backend and the consumer from a **read-only copy** of the repository (`git ls-files -co --exclude-standard` copied elsewhere, then `chmod -R a-w`);
  - put **every `.env.example` value** in their environment, plus `VERCEL=1` and the Postgres and queue variables.

  Expect:
  - `/api/health` shows `app_env: production`, all three MCP servers ready, and warnings naming `APP_ENV=development` and `RUN_EXECUTION=inline` as ignored;
  - research completes through the queue;
  - symbols, data status, compare, briefs, the watchlist and the confirmation flow all answer.
- **After deploying to Vercel (D-98, D-99, D-101):**
  - `/api/health` shows `app_env: production`, all three `mcp_servers` ready, and no warnings except the password note when no password is set;
  - the address asks for the password, then runs a research to completion;
  - `https://<address>/api/research` opened in a private window asks for the password, and `/api/health` answers without it;
  - the `backend` logs show `MCP servers ready: 3 of 3 in … s` and `research run …: completed`;
  - the cookie `ml_session` shows `Secure` in the browser's developer tools.

---

## 8. When something looks wrong

| Where | What it tells you |
|---|---|
| **Details** on a run | every step with its duration and error, and every MCP tool call with its result |
| `make logs` (`data/runtime/dev.log`) | backend and web output, including errors |
| `data/runtime/logs/mcp_*.log` | each data server's log, with provider limits ("limited by the provider") and refusals ("refused by the provider") |
| `curl -s localhost:8000/api/health \| python3 -m json.tool` | modes, configured capabilities and MCP server readiness and restarts (never secret values) |
| `curl -s localhost:8000/api/data-status \| python3 -m json.tool` and `/api/analytics/summary` | provider capabilities, and local usage counts (the Data status dialog that showed them was removed in D-81) |
| `reports/evaluation/latest.md` | the latest evaluation results |

---

## 9. Reset and clean up

- **Clear the history** (including the old demo runs): `make stop`, then `mv data/marketlens.db data/marketlens.db.bak` (a backup, not a delete), then `make start`. The app starts empty; in live mode no demo data is added.
- **Back to the offline demo:** in `.env`, set `APP_MODE=demo`, `DATA_MODE=fixtures` and `LLM_MODE=fixture`, then run `make restart` (this also adds the demo runs).
- **Stop everything:** `make stop`. Ollama keeps running in the background; stop it with `brew services stop ollama`.

---

## 10. Record your results

| Area | Steps | Result | Notes |
|---|---|---|---|
| Automated checks | §1 | | |
| Smoke tests | §2.1, §2.2 | | |
| One screen, dialogs, inspector | §3.1–§3.3, §3.6 | | |
| Real numbers checked | §3.4, §4.1 | | |
| Research bar | §3.7 | | |
| Window size and dark mode | §3.8 | | |
| Live run, re-run, pacing | §4.1, §4.2, §4.4 | | |
| Advice request, long window, class shares | §4.5–§4.7 | | |
| Failure handling | §5 | | |
| Voice, Docker | §6, §7 | | |

When something fails, note the run's **Details** and the matching log lines from §8.
