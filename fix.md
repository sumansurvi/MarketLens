# Fix plan: make MarketLens work on Vercel as intended

This fixes everything in [`report.md`](report.md). The aim: on Vercel, research, the ticker search, compare periods, briefs, the watchlist and the confirmation flow all work, and voice works once a voice worker is hosted.

The plan has three parts:
- **Code changes (§1–§5):** the backend applies its own Vercel settings and can't fail silently. They work whichever of the two causes in the report applies.
- **Vercel dashboard changes (§6):** clean up the environment variables and add the site password.
- **Voice (§7):** needs a voice worker hosted outside Vercel, or Vercel's Pro plan.

§9 lists the checks that prove each feature works after the redeploy.

> **Status (2026-10-20):** §1–§5 are done on branch `fix/vercel-runtime` (decision D-101, change request [`CR-001`](docs/change-requests/CR-001-vercel-runtime-fixes.md)).
> - **Tests:** 14 new tests; the full suite has 385 passed, 6 skipped.
> - **Rehearsal:** the live failure was replayed locally: a read-only copy of the repository, every `.env.example` value in the environment, Postgres and the local Vercel Queues server. Every feature in [`report.md`](report.md) then worked; voice answered "not configured", as designed.
> - **Differences from the plan:**
>   - `/api/health` uses the standard error envelope (`BACKEND_MISCONFIGURED`), so the site needed no special case;
>   - data-status waits at most 8 s per server, not 5;
>   - `vercel_backend` also sets `VERCEL=1` for projects that hide Vercel's system variables;
>   - a database the backend can't open at startup is reported the same way.
> - **Still yours:** §6 (Vercel dashboard), §7 (voice) and the §9 checks after the redeploy.

---

## 1. Apply the Vercel settings in the backend itself

**Problem:** the Vercel settings live in `vercel_backend/__init__.py` as `os.environ.setdefault(...)`. They're lost when Vercel loads `app.main:app` directly, and overridden when the project has `APP_ENV`, `RUN_EXECUTION` or an empty `MCP_LOG_DIR` from `.env.example`.

**Change:** in [`services/core/marketlens_core/config.py`](services/core/marketlens_core/config.py):

1. Add `env_ignore_empty=True` to `Settings.model_config`, so an empty variable such as `MCP_LOG_DIR=` counts as unset, the way `.env.example` intends.
2. Add a helper, `on_vercel()`: `VERCEL` is set and `VERCEL_ENV` isn't `development`, so `vercel dev` keeps local control.
3. In `load_settings`, when running on Vercel, **enforce** the hosted profile instead of only defaulting it:

   | Setting | Value on Vercel | Why it can't be changed there |
   |---|---|---|
   | `.env` file | never read | there is no `.env` on Vercel |
   | `MCP_LOG_DIR` | `-` (stderr) | only `/tmp` is writable, and stderr reaches Vercel's logs |
   | `RUN_EXECUTION` | `queue` | a Function can't keep a run going after it has answered |
   | `APP_ENV` | `production` | `Secure` cookie, production warnings |
   | `DATABASE_URL` | falls back to `POSTGRES_URL` if unset | some Postgres integrations set only `POSTGRES_URL` |

   When a project variable tries to set something else, log one warning naming it ("APP_ENV=development is ignored on Vercel; remove it from the project's environment variables"). Don't fail, so the site keeps working.
4. Keep `vercel_backend/__init__.py` for what only it can do: the import paths, and `VERCEL=1` for projects that hide Vercel's system variables. Remove its other `setdefault` lines, so the rules live in one place.

**Result:** the backend runs with the right settings whether Vercel loads `vercel_backend.asgi:app`, `app.main:app` or the queue consumer, and whatever was pasted into the project's variables.

## 2. MCP servers: never fail silently

**Problem:** `ServerConnection._supervise` in [`services/backend/app/mcp/client_gateway.py`](services/backend/app/mcp/client_gateway.py) creates the log folder before its `try`. An `OSError` there ends the task, and `ready`, `restarts` and `last_error` never change.

**Change:**
1. Move the `mkdir` and the log-file `open` inside the `try`. If either fails, record `last_error`, log a warning, and fall back to stderr for that server, so the server still starts.
2. Add a done-callback on the supervisor task. If the task ever ends unexpectedly, it logs the exception and stores it in `last_error`.
3. `GET /api/health` then always explains a server that isn't ready.

## 3. Show configuration problems instead of crashing

**Problem:** a configuration error, such as a missing database or a missing live-mode key, makes the module import fail. Every `/api` call then returns Vercel's generic `FUNCTION_INVOCATION_FAILED`, and the site just shows "Backend unavailable".

**Change:**
1. In [`vercel_backend/asgi.py`](vercel_backend/asgi.py) (and `app.main._app_from_environment`), catch `ConfigError` and serve a small fallback app instead:
   - every `/api/*` request, `/api/health` included → `503` with the standard error envelope: code `BACKEND_MISCONFIGURED`, message "Setup problem: <first problem>", and all problems in `details.problems`. The problems name the variable to fix and never contain secret values.
   - A database that can't be opened at startup gets the same answer, with the reason (credentials removed) and the Supabase pooler hint when the URL shows Supabase's direct host.
2. Web app: the red bar already shows the backend's error message, so it reads "Backend unavailable. Setup problem: …". When the backend answers without JSON, the bar no longer tells hosted visitors to start `uvicorn`.

## 4. Endpoints that wait too long

1. `GET /api/data-status` asks two MCP servers one after the other, and each can wait 20 s for its server, so it can take 40 s or more. Ask both at the same time (`asyncio.gather`), each bounded to 8 seconds.
2. Keep the 20-second wait for research tool calls, because a cold start launches three servers. Log how long startup took (`MCP servers ready: 3 of 3 in N s`), so cold starts are visible in Vercel's logs.

## 5. Tests

Add these to `tests/`:
- **Vercel profile:** with `VERCEL=1` and `APP_ENV=development`, `RUN_EXECUTION=inline`, `MCP_LOG_DIR=` set, `load_settings()` gives `production`, `queue` and stderr logs. Loading through `app.main` gives the same result as through `vercel_backend`.
- **Read-only log folder:** with an unwritable log folder, each MCP server still starts and logs to stderr, and if it can't start, `last_error` is set.
- **Fallback app:** a `ConfigError` gives `503` with the problems on `/api/health` and `BACKEND_MISCONFIGURED` elsewhere, never `500`.
- **Existing suites:** `uv run pytest -q`, ruff, and web lint and typecheck stay green.

## 6. Vercel dashboard (you)

### 6.1 Environment variables

Open **Settings › Environment Variables** and make them match this table. Anything copied from `.env` or `.env.example` that isn't listed below should be deleted. Local-only values such as `BACKEND_HOST`, `BACKEND_PORT`, `FRONTEND_ORIGIN`, `MCP_TRANSPORT`, `FIXTURE_DIR` and `VOICE_BACKEND_URL` don't belong in the project.

| Variable | Value | |
|---|---|---|
| `DATABASE_URL` | Supabase **Transaction pooler** string (port **6543**, host `…pooler.supabase.com`) | required. Not the direct `db.….supabase.co` one, which is IPv6-only |
| `SITE_PASSWORD` | a password you choose | strongly recommended |
| `RUN_TIMEOUT_SECONDS` | `120` (demo) to `280` | must stay below the 300 s function limit on Hobby |
| `APP_ENV`, `RUN_EXECUTION`, `MCP_LOG_DIR` | **delete them** | the backend sets them on Vercel and ignores these; `/api/health` `warnings` names any that are still there |
| `PROXY_SHARED_SECRET` | **must not exist** | the backend refuses to start with it on Vercel |

Then **Deployments › ⋯ › Redeploy**. Variable changes need a redeploy.

### 6.2 Project settings and the build log

1. Open **Settings › Build and Deployment**. The preset must be **Services**, Root Directory `./`, and no **Override** toggles turned on.
2. Open the latest deployment's **Build Logs** and find the `backend` service. Note which entry point it uses: `vercel_backend.asgi:app` (intended) or something under `services/backend`.
   - After §1 the site works either way. Still, if it isn't `vercel_backend.asgi:app`, send me the lines, so `vercel.json` can be corrected and Vercel stops building the other apps it detected.
3. Check that the deployment lists the research queue consumer, the function from `vercel_backend/queue_consumer.py`. If it's missing, research stays **Queued** and fails after `RUN_TIMEOUT_SECONDS` + 120 s. Send me the build log in that case.

## 7. Voice (Lens)

Voice needs a worker that stays connected to LiveKit all the time. Vercel Functions can't do that, and Vercel's always-on **Daemons** need the Pro plan to run all month. The backend side already works on Vercel:
- Creating a voice session is an ordinary API call.
- The worker's internal API (`/api/internal/voice/…`) is exempt from `SITE_PASSWORD` and protected by its own token, derived from the LiveKit secret.
- A research started by voice goes through the same queue.

So only the worker needs a home. Pick one:

| Option | Cost | What to do |
|---|---|---|
| **A. LiveKit Cloud agent hosting** (recommended: same account you already use) | check LiveKit's current plan limits before choosing | deploy `services/voice_agent` with LiveKit's CLI (`lk agent create`). Its image needs `services/core` too, so it needs a Dockerfile that builds from the repository root, as `Dockerfile.voice` does. Whether LiveKit's builder accepts that build context is the first thing to check |
| B. Vercel Daemon | Vercel Pro | add a `daemons` entry for the worker in `vercel.json` |
| C. Render (or any container host) | a paid background worker | the voice block in `render.yaml` with `Dockerfile.voice` |

Settings for any option:
- **Vercel project:** `VOICE_MODE=livekit`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_AGENT_NAME`.
- **Worker:** the same four LiveKit values, plus `VOICE_BACKEND_URL=https://marketlens-lemon.vercel.app` and the voice model settings from your `.env` (`VOICE_STT_MODEL`, `VOICE_LLM_MODEL`, `VOICE_TTS_MODEL`, `VOICE_TTS_VOICE`, `VOICE_LANGUAGE`).

Code to check while doing this:
- the worker's backend client works over https with the public address;
- the backend keeps a voice session's state in the database, not in one instance's memory, because each request can reach a different Function instance.

## 8. Live data (optional, after everything above works)

On Vercel, set `APP_MODE=live`, `DATA_MODE=live`, `ALPHA_VANTAGE_API_KEY`, `SEC_USER_AGENT`, `LLM_MODE=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL` and `RUN_TIMEOUT_SECONDS=240`.

The local Ollama model can't be reached from Vercel, so a hosted model is required. Set `SITE_PASSWORD` first, so strangers can't spend your quotas.

## 9. Checks after the redeploy

| Check | Expected |
|---|---|
| Open the site | password prompt (if set), then the app with the yellow DEMO FIXTURES banner |
| `GET /api/health` | `app_env: production`; all three `mcp_servers` show `ready: true` with their tools (the first request after a quiet period can take several seconds) |
| Ticker dropdown, type `apple` | AAPL appears within about a second |
| Bad tickers `AAPL$`, `ZZZZQ`, `DTEGY` | the three red messages from FEATURES A5 |
| Research AAPL | **Queued**, then the four progress steps, then a finished brief within about a minute |
| Read the brief | chips, source panel, chart, PDF (FEATURES C and D) |
| Cancel a run | "Run cancelled" |
| Re-run, Compare periods, Details | all answer (FEATURES E) |
| Search `apple`, refresh a `/?run=…` link | the run is found and reopens |
| `GET /api/briefs`, `/api/watchlist`, `/api/data-status` | 200 within a few seconds |
| Session cookie | `Secure; HttpOnly; SameSite=Lax` |
| Logs, filtered to `backend` | `MCP server … ready`, and for each run `research run …: completed` from the queue consumer |
| Voice (after §7) | the sound-wave button is active and a spoken request starts a run |

## 10. Documentation to update with the code

Following the project rule, update:
- `docs/DEPLOY_VERCEL.md`: the variables to delete, the Supabase pooler, and the build-log checks.
- `.env.example`: a note at the top, "Don't import this file into Vercel".
- `architecture.md`: the Vercel settings now enforced in core, and the fallback app.
- `README.md`: the deployment notes.
- `PROJECT_STATUS.md`: a new decision entry (D-101), test results, and the known-limitation updates.
