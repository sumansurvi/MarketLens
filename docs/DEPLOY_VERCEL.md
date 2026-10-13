# Deploying MarketLens (Vercel + Render)

MarketLens has two parts, and only one of them fits Vercel:

| Part | Where | Why |
|---|---|---|
| **Web app** (`apps/web`, Next.js) | **Vercel** | A standard Next.js app. `/api/*` is proxied to the backend, so the browser only ever talks to your Vercel address. |
| **Backend** (FastAPI, 3 MCP servers, SQLite) | **A container host.** Render is set up in `render.yaml`; Railway, Fly.io, Cloud Run or a VM also work | It runs long-lived MCP subprocesses and background research, and keeps a SQLite file. Vercel's serverless functions can't do that. |
| **Voice worker** (optional) | The same container host, as a background worker (`Dockerfile.voice`) | It is an always-on LiveKit agent. Dictation needs nothing on the server. |

Deploy the backend first, because the web app needs its URL. It takes about 15 minutes. The first deploy runs the **demo** (synthetic data, no API keys), so it works before you add any keys.

```
Browser ──https──▶ Vercel (apps/web)          ──https──▶ Render (Dockerfile.backend) ──▶ Alpha Vantage, SEC, Yahoo RSS, OpenAI
                   proxy.ts: site password,              checks PROXY_SHARED_SECRET,
                   adds PROXY_SHARED_SECRET              SQLite on a disk at /data
```

## 1. Backend on Render

1. In the Render dashboard choose **New › Blueprint**, connect your GitHub account and pick this repository and the branch you deploy from.
2. Render reads [`render.yaml`](../render.yaml) and proposes one web service, **marketlens-backend**:
   - built from `Dockerfile.backend`;
   - a 1 GB disk at `/data` for the research history;
   - demo-mode settings;
   - `APP_ENV=production`, which makes the session cookie `Secure`;
   - a randomly generated **`PROXY_SHARED_SECRET`**.

   Click **Apply**.
3. When the deploy is live, open `https://<your-service>.onrender.com/api/health`. You should see JSON with `"data_mode":"fixtures"`. Every other `/api` address answers **403** when opened directly; that's expected, because only your web app may call it.
4. Note two values for step 2:
   - the service URL (top of the service page);
   - `PROXY_SHARED_SECRET` (**Environment** tab › reveal).

**Plans:** a disk needs a paid instance (`plan: starter`). To try it for free, delete the `disk` block in `render.yaml` and set `plan: free`. Then the research history resets on every deploy, and the service sleeps when idle (the first visit after that takes about a minute).

**Other hosts:** `Dockerfile.backend` runs anywhere. You need to:
- mount a persistent volume at `/data`;
- let the host set `PORT`;
- use `/api/health` as the health check;
- set the same variables as `render.yaml`;
- generate the secret yourself with `openssl rand -hex 32`.

To try the image locally, run `docker build -f Dockerfile.backend -t marketlens-backend . && docker run -p 8000:8000 -v marketlens-data:/data marketlens-backend`.

## 2. Web app on Vercel

1. In Vercel choose **Add New › Project** and import the same repository.
2. Set **Root Directory** to `apps/web`. Leave "Include source files outside of the Root Directory" on (the default). The framework (Next.js) and the pnpm commands come from [`apps/web/vercel.json`](../apps/web/vercel.json); don't override them.
3. Add these **Environment Variables**:

   | Variable | Value | |
   |---|---|---|
   | `BACKEND_URL` | the Render URL, e.g. `https://marketlens-backend.onrender.com` (https, no trailing slash) | required: the build stops with a clear message without it |
   | `PROXY_SHARED_SECRET` | exactly the value from Render | required when the backend has one (the blueprint sets one) |
   | `SITE_PASSWORD` | a password of your choice | recommended: visitors must enter it before anything loads (any user name works) |
   | `ENABLE_EXPERIMENTAL_COREPACK` | `1` | recommended: Vercel then installs with exactly the pnpm version in `package.json` (12.10.1) |

4. Click **Deploy**. Open the address Vercel gives you. The browser asks for the password, then MarketLens loads with the yellow **DEMO FIXTURES** label. Run a research to check the whole path.

With the CLI instead: `cd apps/web && vercel link`, add the variables with `vercel env add`, then `vercel --prod`.

## 3. Tell the backend your Vercel address

On Render, set **`FRONTEND_ORIGIN`** to your Vercel address (for example `https://marketlens-yourname.vercel.app`) and save; Render redeploys. The app already works before this step, because requests arrive through the proxy. This setting is the backend's CORS allow-list.

## 4. Optional: live data

All keys go on the **backend (Render › Environment)**, never in Vercel. Set:

| Variable | Value |
|---|---|
| `APP_MODE`, `DATA_MODE` | `live` |
| `ALPHA_VANTAGE_API_KEY` | your key (the free key allows 25 requests a day, shared by everyone using your site) |
| `SEC_USER_AGENT` | `MarketLens/0.1 contact=you@yourdomain.com` (SEC requires a real contact) |
| `LLM_MODE` | `openai` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | an OpenAI key and a model that supports structured outputs |
| `RUN_TIMEOUT_SECONDS` | `240` (model calls take longer than the demo) |

The local Ollama setup (`OPENAI_BASE_URL=http://127.0.0.1:11434/v1`) runs only on your own computer, so a hosted backend needs a hosted model. Another OpenAI-compatible service works only if it supports the Responses API with structured outputs. When Alpha Vantage's plan has no news, headlines come from Yahoo Finance's public feed. The backend therefore needs outbound HTTPS to `www.alphavantage.co`, `data.sec.gov`, `www.sec.gov` and `feeds.finance.yahoo.com`. Render allows this by default. Every other setting is explained in [`.env.example`](../.env.example).

## 5. Optional: voice (Lens)

1. **Backend (Render):**
   - `VOICE_MODE=livekit`;
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` from your LiveKit Cloud project.
2. **Worker:** uncomment the `marketlens-voice` block in `render.yaml` and push (Render's Blueprint syncs it). Or create a **Background Worker** from `Dockerfile.voice` by hand. Give it:
   - the same `VOICE_MODE` and `LIVEKIT_*` values;
   - the voice models (`VOICE_STT_MODEL`, `VOICE_LLM_MODEL`, `VOICE_TTS_MODEL`, optional `VOICE_TTS_VOICE`; the tested set is in `.env.example`);
   - `VOICE_BACKEND_URL` = the backend's https URL.

   The worker uses its own token (derived from the LiveKit secret) and doesn't need `PROXY_SHARED_SECRET`.
3. The browser connects to LiveKit directly. The site is served over https, so the microphone works once the visitor allows it.

## Updating

Push to the deployed branch: Render and Vercel both redeploy on their own. `BACKEND_URL` is fixed when the web app is built, so redeploy Vercel after changing it. Changes to `SITE_PASSWORD` or `PROXY_SHARED_SECRET` also take effect on the next Vercel deploy.

## If something goes wrong

| What you see | Fix |
|---|---|
| Vercel build: "BACKEND_URL must be set to the public https URL…" | Add `BACKEND_URL` (https) to the Vercel project and redeploy. |
| Vercel install fails over the pnpm version | Add `ENABLE_EXPERIMENTAL_COREPACK=1` and redeploy. |
| Red "Backend unavailable" bar, or "This backend only accepts requests through the MarketLens web app" | `PROXY_SHARED_SECRET` is missing in Vercel or differs from Render's. Fix it and redeploy Vercel. |
| The first visit takes a minute | The Render free plan was asleep. Use a paid plan to keep it awake. |
| Research history disappears after a deploy | The backend has no disk at `/data` (free plan). |
| Render deploy fails its health check | Open the service's **Logs**. A configuration problem is printed with the variable to fix (for example `SEC_USER_AGENT` in live mode). |

## What is protected, and what isn't

Protected:
- **`SITE_PASSWORD`** keeps strangers out of the site and its API.
- **`PROXY_SHARED_SECRET`** stops anyone from calling the backend directly, bypassing the password.
- The session cookie is HTTP-only, `SameSite=Lax` and `Secure`.
- API keys exist only on the backend.

Not protected:
- MarketLens is still **one shared workspace**. Everyone who has the password sees the same research history and uses the same API key quotas.
- There are no user accounts, per-user data or rate limits.

Share the password only with people you trust. If you want a site anyone can open without a password, keep it in demo mode (synthetic data, no keys). Per-user accounts and rate limits would be a separate project (README › Deployment notes).
