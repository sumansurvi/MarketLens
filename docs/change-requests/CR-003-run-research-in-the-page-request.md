# CR-003: Run research in the page's own request on Vercel

| | |
|---|---|
| **Status** | Ready for review. Code, tests and documentation are done on branch `fix/research-runs-execute-in-request` (from `main`, which has D-102); not yet deployed |
| **Requested** | 2026-10-21, from the live site: "nothing is working" |
| **Priority** | Critical: no research could run on the live site |
| **Decision record** | D-103 in [`PROJECT_STATUS.md`](../../PROJECT_STATUS.md) |
| **Follows** | [CR-002](CR-002-research-runs-stalled-on-vercel.md) (D-102) |

## Summary

On the live site, every research run stayed **queued** until the stale-run sweep marked it "didn't pick the run up", four minutes later. The start request was accepted and the message was published to Vercel Queues, but the consumer never received a delivery. Every other feature works: health, symbols, briefs, watchlist, data status, history and search.

This change stops depending on the queue. On Vercel the browser starts a run, then asks the backend to run it in that request, and polls the run's row for progress. The request lasts as long as the research (up to Hobby's 300 s). Nothing needs to be deployed or delivered besides the two services. The queue mode stays available for local rehearsal, but Vercel no longer builds a consumer for it.

## Problem

| What the live site showed | Meaning |
|---|---|
| A run with "The research service didn't pick the run up" (D-102 copy) | the publish succeeded and no delivery arrived within four minutes |
| Every other endpoint answering | the app, the MCP servers and the database are fine; only the delivery is missing |
| No consumer log lines reachable from here | whether Vercel built the consumer for this services project cannot be confirmed from outside |

The queue design depends on a part of the platform (a subscriber built and delivered for a Python service inside a Services project) that could not be confirmed from the code or the docs. Vercel's Python SDK documents subscriber discovery at build time, and its services section documents triggers only for JavaScript.

## The change

1. **New execution mode `RUN_EXECUTION=request`**, enforced on Vercel by the Vercel profile:
   - `POST /api/research` records the run as `queued` and answers at once (nothing is published).
   - The page opens the run. The run view sees it `queued` and calls `POST /api/research/{id}/execute`.
   - The execute request runs `run_consumer.run_queued_research` to the end: an atomic claim, the workflow, and a watch on the run row for a cancel request (set from another instance if need be).
   - The page polls the run's row while it works, as before. Re-run works the same way.
2. **Repeats are safe.** The claim is atomic, so a second execute request for a run returns at once. A module-level set stops one page from starting a run twice. A run that is still queued when a page opens it is started by that page, so a reload recovers a run that never started.
3. **Duration.** `vercel.json` sets `maxDuration: 300` for the backend's function (Hobby's limit, which the docs confirm). `RUN_TIMEOUT_SECONDS` stays at 280 or less.
4. **Removed from Vercel.** The `[[tool.vercel.subscribers]]` entry in `pyproject.toml`, so no idle consumer is built. `vercel_backend/queue_consumer.py` stays for `RUN_EXECUTION=queue` and its local rehearsal.
5. **Validation.** On Vercel, a non-Postgres database is refused for every mode except inline (the message no longer mentions Queues).
6. **Web.** `executeRun(runId)` in the API client, with a 310 s limit (just above the server's 300 s). The run view starts queued runs. The start request keeps its 45 s limit.

**Out of scope:**
- **Voice-started research.** Voice is off on Vercel. A run started by voice is recorded as queued and isn't executed in this mode. The deploy guide says what a voice worker would need to call.
- **The queue itself.** Its code, tests and rehearsal remain, for anyone who wants it later.
- **Live models.** A live research can take minutes. It needs `RUN_TIMEOUT_SECONDS` of 280 or less and is not yet tested on Vercel.

## Impact

| Who or what | Impact |
|---|---|
| Visitors | Research starts and completes as the page is open. A reload of a queued run starts it |
| Operator | No queue, no consumer and no subscriber to deploy. Logs show `research run … executed in its request: completed` |
| API | One new route, `POST /api/research/{run_id}/execute`. The schema and web types are regenerated. No other contract changes |
| Data | No schema change and no migration |
| Security | Unchanged. The execute route sits behind the same site password and client header as the other routes |

## Risks and how they're handled

| Risk | Handling |
|---|---|
| The platform ends the execute request early (the page closed, or the 300 s limit) | The run stays `running` until the stale-run sweep marks it `RESEARCH_TIMED_OUT`; the page says so. Keep `RUN_TIMEOUT_SECONDS` at 280 or less |
| The page closes before it starts a queued run | The next page that opens the run starts it. The sweep marks a run that is never opened `RESEARCH_NOT_STARTED` |
| Two pages start the same run | The atomic claim lets one execute; the other returns at once |
| The execute request never reaches the backend | The run stays queued; the page that opened it shows it, and reloading tries again |

**Rollback:** revert the merge commit and redeploy. There's no data migration to undo. On Vercel the previous queue path would need its subscriber restored in `pyproject.toml`, and that path has not delivered runs on the live site.

## Testing done

- **New tests:** `tests/integration/test_research_request_mode.py`, 5 tests. The start request only records the run. Execute completes it. A repeat execute does nothing. An unknown run is 404. A re-run is executed the same way. The Vercel profile test expects `request`.
- **Full suite:** 395 passed, 6 skipped. Ruff, the schema check, web typecheck, lint and build are clean.
- **Production-like rehearsal:** a read-only copy of the repository, `VERCEL=1`, the `.env.example` values in the environment, `vercel_backend.asgi:app` on Postgres 14. The start request took 0.03 s and recorded the run. Execute took 0.16 s and completed it with a brief. A repeat execute did nothing. Health reported production with three servers ready.
- **Browser:** against that backend, a run created by API only was started and completed by the page itself, and the brief rendered in headless Chrome.

## What this does not prove

- **The platform's handling of a long execute request on Vercel.** It is documented as a 300 s function, but the live site has not yet run one to completion.
- **Live models.** Their duration and the cost of a 280 s request.

## Deployment steps (after approval)

1. Merge `fix/research-runs-execute-in-request` into `main`. Vercel redeploys both services.
2. Open https://marketlens-lemon.vercel.app/ and start an AAPL research. Expect it to complete within about a minute, and its brief to open without a reload.
3. Check the `backend` logs for `POST /api/research/…/execute` and `research run … executed in its request: completed`.
4. If a run stays running, note its time and the logs for that request, and send them.

## Acceptance criteria

- [ ] A research started on the live site completes within a minute, and its brief opens.
- [ ] The run's page shows the progress steps while it runs.
- [ ] A re-run on the live site completes the same way.
- [ ] Cancelling a running research stops it.

## Approval

- [ ] Reviewed: code and tests
- [ ] Approved to merge and deploy
