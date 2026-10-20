# MarketLens web app

Next.js 16 (App Router, TypeScript, Tailwind 4) client for MarketLens. See the repository root
`README.md` for setup. The browser calls same-origin `/api/*`, which Next.js proxies to the FastAPI
backend at `BACKEND_URL` (default `http://127.0.0.1:8000`). The app never holds API keys.

- `pnpm dev` / `pnpm build` / `pnpm lint` / `pnpm typecheck`
- On Vercel this is the `web` service in the repository's root `vercel.json`. Vercel sends
  `/api/*` straight to the `backend` service (D-98, D-99).
- Locally, and with a backend on its own host, `app/api/[...path]/route.ts` forwards `/api/*`
  to `BACKEND_URL` when a request arrives (default `http://127.0.0.1:8000`).
- `proxy.ts` (Next 16's renamed middleware) can ask for a site password (`SITE_PASSWORD`); on
  Vercel the backend checks the same password for `/api`. With a backend on its own host it adds
  the shared secret (`PROXY_SHARED_SECRET`, D-94) and keeps the password to itself; otherwise it
  passes the browser's credentials on, so the backend can check them. Both are unset locally. See
  `docs/DEPLOY_VERCEL.md`.
- `pnpm gen:api` regenerates `lib/api/schema.d.ts` from `openapi.json`
  (export it with `uv run python scripts/export_openapi.py` at the repo root).

## Layout (one chat-style screen)

| Area | Components |
|---|---|
| Shell | `components/workspace/workspace.tsx`: mode banner, header, history rail (left), main pane, suggestions rail (right, ≥1280 px), composer (bottom) |
| History | `workspace/rail.tsx`: **New research** and the search button, runs with status, DEMO and **Voice** tags; `workspace/run-status.ts` (status dots shared with search) |
| Search | `workspace/search.tsx`: "Search research" modal (⌘K / Ctrl+K), suggestions as you type from `GET /api/research/search`, recent research and researched tickers when empty (D-96) |
| Research bar | `workspace/research-bar.tsx` (one line: ticker, question, window, dictation, voice, ↑), `ticker-combobox.tsx` (always-populated ticker dropdown, `GET /api/symbols`), `dictation.tsx` (browser speech recognition) |
| Welcome, quick starts, guide | `workspace/welcome.tsx`, `workspace/suggestions.tsx`, `workspace/guide.tsx` ("Explore MarketLens" modal) |
| Run and brief | `run/run-view.tsx` (summary bar, PDF), `run/brief-view.tsx` (sections, tinted findings), `run/claims.tsx` (statements, source chips), `run/evidence-inspector.tsx` (source panel in plain words), `run/progress.tsx`, `run/issues.tsx` (Heads up), `run/share.tsx` (PDF button, per-section Copy/Share), `run/jump.ts` |
| Voice (Lens) | `voice/voice-panel.tsx` (voice button, large voice screen, compact bar, progress inside the screen, audio unlock), `voice/voice-state.ts` |
| Shared | `hint.tsx` (hover definitions inside the brief), `scroll-to-bottom.tsx`, `icons.tsx`, `modal.tsx`, `ui.tsx`, `lib/readable.ts` (source names and plain-language values), `lib/natural.ts` (dates, ID stripping) |

Rules the UI follows: no internal IDs, field names or codes on screen; hover hints only inside the
brief; every source chip has a distinct name; nothing navigates away from the one screen. Design
decisions are recorded as D-78, D-81 to D-93, D-95, D-96, D-98 and D-99 in `PROJECT_STATUS.md`.
