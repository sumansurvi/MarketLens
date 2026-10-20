# MarketLens on Vercel: what works and what doesn't

**Site checked:** https://marketlens-lemon.vercel.app/
**Checked on:** 2026-10-20, from outside, by calling every public API endpoint and loading the pages.
**Fixes:** see [`fix.md`](fix.md). §1–§5 of it are implemented on branch `fix/vercel-runtime` (D-101, change request [`CR-001`](docs/change-requests/CR-001-vercel-runtime-fixes.md)). This report describes the site **before** that change was deployed.

## Summary

The website loads, the database connection (Supabase) works, and anything that only reads the database answers. **Every feature that needs market data fails.** That covers research, the ticker search, comparing periods, saved briefs and the watchlist.

The cause is one problem with two symptoms:

1. **The backend isn't running with its Vercel settings.** It reports `app_env: development` and runs research inline, as it does on a laptop. It should report `production` and run research through Vercel Queues.
2. **Because of that, none of the three data services (MCP servers) can start.** Each one tries to create a log folder at `data/runtime/logs`. A Vercel Function can only write to `/tmp`, so the folder can't be created, and the server's startup task stops without recording any error. Every request that needs a server waits 20 seconds and then fails with "The … MCP server is unavailable."

Voice is off for a separate, deliberate reason: the Vercel setup (D-98) has no always-on voice worker. Making it work needs a worker hosted somewhere else (see `fix.md` §6).

## What the live site reports

`GET /api/health` (answers in about 0.6 s):

| Field | Live value | Expected on Vercel |
|---|---|---|
| `app_env` | `development` | `production` |
| `app_mode` / `data_mode` / `llm_mode` | `demo` / `fixtures` / `fixture` | the same (demo is the default) |
| `voice_mode` | `disabled` | `disabled` until a voice worker exists |
| `mcp_servers.*.ready` | `false` for all three | `true` |
| `mcp_servers.*.restarts` | `0` | `0` |
| `mcp_servers.*.last_error` | `null` | `null` |
| `warnings` | `[]` | the "no site password" warning, because `APP_ENV=production` and no password is set |

`ready: false` with `restarts: 0` and no `last_error` means the servers never got as far as starting. A server that started and then crashed would show a restart count and an error.

Other signals:
- The session cookie `ml_session` comes back **without `Secure`**. The backend marks it `Secure` only in production.
- The pages load **without asking for a password**, so `SITE_PASSWORD` isn't set (or hasn't been redeployed).

## Why the backend lacks its Vercel settings

The Vercel settings are applied in [`vercel_backend/__init__.py`](vercel_backend/__init__.py), which runs before the app starts. It sets `APP_ENV=production`, `RUN_EXECUTION=queue` and `MCP_LOG_DIR=-` (server logs go to Vercel's logs instead of a file). It uses `os.environ.setdefault`, so it only fills in a variable that isn't already set.

I reproduced both cases locally with `VERCEL=1`:

| How the backend was started | `app_env` | `run_execution` | MCP log folder |
|---|---|---|---|
| Through `vercel_backend.asgi:app` | `production` | `queue` | none (logs to stderr) |
| Directly from `app.main:app`, or with `.env.example` values set | `development` | `inline` | `data/runtime/logs` (read-only on Vercel) |

The live site matches the second row. I can't see the Vercel logs, so I can't tell which of two things caused it:
- **A. Vercel loads the FastAPI app from `services/backend` directly** and skips `vercel_backend`. The import screen listed a `backend` app under `services/backend` from its own scan, so this is plausible.
- **B. The project's environment variables include values copied from `.env.example`**: `APP_ENV=development`, `RUN_EXECUTION=inline`, and an empty `MCP_LOG_DIR=`. These take precedence over the Vercel defaults. An empty `MCP_LOG_DIR` counts as "use the default folder".

Either way, the fix is the same: the backend should apply its Vercel settings itself whenever it runs on Vercel, whatever the entry point and whatever the environment variables say (`fix.md` §1).

## Why the MCP servers fail without an error

In [`services/backend/app/mcp/client_gateway.py`](services/backend/app/mcp/client_gateway.py), `ServerConnection._supervise` creates the log folder **before** its error handling:

```python
if self.log_path is not None:
    self.log_path.parent.mkdir(parents=True, exist_ok=True)  # read-only on Vercel → OSError
while not self._closing:
    ...
    try:
        ...
    except Exception as exc:
        self.last_error = _describe(exc)
```

On a read-only filesystem the `mkdir` raises, the background task ends, and `last_error` and `restarts` are never updated. The health check then shows exactly what the live site shows. Every request that needs a server waits the full `STARTUP_TIMEOUT_SECONDS` (20 s) before giving up.

## Feature by feature

Legend: ✅ works · ❌ broken by the deployment · ⏸ can't be tried until research works · ➖ off by design.

"Checked" means I called the API behind the feature. UI-only features weren't clicked through in a browser. They depend only on the website, which loads, so they're marked by inspection.

### Starting research

| Feature | Status | What happens on the live site |
|---|---|---|
| A1 Research a company | ❌ | `POST /api/research` → **503 after 20 s**, "The research_sources MCP server is unavailable." (checked) |
| A2 Ticker only (standard question) | ❌ | same as A1 |
| A3 Time window, custom dates | ❌ | the window is set, but the run can't start |
| A4 Quick-start cards | ✅ / ❌ | filling the research bar works; starting fails as in A1 |
| A5 Bad input caught | ❌ partly | the question-length check works (request validation, no server needed). Ticker checks (`AAPL$`, `ZZZZQ`, `DTEGY`) need `research_sources`, so they fail like A1 |
| A6 Class shares (`BRK.B`) | ❌ | needs `research_sources` |
| A7 Prefill from a link (`/?symbol=NVDA`) | ✅ | handled in the browser |
| A8 Ticker dropdown | ❌ | `GET /api/symbols?q=apple` → **503 after 20 s** (checked) |
| A9 Layout of the research bar | ✅ | website only |
| A10 Dictation | ✅ (not tried here) | runs in the visitor's browser, so it doesn't depend on Vercel |

### While a run works, reading a brief, sources, actions

| Feature | Status | Notes |
|---|---|---|
| B1–B4 Progress, steps, cancel, background runs | ⏸ | no run can start. Once the MCP servers start, runs would still be **inline** and could be frozen after each response on Vercel. They need queue mode (`fix.md` §1) |
| C1–C22 Reading a brief | ⏸ | the database has no runs yet (`GET /api/research` → `{"runs":[]}`) |
| D1–D5 Source panel | ⏸ | needs a brief |
| E1 Re-run | ⏸ | needs a run, and then fails like A1 |
| E2 Compare periods | ❌ | `POST /api/compare` → **no answer within 60 s** (checked) |
| E3 Details | ⏸ | needs a run |

### Moving around

| Feature | Status | Notes |
|---|---|---|
| F1 Research history | ✅ | `GET /api/research` → 200 in about 3 s, empty (checked) |
| F1a Search research | ✅ | `GET /api/research/search?q=apple` → 200, empty (checked) |
| F2 Links and refresh (`/?run=…`) | ⏸ | needs a run |
| F3–F6 Old addresses, keyboard, narrow windows, dark mode | ✅ | website only |
| F7 DEMO / LIVE banner, research-only note | ✅ | comes from `/api/health`, which answers |

### Voice

| Feature | Status | Notes |
|---|---|---|
| G1 Voice-session button | ➖ | greyed out with "Voice not configured". `POST /api/voice/session` → 409 `VOICE_NOT_CONFIGURED` (checked) |
| G2 Voice session with Lens | ➖ | needs an always-on voice worker, which the Vercel Hobby setup doesn't include (D-98) |
| G3 Blocked microphone message | ✅ | browser only |

### Safety

| Feature | Status | Notes |
|---|---|---|
| H1, H3–H6 No advice, no made-up numbers, gaps admitted, demo labelled, planted instructions ignored | ⏸ | they act on research output, so they need a working run |
| H2 Nothing written without confirmation | ❌ | the confirmation flow uses `research_workspace`, which is down |

### Backend features without a button in the app

| Endpoint | Status | What happens |
|---|---|---|
| `GET /api/health` | ✅ | 200 in 0.6 s, but reports all three MCP servers down |
| `GET /api/data-status` | ❌ | **no answer within 90 s** (checked) |
| `GET /api/briefs`, `GET /api/watchlist` | ❌ | **503 after about 21 s**, "research_workspace MCP server is unavailable" (checked) |
| `GET /api/analytics/summary` | ✅ | 200, all counts zero (checked) |
| `POST /api/analytics/events` | ✅ | answers, and validates input (checked) |
| `GET /api/docs`, `/api/openapi.json` | ✅ | 200. They're public while no site password is set |

## Other findings

| # | Finding | Impact |
|---|---|---|
| 1 | No `SITE_PASSWORD` | Anyone with the address can use the site. Today that costs nothing (demo mode), but in live mode it would spend your API quotas |
| 2 | Session cookie not `Secure` | follows from `app_env: development` |
| 3 | Research would run inline | a Vercel Function can't reliably keep working after it has answered. Runs could stall at "Running" until the stale-run sweep marks them failed |
| 4 | The research queue consumer is unproven | it's declared in `pyproject.toml` (`[[tool.vercel.subscribers]]`), but research has never reached it. Check after the fixes |
| 5 | A configuration error crashes the whole backend | before the database was added, every `/api` call returned Vercel's generic `FUNCTION_INVOCATION_FAILED`, with no clue for the visitor |
| 6 | Supabase connection | the database works now. Supabase's **direct** connection only works over IPv6, which Vercel can't use, so `DATABASE_URL` must be the **pooler** connection string (port 6543) |

## What I couldn't check

- **Vercel's build and runtime logs.** No Vercel CLI or login was available here. The logs would settle whether the cause is A or B, and would show the `OSError` from `mkdir`.
- **The project's environment variables.**
- **Clicking through the UI in a browser.** UI-only features are judged from the code and the page that loads.
