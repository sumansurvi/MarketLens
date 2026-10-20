# Deploying MarketLens on Vercel

The whole app runs in **one Vercel project** on the free **Hobby** plan, using Vercel Services (decisions D-98, D-99):

| Part | On Vercel |
|---|---|
| **Website** (`apps/web`, Next.js) | Service `web`, public at your address: every page. It can ask for a site password. |
| **Backend** (FastAPI and its 3 MCP servers) | Service `backend`, public at **`/api`** on the same address, so the browser calls it directly. It asks for the same site password. Each instance starts the MCP servers inside itself. |
| **Research runs** | **Vercel Queues.** A new research is put on a queue, and a private queue-triggered function runs it (up to 5 minutes on Hobby). |
| **Database** | **Postgres**: Neon from the Vercel Marketplace (free plan), or your own Supabase project through its pooler. It replaces the local SQLite file. |
| **Voice (Lens)** | **Off** in the hosted version: it needs an always-on worker, which Hobby doesn't offer. Dictation still works, because it runs in the visitor's browser. |

```
              ┌─ /api/* ──▶ backend (FastAPI + 3 MCP servers) ──▶ Postgres (Neon or Supabase)
Browser ─https┤               site password │ publishes each run       ▲
              │                             ▼                          │
              │                       Vercel Queues ──▶ research consumer (private function)
              └─ everything else ──▶ web (Next.js pages), site password
```

**Before you start:**
- Services, Queues (Python SDK) and the Python backend are **beta** Vercel features. Hobby includes the first 1,000,000 queue operations a month, and one research uses only a handful.
- The first deploy (2026-10-20) found that environment variables copied from `.env.example` stopped the backend's three data services. Since D-101 the backend applies its Vercel settings itself, and a setup problem shows up on the site as a message that names the fix. Both were rehearsed locally with a read-only copy of the repository, Postgres and the Queues SDK's local server (see [PROJECT_STATUS](../PROJECT_STATUS.md) D-101). [If something goes wrong](#if-something-goes-wrong) covers the likely problems.

## 1. Create the Vercel project

1. In Vercel choose **Add New › Project** and import the repository.
2. On the configuration screen, set **Application Preset** to **Services**. Some screens call it **Framework Preset**. This is the step that turns one import into two services.
   - Vercel builds services only when the preset is **Services** *and* `vercel.json` has a `services` key. With any other preset, it ignores the `services` block and deploys one app. It often suggests Next.js because of `apps/web`.
   - If the list doesn't offer **Services**, deploy anyway. Then set it under Settings › Build and Deployment › **Framework Preset** and redeploy.
   - Below the preset, Vercel says "Multiple applications detected" and lists every folder that looks like an app, each with an **Import single project** button. Expect `web`, `backend`, `core`, the three MCP servers, `voice_agent` and a Python `app` at the root. **Don't click any of those buttons.** Each one imports that folder as a separate project. The list comes from Vercel's own scan, not from `vercel.json`, which defines just `web` and `backend`.
3. Leave **Root Directory** as the repository root (`./`). The `vercel.json` there defines both services.
   - If you imported the repository before with `apps/web` as the root, open Settings › Build and Deployment, set **Framework Preset** to **Services**, clear **Root Directory**, and turn off any **Override** toggles.
4. Under Git, check that the **Production Branch** is the branch you deploy. `main` has had this setup since PR #4.
5. Scroll past the list and click **Deploy** at the bottom of the page. The website appears. Research doesn't work yet, because the backend has no database ([section 2](#2-add-the-database)).
6. Check that both services were built: the deployment's page shows a services graph with `web` and `backend`, and **Logs** lets you filter by service. If you see only one app, the preset isn't **Services** (see step 2).

## 2. Add the database

**Neon from the Vercel Marketplace:**
1. In the project open **Storage › Create Database › Neon** (Postgres).
2. Choose the region **Washington, D.C. (us-east-1)**, close to Vercel's default region `iad1`, and the **Free** plan.
3. Connect it to the project for Production and Preview. This adds `DATABASE_URL` to the project's environment variables.
4. Open **Deployments**, pick the latest one, and choose **⋯ › Redeploy**. The backend creates its tables on the first request.

**Or your own Supabase project:**
1. In Supabase, open the project and click **Connect**. Under **Connection string**, choose **Transaction pooler** (port **6543**, host `…pooler.supabase.com`). Copy it and put your database password in it.
   - Not the **direct connection** (`db.….supabase.co`, port 5432). It only accepts IPv6, which Vercel can't use, so every request hangs and then fails. The backend recognises that host and says so.
2. In Vercel, add it as `DATABASE_URL` under **Settings › Environment Variables** for Production and Preview.
3. Redeploy, as in step 4 above.

If you connect Supabase through the Vercel Marketplace instead, it sets `POSTGRES_URL`. The backend uses that when `DATABASE_URL` isn't set.

## 3. Optional settings

Add these under **Settings › Environment Variables**, then redeploy. **Don't import `.env` or `.env.example` into the project:** they hold local values, and the backend has its own settings for Vercel.

| Variable | Value | |
|---|---|---|
| `SITE_PASSWORD` | a password of your choice | **strongly recommended**: visitors must enter it (any user name) before anything loads. The website checks it for pages and the backend for `/api`; browsers send it with every request, so visitors sign in once. Without it, anyone who finds the address can run research on your keys |
| `RUN_TIMEOUT_SECONDS` | `120` (default) to `280` | a research must finish within the 300-second function limit on Hobby |

What you don't need to set:
- `RUN_EXECUTION=queue`, `APP_ENV=production` (the `Secure` session cookie) and `MCP_LOG_DIR=-` (server logs in Vercel's logs) are always used on Vercel: the backend enforces them (D-101). If the project has other values for them, for example copied from `.env.example`, they are ignored, and `/api/health` lists them under `warnings` so you can delete them.
- `BACKEND_URL` comes from the binding.
- **Don't set `PROXY_SHARED_SECRET`** in this setup. Browsers call `/api` directly and can't send it, so the backend refuses to start with it on Vercel and says why. It only belongs in the backend-on-its-own-host setup (bottom of this page).

Environment variables belong to the whole project, so both services can read them on the server. None of them is sent to browsers.

## 4. Check that it works

1. Open your address. The browser asks for the password if you set one. MarketLens loads with the yellow **DEMO FIXTURES** label.
2. Open `/api/health`. Expect `"app_env": "production"`, all three `mcp_servers` with `"ready": true`, and no `warnings` except the password note when `SITE_PASSWORD` isn't set.
3. Run a research, for example AAPL. It shows **Queued**, then the progress steps, and completes within a minute in demo mode.
4. Press the search icon and search for `apple`: the run appears.
5. Optional: in **Logs**, choose the `backend` service. You should see `MCP servers ready: 3 of 3 in … s` and, for each research, `research run …: completed`.

## Live data (optional)

Add these to the project's environment variables, then redeploy:

| Variable | Value |
|---|---|
| `APP_MODE`, `DATA_MODE` | `live` |
| `ALPHA_VANTAGE_API_KEY` | your key (the free key allows 25 requests a day, shared by everyone using the site) |
| `SEC_USER_AGENT` | `MarketLens/0.1 contact=you@yourdomain.com` (SEC requires a real contact) |
| `LLM_MODE` | `openai` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | an OpenAI key and a model that supports structured outputs |
| `RUN_TIMEOUT_SECONDS` | `240` (model calls take longer than the demo) |

The local Ollama setup runs only on your own computer, so the hosted backend needs a hosted model. Another OpenAI-compatible service works only if it supports the Responses API with structured outputs. Outbound HTTPS to Alpha Vantage, SEC EDGAR and Yahoo Finance is allowed by default. Every other setting is explained in [`.env.example`](../.env.example).

## Voice (not in the hosted version)

The voice assistant needs a worker that stays connected to LiveKit all the time. On Vercel that means a **Daemon**, which needs the Pro plan to run all month: Hobby includes 10 vCPU-hours a month, about 10 hours of one worker. The alternative is LiveKit's own agent hosting. With voice off, the sound-wave button is greyed out with the label "Voice not configured", and everything else, including dictation, works. `Dockerfile.voice` is kept for later.

## Updating

Push to the production branch and Vercel redeploys both services. Preview deployments of other branches work too: each preview has its own queue partition. Previews share the production database unless you enable Neon's branch-per-preview option in the integration.

## If something goes wrong

| What you see | Where to look and what to do |
|---|---|
| The import or the deployment shows a single app (no `web` and `backend` services) | Settings › Build and Deployment › **Framework Preset** isn't **Services**. Set it, clear Root Directory (`./`), turn off any **Override** toggles, and redeploy. Vercel uses the `services` in `vercel.json` only with this preset. |
| Red bar "Backend unavailable. Setup problem: …" | Do what the message says; `/api/health` lists every problem under `error.details.problems`. Usually `DATABASE_URL` is missing or not Postgres (step 2), a live-mode key is missing, or `PROXY_SHARED_SECRET` is set (remove it). |
| "Setup problem: The backend can't use its database …" | `DATABASE_URL` is wrong or unreachable. With Supabase, use the **Transaction pooler** string (port 6543), not the direct `db.….supabase.co` one. |
| Red bar "The MarketLens backend failed to answer (HTTP 500)" | The backend crashed before it could answer. Look in Logs › `backend`. If there are no `backend` logs at all, check that Framework Preset is **Services**, that `vercel.json` is at the repository root and that Root Directory is `./`. |
| "The … MCP server is unavailable" | `/api/health` shows each server's `last_error`. Send it with the `backend` logs. A log folder the function can't write no longer stops the servers (D-101). |
| `/api/health` warns "… is set in the project's environment variables but ignored on Vercel" | Delete that variable in Settings › Environment Variables. It does no harm, but it doesn't belong there. |
| A research stays **Queued**, then fails with "didn't finish in time" | The queue consumer didn't run. In Logs, look for the consumer function and its errors. Check that `pyproject.toml` still has `[[tool.vercel.subscribers]]`. |
| A research fails with "couldn't be queued" | The backend couldn't publish to Vercel Queues. The `backend` logs show why (for example an authentication error). |
| The first request after a quiet period takes several seconds | The backend instance was starting, which includes its three MCP servers. Later requests are fast. |
| Build error about pnpm or the install | The `web` service's install and build commands come from `vercel.json`. Turn off any **Override** toggles in the project settings. |

## What is protected, and what isn't

Protected:
- **`SITE_PASSWORD`** keeps strangers out of the pages (checked by the website) and the API at `/api` (checked by the backend). Only `/api/health` answers without it; it shows modes and capabilities, never keys.
- The queue consumer can only be started by Vercel Queues.
- The session cookie is HTTP-only, `SameSite=Lax` and `Secure`.
- API keys are server-side environment variables, never sent to browsers.

Not protected:
- MarketLens is still **one shared workspace**. Everyone with the password sees the same research history and uses the same API key quotas.
- There are no user accounts and no rate limits on research.

Share the password only with people you trust. If you want a site anyone can open without a password, keep it in demo mode (synthetic data, no keys).

## How it fits together (for maintainers)

- **Project settings:** Framework Preset **Services**, Root Directory `./`, no overrides. Without the Services preset, Vercel ignores the `services` key.
- **`vercel.json`:**
  - service `web`: root `apps/web`; pnpm 12.10.1 through `npx`, because Vercel's own pnpm stops at 10; and a binding to `backend`, which sets `BACKEND_URL`;
  - service `backend`: root `./`, FastAPI, entry point `vercel_backend.asgi:app`;
  - two public rewrites, in this order: `/api/(.*)` → `backend` (the service sees the full `/api/…` path, which matches its routes), then `/(.*)` → `web`.
- **`vercel_backend/`:**
  - `asgi.py`: the app;
  - `queue_consumer.py`: the `@subscribe` research consumer, declared under `[[tool.vercel.subscribers]]` in `pyproject.toml`;
  - `__init__.py`: import paths, and `VERCEL=1` when the platform hides its system variables.
- **Vercel settings** (`marketlens_core.config`, D-101): on Vercel (`VERCEL` set, `VERCEL_ENV` not `development`), `load_settings` enforces `VERCEL_PROFILE` (`APP_ENV=production`, `RUN_EXECUTION=queue`, `MCP_LOG_DIR=-`), skips `.env`, and takes `POSTGRES_URL` when `DATABASE_URL` is unset. It works whichever module loads the app. Empty variables count as unset everywhere.
- **Setup problems answer instead of crashing** (D-101):
  - An invalid configuration makes `hosted_app_from_settings` serve a stand-in app. Every `/api` request gets `503 BACKEND_MISCONFIGURED` with the problems, and the site's red bar shows the first one.
  - A database the backend can't open at startup gives the same 503, with a known fix (the Supabase pooler) when the URL shows one.
  - Locally both still stop the backend, as before.
- **MCP servers:** a log file that can't be written falls back to stderr, and a supervisor that dies records why in `last_error`.
- **`apps/web/app/api/[...path]/route.ts`** forwards `/api/*` to `BACKEND_URL` at request time. On Vercel `/api` never reaches the website, so this matters for local runs and the backend-on-its-own-host setup. The binding stays as a fallback for that route (a build-time rewrite couldn't use it).
- **Site password on the backend** (`app/api/middleware.py`, D-99): with `SITE_PASSWORD` set and no `PROXY_SHARED_SECRET`, every `/api` request except `/api/health` must carry it as HTTP Basic credentials. With a secret, the backend trusts the website, which checked the password, and ignores direct calls.
- **Research execution:** `RUN_EXECUTION=queue`.
  - `QueueRunManager` publishes `{run_id}` before answering.
  - `run_consumer.run_queued_research` claims the run atomically, runs it, and watches the database for cancellation.
  - Runs left queued or running past their limit are marked failed when anyone looks.
- **Postgres:** `migrations_postgres/` mirrors `migrations/` with the same version numbers. Writes take one advisory lock, so they are serialized as in SQLite.
- **Local rehearsal:** see [TESTING_GUIDE](TESTING_GUIDE.md) §7.

## Alternative: backend on another host

The earlier setup (D-94) still works:
- The website is a Vercel project with Root Directory `apps/web`. Set the install command `npx --yes pnpm@12.10.1 install --frozen-lockfile` and the build command `npx --yes pnpm@12.10.1 run build` in its settings.
- Add the environment variables `BACKEND_URL`, `PROXY_SHARED_SECRET` and `SITE_PASSWORD`.
- The backend runs from `render.yaml` / `Dockerfile.backend` on Render or any container host, with SQLite on a disk. Research then runs inside the backend process (`RUN_EXECUTION=inline`).
