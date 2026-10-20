# Deploying MarketLens on Vercel

The whole app runs in **one Vercel project** on the free **Hobby** plan, using Vercel Services (decisions D-98, D-99):

| Part | On Vercel |
|---|---|
| **Website** (`apps/web`, Next.js) | Service `web`, public at your address: every page. It can ask for a site password. |
| **Backend** (FastAPI and its 3 MCP servers) | Service `backend`, public at **`/api`** on the same address, so the browser calls it directly. It asks for the same site password. Each instance starts the MCP servers inside itself. |
| **Research runs** | **Vercel Queues.** A new research is put on a queue, and a private queue-triggered function runs it (up to 5 minutes on Hobby). |
| **Database** | **Postgres** from the Vercel Marketplace (Neon, free plan). It replaces the local SQLite file. |
| **Voice (Lens)** | **Off** in the hosted version: it needs an always-on worker, which Hobby doesn't offer. Dictation still works, because it runs in the visitor's browser. |

```
              ┌─ /api/* ──▶ backend (FastAPI + 3 MCP servers) ──▶ Postgres (Neon)
Browser ─https┤               site password │ publishes each run       ▲
              │                             ▼                          │
              │                       Vercel Queues ──▶ research consumer (private function)
              └─ everything else ──▶ web (Next.js pages), site password
```

**Before you start:**
- Services, Queues (Python SDK) and the Python backend are **beta** Vercel features. Hobby includes the first 1,000,000 queue operations a month, and one research uses only a handful.
- Everything was rehearsed locally with Postgres, the Queues SDK's local server and the production web build (see [PROJECT_STATUS](../PROJECT_STATUS.md) D-98). It has **not yet been deployed to Vercel**, so the first deploy is the real test. [If something goes wrong](#if-something-goes-wrong) covers the likely problems.

## 1. Create the Vercel project

1. In Vercel choose **Add New › Project** and import the repository.
2. Leave **Root Directory** as the repository root (`./`). The `vercel.json` there defines both services.
   - If you imported the repository before with `apps/web` as the root, open Settings › Build and Deployment, clear **Root Directory**, and turn off any **Override** toggles.
3. Under Git, set the **Production Branch** to the branch you deploy (for example `feature/vercel-services`, or `main` after merging).
4. Click **Deploy**. The website appears. Research doesn't work yet, because the backend has no database (step 2).

## 2. Add the database

1. In the project open **Storage › Create Database › Neon** (Postgres).
2. Choose the region **Washington, D.C. (us-east-1)**, close to Vercel's default region `iad1`, and the **Free** plan.
3. Connect it to the project for Production and Preview. This adds `DATABASE_URL` to the project's environment variables.
4. Open **Deployments**, pick the latest one, and choose **⋯ › Redeploy**. The backend creates its tables on the first request.

## 3. Optional settings

Add these under **Settings › Environment Variables**, then redeploy:

| Variable | Value | |
|---|---|---|
| `SITE_PASSWORD` | a password of your choice | **strongly recommended**: visitors must enter it (any user name) before anything loads. The website checks it for pages and the backend for `/api`; browsers send it with every request, so visitors sign in once. Without it, anyone who finds the address can run research on your keys |
| `RUN_TIMEOUT_SECONDS` | `120` (default) to `280` | a research must finish within the 300-second function limit on Hobby |

What you don't need to set:
- `RUN_EXECUTION=queue`, `APP_ENV=production` (the `Secure` session cookie) and `MCP_LOG_DIR=-` (server logs in Vercel's logs) are set automatically on Vercel by `vercel_backend/`.
- `BACKEND_URL` comes from the binding.
- **Don't set `PROXY_SHARED_SECRET`** in this setup. Browsers call `/api` directly and can't send it, so the backend refuses to start with it on Vercel and says why. It only belongs in the backend-on-its-own-host setup (bottom of this page).

Environment variables belong to the whole project, so both services can read them on the server. None of them is sent to browsers.

## 4. Check that it works

1. Open your address. The browser asks for the password if you set one. MarketLens loads with the yellow **DEMO FIXTURES** label.
2. Run a research, for example AAPL. It shows **Queued**, then the progress steps, and completes within a minute in demo mode.
3. Press the search icon and search for `apple`: the run appears.
4. Optional: in **Logs**, choose the `backend` service. You should see `MCP server … ready` and, for each research, `research run …: completed`.

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
| Red "Backend unavailable" bar | Logs › `backend`: a configuration message names the variable to fix. If there are no `backend` logs at all, check that `vercel.json` is at the repository root and that Root Directory is `./`. |
| "Backend unavailable" and the `backend` logs show `Invalid MarketLens configuration` | The message names the variable. Usually `DATABASE_URL` is missing (step 2), a live-mode key is missing, or `PROXY_SHARED_SECRET` is set (remove it). |
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

- **`vercel.json`:**
  - service `web`: root `apps/web`; pnpm 12.10.1 through `npx`, because Vercel's own pnpm stops at 10; and a binding to `backend`, which sets `BACKEND_URL`;
  - service `backend`: root `./`, FastAPI, entry point `vercel_backend.asgi:app`;
  - two public rewrites, in this order: `/api/(.*)` → `backend` (the service sees the full `/api/…` path, which matches its routes), then `/(.*)` → `web`.
- **`vercel_backend/`:**
  - `asgi.py`: the app;
  - `queue_consumer.py`: the `@subscribe` research consumer, declared under `[[tool.vercel.subscribers]]` in `pyproject.toml`;
  - `__init__.py`: import paths and the Vercel defaults above.
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
