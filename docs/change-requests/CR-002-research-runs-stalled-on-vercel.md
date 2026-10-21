# CR-002: Research runs that stall on Vercel, and what the page says about them

| | |
|---|---|
| **Status** | Ready for review. Code, tests and documentation are done on branch `fix/research-runs-on-vercel` (from `main`); not yet deployed |
| **Requested** | 2026-10-21, from the live site (https://marketlens-lemon.vercel.app) after CR-001 was deployed |
| **Priority** | High: starting a research could leave the page spinning, and a queued run failed with a message that didn't say why |
| **Decision record** | D-102 in [`PROJECT_STATUS.md`](../../PROJECT_STATUS.md) |
| **Follows** | [CR-001](CR-001-vercel-runtime-fixes.md) (D-101) |

## Summary

Two things on the live site:

1. **The start button kept spinning.** The page waited for `POST /api/research` with no time limit, and the backend waited for the publish to Vercel Queues with no time limit. A slow publish left the form in its "Starting…" state indefinitely.
2. **A failed run gave an unhelpful reason.** A run that was published but never finished was marked by the stale-run sweep with "didn't finish in time", whether it never started or started and stalled. The card titled every unknown failure "Something limited this report".

This change bounds both waits, gives each way a run can stop its own reason and code, and makes the consumer log each step so the runtime logs show where a run stopped. It changes no data, no routes and no `vercel.json`.

## Problem

| Where | Before | Evidence |
|---|---|---|
| Start button | Spinner until the browser gave up (no limit) | Screenshot: the research bar showing the spinner, with an NVDA question typed and the MSFT run open behind it |
| Publish | No limit on `QueueClient.send` inside the request | `services/backend/app/services/run_manager.py` |
| Stale runs | One message for every run that didn't finish | `repositories.py` `fail_stale_runs` |
| Failure card | Unknown codes titled "Something limited this report" | `apps/web/components/run/issues.tsx` |
| Consumer | Failures retried silently; no log line per delivery | `vercel_backend/queue_consumer.py` |

**Not established:** whether the consumer runs on the live deployment. The build log and the consumer's runtime logs will show that (`research consumer loaded`). See "What this does not prove" below.

## The change

1. **Bounded requests.**
   - The web app gives every API call 30 s, and the start request 45 s. A call that times out says so, and the button returns to ↑.
   - The backend bounds the publish to Vercel Queues at 15 s. Past that, the run fails at once with `RESEARCH_QUEUE_UNAVAILABLE`.
2. **A reason for every way a run stops without a brief.**

   | Code | When | Shown as |
   |---|---|---|
   | `RESEARCH_QUEUE_UNAVAILABLE` | the publish failed or timed out | The run couldn't be started |
   | `RESEARCH_NOT_STARTED` | still queued past the limit (the stale-run sweep) | The research service didn't pick the run up |
   | `RESEARCH_TIMED_OUT` | still running past the limit (the stale-run sweep) | The run ran out of time |
   | `RESEARCH_STOPPED` | the consumer failed on its last delivery, or the run was interrupted | The research service stopped unexpectedly |

   Older runs keep their stored code. A failed run with an unknown code is now titled "The research stopped before it finished", with its own message.
3. **Consumer observability and a fast final failure.**
   - The consumer logs when it loads, and each delivery (`research run … delivery N of 3`), with the traceback of any failure.
   - Failed deliveries are raised so Vercel Queues retries them (three in all, 30 s apart). On the last one, the run is marked `RESEARCH_STOPPED` at once. Before, it waited for the sweep: about four minutes, with the wrong reason.
4. **One retry on a failed run.** The card no longer repeats the header's **Re-run**.

**Out of scope:**
- **Vercel's consumer discovery.** The build reads the subscriber from `pyproject.toml`, as the Vercel Python SDK documents. Whether this services project gets its consumer built is confirmed only by the logs (below).
- **Synchronous fallback.** Running research inside the request would need the function to stay alive for the whole run, and it's a design decision for you, not a fix.
- **Voice.** Unchanged (CR-001 §out of scope).

## Impact

| Who or what | Impact |
|---|---|
| Visitors | No endless spinner. A failed run says why, in plain words, with one Re-run |
| Operator | Consumer and publish lines in the runtime logs, which show where a run stopped |
| API | Four new run error codes (`RESEARCH_QUEUE_UNAVAILABLE`, `RESEARCH_NOT_STARTED`, `RESEARCH_TIMED_OUT`, `RESEARCH_STOPPED`). The OpenAPI schema and web types are regenerated. No other contract changes |
| Data | No schema change and no migration. Existing runs keep their stored reasons |
| Security | No secrets in messages or logs beyond the existing tracebacks. Nothing new is exposed |

## Risks and how they're handled

| Risk | Handling |
|---|---|
| A publish that times out may still have been accepted | The run is marked failed, and if the consumer later delivers it, the claim finds it no longer queued and skips it. The stale-run sweep and the claim are both atomic |
| A retried start may create a second run | The page says to check the history before starting again |
| The consumer's final-failure path can't reach the database | Logged, and the stale-run sweep still stops the run |
| 30 s limit cuts a slow but healthy request | Only the page's wait ends. The request itself isn't cancelled on the server; the run is still created, and it appears in the history |

**Rollback:** revert the merge commit and redeploy. There's no data migration to undo.

## Testing done

- **New tests:** 5 (390 passed, 6 skipped in total). Among them:
  - the trigger the build reads from the consumer module (topic, three attempts, 30 s apart);
  - an early failure is raised so the queue retries it;
  - the last failure records the run as stopped, with a reason;
  - a published run is delivered through the SDK's push path to the real consumer and completes;
  - a hanging publish fails the run within 10 s, with its reason;
  - queued and running stale runs get different reasons.
- **Postgres:** the queue, consumer and Postgres test files also pass against a throwaway Postgres 14 (30 tests).
- **Checks:** ruff, web typecheck, lint and production build clean; schema check current.
- **Screen:** a local demo run in each failure state, rendered in headless Chrome. Each card shows its reason, "What you can do: Press Re-run to try again.", and one Re-run in the header. The send button shows ↑ when idle. The spinner-while-waiting state was not rendered in a browser; it is covered by the 45 s limit in code and by typechecking.

## What this does not prove

- **The consumer on Vercel.** Local tests use the SDK's embedded queue service, which dispatches like Vercel, but they don't run Vercel's build. The first deployment's runtime logs should show `research consumer loaded`, then `research run … delivery 1 of 3`, then `… completed`.
- **The publish from Vercel.** Its latency and any token issue show only in the logs, as `could not publish research run`.

## Deployment steps (after approval)

1. Merge `fix/research-runs-on-vercel` into `main`. Vercel redeploys both services.
2. Open the live site and start an AAPL research. Expect Queued, then Completed within about a minute.
3. If it stalls, read the `backend` and consumer logs (the names are in the deploy guide's troubleshooting table) and send them with the run's ID.

## Acceptance criteria

- [ ] Pressing Enter to start a research never leaves the button spinning for more than 45 s.
- [ ] A research started on the live site completes within a minute, and its brief opens.
- [ ] The backend logs show `research run … published to marketlens-research-runs`, and the consumer logs show `research consumer loaded` and `delivery 1 of 3`.
- [ ] A failed run shows its reason, and one **Re-run** button.

## Approval

- [ ] Reviewed: code and tests
- [ ] Approved to merge and deploy
