# Deploying MarketLens with Vercel

MarketLens has two parts, and only one fits Vercel:

| Part | Where | Why |
|---|---|---|
| **Web app** (`apps/web`, Next.js) | **Vercel** | A standard Next.js app; `/api/*` is proxied to the backend by a rewrite. |
| **Backend** (FastAPI + 3 MCP servers + SQLite) | **A container host** (Render, Fly.io, Railway, Cloud Run, a VM) | It spawns long-lived MCP subprocesses, runs research in background tasks and keeps a SQLite file. Vercel's serverless functions can't do that. |

## 1. Deploy the backend (`Dockerfile.backend`)

Build and run `Dockerfile.backend` on your host, with a persistent volume at `/data` (the SQLite file). Settings:

| Variable | Value |
|---|---|
| `FRONTEND_ORIGIN` | your Vercel URL, e.g. `https://marketlens.vercel.app` |
| `APP_MODE` etc. | defaults give the offline demo; see `.env.example` for live data, OpenAI and voice |

News in live mode falls back to Yahoo Finance's public headline feed when the Alpha Vantage plan lacks news (`NEWS_FALLBACK`), so the backend needs outbound HTTPS to `feeds.finance.yahoo.com` as well as Alpha Vantage and SEC EDGAR. Voice needs the worker deployed separately as a LiveKit agent (`marketlens-voice-agent start`, or LiveKit Cloud agents) with `VOICE_BACKEND_URL` pointing at this backend; the Vercel web app alone doesn't include it. Dictation needs nothing on the server (it uses the visitor's browser).

Check `https://<backend>/api/health`. Locally you can try it with
`docker build -f Dockerfile.backend -t marketlens-backend . && docker run -p 8000:8000 -v marketlens-data:/data marketlens-backend`.

## 2. Deploy the web app on Vercel

1. Import the repository in Vercel.
2. Set **Root Directory** to `apps/web` (keep "Include source files outside of the Root Directory" enabled, the default). `apps/web/vercel.json` sets the framework and the pnpm commands.
3. Add the environment variable **`BACKEND_URL`** = `https://<your backend>` (https, no trailing slash). The build fails with a clear message if it is missing. It is fixed at build time, so redeploy after changing it.
4. Deploy. Or with the CLI: `cd apps/web && vercel --prod`.

## Before you share the URL

MarketLens is a **single-user MVP with no authentication**: anyone who can open the site shares the same runs, saved briefs and watchlist, and live mode would spend your API keys. A public deployment is fine for the **fixture demo** (synthetic data, no keys). For live data, a real model or voice, add authentication, per-user isolation and rate limits first (README › Deployment notes). Never put API keys in Vercel's web project; they belong only on the backend.
