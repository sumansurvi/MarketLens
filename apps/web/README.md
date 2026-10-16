# MarketLens web app

Next.js 16 (App Router, TypeScript, Tailwind 4) client for MarketLens. See the repository root
`README.md` for setup. The browser calls same-origin `/api/*`, which Next.js proxies to the FastAPI
backend at `BACKEND_URL` (default `http://127.0.0.1:8000`). The app never holds API keys.

- `pnpm dev` / `pnpm build` / `pnpm lint` / `pnpm typecheck`
- Deploys to Vercel with Root Directory `apps/web` (`vercel.json`). `proxy.ts` (Next 16's renamed
  middleware) can ask for a site password (`SITE_PASSWORD`), and adds the backend shared secret
  (`PROXY_SHARED_SECRET`) to `/api` requests server-side. Both are unset locally. See
  `docs/DEPLOY_VERCEL.md` (D-94).
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
decisions are recorded as D-78, D-81 to D-93, D-95 and D-96 in `PROJECT_STATUS.md`.
