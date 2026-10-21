# CR-005: Live research stops at a failed model step, and says why

| | |
|---|---|
| **Status** | Ready for review. Code, tests and documentation are done on branch `fix/live-model-reasons`, from `fix/real-data-only-hosted` (D-104); not yet deployed |
| **Requested** | 2026-10-21, from the live site: a live research was "partial", with "The AI model couldn't finish a step" and summaries written by code in place of model analysis |
| **Priority** | High: the user asked for real data only, and the hosted site was presenting summaries as research |
| **Decision record** | D-105 in [`PROJECT_STATUS.md`](../../PROJECT_STATUS.md) |
| **Follows** | [CR-004](CR-004-real-data-only-on-the-hosted-site.md) (D-104) |

## Summary

Live market data now works on the hosted site. The AI steps fail, and the run falls back to summaries written by the code. The page shows only "couldn't finish a step", so the cause is hidden.

This change does two things. A model step that can't complete now ends a live run, with the provider's own reason on the page. And the provider's errors name their cause: a model the key can't use, an account without credit, a rejected key, or an endpoint that can't be reached.

## Problem

| What the live site showed | What it meant |
|---|---|
| "Partial" status, four failed specialist steps and the supervisor, "deterministic summary was used" | the model failed on every call, and the workflow wrote a summary in its place |
| "The AI model couldn't finish a step … Local models can be slow" | a generic message; the provider's reason was dropped |
| Health: `live_llm: true` | the model is configured, but its calls fail |

**Likely causes, checked with the local key and the local settings (not printed):**
- `OPENAI_MODEL=qwen2.5:7b` isn't an OpenAI model. OpenAI answers `404: The model does not exist or you do not have access to it`.
- The local key is valid (OpenAI lists 127 models for it), but its account has no credit: `429: You have no credits remaining`. If the Vercel key is the same, every call fails for that reason alone.
- `OPENAI_BASE_URL` on the laptop, if copied to Vercel, can't be reached from the hosted site.

The Vercel values themselves weren't visible from here, so these are the likely causes, not confirmed ones.

## The change

1. **Live runs stop at a failed model step.** The specialists, the supervisor and the evidence review raise instead of falling back. The step is recorded as failed with the reason, and the run fails. Parallel specialists are cancelled when one fails, so none keeps calling the model.
2. **The provider's reason is kept.** The provider's message (key removed, one line) appears in the run's error and on the page:
   - a model the key can't use: "Check OPENAI_MODEL";
   - no credit left: a separate message, not retried, that says to add billing;
   - a rejected key;
   - an endpoint that can't be reached, named by host only.
3. **The page's model card.** A failed run says the brief wasn't written, shows the reason, and offers Re-run.

**Out of scope:**
- **Fixture mode** keeps its labelled fallbacks, for the tests and a local demo.
- **The model choice.** Which hosted model to use is your decision.

## Impact

| Who or what | Impact |
|---|---|
| Visitors | A live research either completes or fails with a reason. No summary written by code appears in a live brief |
| Operator | The reason tells you which setting to fix |
| API | No new routes. The run's error messages and the provider error details are more specific |
| Data | No schema change |
| Cost | A failed run stops early, so no further model calls are made after the first failure |

## Risks and how they're handled

| Risk | Handling |
|---|---|
| Runs fail more often while the model is unreliable | That is the intended trade: a failed run is shown as failed, not as a partial report. Re-run after fixing the cause |
| The provider's message could contain something sensitive | The key is removed from it, it is cut to one line, and the full exception is logged on the server only |
| Cancelling the other specialists leaves them half done | Cancelled tasks write no partial output, and the run is marked failed |

**Rollback:** revert the merge and redeploy. No data changes.

## Testing done

- **Provider tests:** a missing model names `OPENAI_MODEL`; no credit is not a rate limit; the key never appears in the message. Existing error-mapping tests still pass.
- **Pipeline test:** with the live rules switched on, a failed news step ends the run as failed, with its reason, and produces no fallback claims.
- **Full suite:** 403 passed, 6 skipped. Ruff and the schema check are clean. Web typecheck and lint are clean.
- **Local checks** with the local key (not printed): the key lists models (HTTP 200); a structured-output call to `gpt-4.1-mini` returned "no credits"; `qwen2.5:7b` returned "does not exist".

## What this does not prove

- **A complete hosted research.** It needs a hosted model with credit, and the Vercel values set correctly. Those are your decisions.

## Deployment steps (after approval)

1. On Vercel, check `OPENAI_MODEL` (an OpenAI model name, such as `gpt-4.1-mini`), that `OPENAI_BASE_URL` is removed, and that the account has credit.
2. Merge the pull requests in order: D-103, D-104, then this one.
3. Redeploy. Re-run the NVDA research. It should either complete with live data, or fail with a reason you can act on.

## Acceptance criteria

- [ ] A live research completes with live data and no summary written by code, or it fails with a reason that names the setting or account to fix.
- [ ] A failed run says the brief wasn't written, and offers Re-run.

## Approval

- [ ] Reviewed: code and tests
- [ ] Vercel model settings checked
- [ ] Approved to merge and deploy
