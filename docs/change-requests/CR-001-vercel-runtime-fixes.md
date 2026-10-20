# CR-001: Make MarketLens work on Vercel as intended

| | |
|---|---|
| **Status** | Ready for review. Code, tests and documentation are done on branch `fix/vercel-runtime`; not yet deployed |
| **Requested** | 2026-10-20, after the first live deploy at https://marketlens-lemon.vercel.app |
| **Priority** | High: on the live site no research can run |
| **Decision record** | D-101 in [`PROJECT_STATUS.md`](../../PROJECT_STATUS.md) |
| **Evidence and plan** | [`report.md`](../../report.md) (what works and what doesn't), [`fix.md`](../../fix.md) (the fix plan and the post-deploy checks) |

## Summary

On the live site, every feature that needs market data fails: research, the ticker search, compare periods, saved briefs, the watchlist and the confirmation flow. The cause is that the backend's three data services (MCP servers) never start on Vercel, and they fail without recording why.

This change makes the backend apply its Vercel settings itself, makes the data services start even when they can't write a log file, and turns setup problems into a clear message on the site instead of a generic server error. It changes no data, no routes and no `vercel.json`.

## Problem

Observed on 2026-10-20 by calling every public API endpoint:

| Area | Before this change |
|---|---|
| Research, ticker search, ticker checks | 503 after 20 s: "The research_sources MCP server is unavailable" |
| Compare periods, data status | no answer within 60–90 s |
| Saved briefs, watchlist, confirmations | 503 after about 21 s: "research_workspace MCP server is unavailable" |
| `/api/health` | `app_env: development`; all three MCP servers `ready: false`, `restarts: 0`, no error |
| Session cookie | not `Secure` |
| Pages, history, search, analytics | working |

**Root cause:**
- The Vercel settings were defaults that only applied when the project hadn't set them. The project's variables matched `.env.example` (`APP_ENV=development`, `RUN_EXECUTION=inline`, an empty `MCP_LOG_DIR`), so the defaults were overridden.
- Each MCP server then tried to create a log folder on Vercel's read-only filesystem. That failed before the error handling, so the server's supervisor stopped without recording anything.

## The change

**In scope:**
1. **Vercel settings are enforced.** On Vercel the backend always uses `APP_ENV=production`, research through Vercel Queues, and MCP server logs on stderr. It doesn't read `.env`, and it accepts `POSTGRES_URL` when `DATABASE_URL` is unset. This holds whichever module Vercel loads. Variables that say otherwise are ignored and listed in `/api/health` `warnings`.
2. **Configuration hygiene, everywhere:**
   - an empty variable (`NAME=`) means "use the default";
   - an invalid value is reported by the variable's name and the rule, never by the value, which might be a secret.
3. **The data services can't fail silently:**
   - a log file that can't be written falls back to stderr;
   - a supervisor that stops records why;
   - startup logs how long the three servers took.
4. **Setup problems answer instead of crashing (on Vercel):**
   - With an invalid configuration, or a database that can't be opened, every `/api` request gets `503 BACKEND_MISCONFIGURED` with a message that names the fix. The site's red bar shows it.
   - With Supabase's IPv6-only direct connection, the message points to the Transaction pooler.
5. **Data status doesn't hang.** It asks both servers at once, with a limit of 8 s each.
6. **Web:** the "start uvicorn" hint shows only on a developer's machine.
7. **Documentation:** the deploy guide (including Supabase), `.env.example`, README, architecture, the testing guide, features, and project status.

**Out of scope:**
- **Voice on Vercel.** It needs an always-on worker: LiveKit Cloud agent hosting, a Vercel Pro Daemon, or a paid container worker. This is a separate decision, with the options in `fix.md` §7.
- **Live data.** It needs the user's API keys and a hosted model (`fix.md` §8).
- **User accounts and rate limits** (README › Deployment notes).

## Impact

| Who or what | Impact |
|---|---|
| Visitors | Research and every data feature work. A setup problem reads as "Setup problem: …" instead of "Backend unavailable" |
| Operator (Vercel project) | Must redeploy, and should delete the copied variables, which health names, and set `SITE_PASSWORD` |
| API | One new error code, `BACKEND_MISCONFIGURED` (HTTP 503). No other contract changes. The OpenAPI schema and web types are regenerated |
| Data | No schema change and no migration |
| Local development | Unchanged, except: empty variables mean the default; invalid values get a readable message; `vercel dev` (`VERCEL_ENV=development`) keeps local settings |
| Security | No secrets in messages: config problems name variables, and database errors have credentials removed. The stand-in app serves no data. `/api/health` stays public, as before |

## Risks and how they're handled

| Risk | Handling |
|---|---|
| Vercel hides its system variables, so the backend can't tell it's on Vercel | The Vercel entry point marks the process as Vercel itself |
| A runtime replaces `sys.stderr` with something without a file descriptor, which the child processes need | The servers fall back to file descriptor 2 directly |
| Enforcing settings surprises someone who set them on purpose | Only three settings are enforced, all required by how Vercel Functions work. Health lists anything ignored |
| The queue consumer isn't deployed by Vercel | Not caused by this change. A post-deploy check confirms it (`fix.md` §6.2) |

**Rollback:** revert the merge commit and redeploy. There's no data migration to undo.

## Testing done

- **New tests:** 14 in `tests/integration/test_vercel_runtime.py`. They cover:
  - the enforced settings, `vercel dev`, `POSTGRES_URL` and empty variables;
  - invalid values;
  - the 503 for a bad configuration and for an unreachable database, with the hint and no password;
  - the local behaviour unchanged;
  - MCP servers with an unwritable log folder, and a dead supervisor;
  - data status bounded.
- **Full checks:** 385 passed, 6 skipped (the 6 need Postgres or real credentials); ruff, web lint, typecheck and production build clean; API schema check current; evaluation 49/49.
- **Local rehearsal of the live failure:** a read-only copy of the repository, every `.env.example` value plus `VERCEL=1` in the environment, Postgres and the local Vercel Queues server. Results:
  - production mode, all three data services ready in 0.4 s, the `Secure` cookie;
  - research completed through the queue in about 2 s; re-run and cancel worked;
  - search, ticker search and checks, data status, compare, briefs, the watchlist and the confirmation flow all answered;
  - voice answered "not configured", as designed.

## Deployment steps (after approval)

1. Merge `fix/vercel-runtime` into `main`. Vercel redeploys both services.
2. In the Vercel project, **Settings › Environment Variables**:
   - delete `APP_ENV`, `RUN_EXECUTION`, `MCP_LOG_DIR` and any other local values;
   - keep `DATABASE_URL` (the Supabase Transaction pooler string);
   - add `SITE_PASSWORD`;
   - then redeploy.
3. Run the checks in `fix.md` §9 and record the results in `PROJECT_STATUS.md` §6.

## Acceptance criteria

- [ ] `/api/health` on the live site shows `app_env: production`, all three `mcp_servers` with `ready: true`, and no ignored-variable warnings.
- [ ] A demo research for AAPL goes Queued → Completed within a minute, and the brief opens.
- [ ] The ticker dropdown finds AAPL for "apple"; the three bad-ticker messages appear.
- [ ] Compare periods, data status, saved briefs and the watchlist answer within a few seconds.
- [ ] The session cookie is `Secure`, and the site asks for the password when `SITE_PASSWORD` is set.
- [ ] The `backend` logs show `MCP servers ready: 3 of 3` and `research run …: completed`.

## Approval

- [ ] Reviewed: code and tests
- [ ] Reviewed: deployment steps and the variables to delete
- [ ] Approved to merge and deploy
