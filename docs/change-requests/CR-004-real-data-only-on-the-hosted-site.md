# CR-004: The hosted site shows real data only

| | |
|---|---|
| **Status** | Ready for review. Code, tests and documentation are done on branch `fix/real-data-only-hosted`, from `fix/research-runs-execute-in-request` (D-103, not yet on `main`); the Vercel variables must be set before or with the deploy |
| **Requested** | 2026-10-21, from the live site: a completed research showed "DEMO FIXTURES", "Scripted demo analysis" and synthetic data |
| **Priority** | High: the hosted site presented synthetic data as research |
| **Decision record** | D-104 in [`PROJECT_STATUS.md`](../../PROJECT_STATUS.md) |
| **Follows** | [CR-003](CR-003-run-research-in-the-page-request.md) (D-103) |

## Summary

The live site ran with the defaults: demo mode, fixture data and the scripted model. Those are right for a laptop, and wrong for a site that people use for research. This change makes the hosted site refuse to run on demo data, and makes live mode leave demo runs out of what the site shows. The demo stays for local development, the tests and the evaluation.

## Problem

| Where | What the user saw |
|---|---|
| Banner | "DEMO FIXTURES — synthetic, illustrative data" |
| Brief | "Scripted demo analysis (no AI model)", and a "Synthetic demo data" notice |
| Dates | fixture dates (prices through 30 September 2026) |
| Health | `app_mode: demo`, `data_mode: fixtures`, `llm_mode: fixture` |

**Cause:** no live settings were set in the Vercel project, so the defaults applied. The local `.env` is already live (Alpha Vantage, SEC, a model). Its model runs on the laptop through Ollama (`OPENAI_BASE_URL` on 127.0.0.1), which Vercel can't reach, so the hosted site needs its own hosted model.

## The change

1. **The hosted site refuses demo data.** On Vercel, `validate_settings` requires `APP_MODE=live`, `DATA_MODE=live` and `LLM_MODE=openai`, plus `OPENAI_API_KEY`. Otherwise the site doesn't start, and the red bar names the settings. The existing live checks (Alpha Vantage key, SEC contact, model name) still apply.
2. **Live mode leaves demo runs out.** With `DATA_MODE=live`, the run list, the search and `GET /api/research/{id}` exclude runs whose `data_mode` is `fixture` (404 for a direct link). The rows stay in the database. Nothing is deleted.
3. **Documentation.** The deploy guide's live-data section becomes the real-data section (required variables, and what not to copy from `.env`). README and `.env.example` say the hosted site is always live.

**Out of scope:**
- **Removing fixtures from the code.** The tests, the evaluation and the local demo depend on them.
- **Deleting the demo rows.** It is destructive, so it needs your confirmation.
- **Saved briefs.** They aren't in the interface; the API route still lists them.

## Settings needed on Vercel

| Variable | Value |
|---|---|
| `APP_MODE`, `DATA_MODE`, `LLM_MODE` | `live`, `live`, `openai` |
| `ALPHA_VANTAGE_API_KEY` | your key |
| `SEC_USER_AGENT` | `MarketLens/0.1 contact=<a real contact address>` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | your OpenAI key and a hosted model that supports structured outputs |
| `RUN_TIMEOUT_SECONDS` | `240` |

Not `OPENAI_BASE_URL` from `.env`.

## Impact

| Who or what | Impact |
|---|---|
| Visitors | Every banner, brief and figure is real data. Old demo runs no longer appear |
| Operator | Must set the variables, or the site shows a setup problem instead of running |
| API | None. The run routes return 404 for a hidden demo run |
| Data | No schema change. Demo rows remain, hidden |
| Local development | Unchanged. Demo, tests and evaluation still use fixtures |

## Risks and how they're handled

| Risk | Handling |
|---|---|
| Deploying before the variables are set | The site shows the setup problems in its red bar; nothing runs on demo data |
| A hosted model is slow or unavailable | Runs follow the existing time limit and fall back to a labelled deterministic summary, marked partial. The brief says so |
| Alpha Vantage's free daily quota is shared by everyone on the site | Existing behaviour; the status shows it, and the deploy guide says so |

**Rollback:** revert the merge and redeploy. The variables can stay.

## Testing done

- **Local live check:** the backend on the local live settings (keys not printed) against a scratch database. The data status reports `is_fixture: false`. A comparison of NVIDIA returns real prices from Alpha Vantage, with the company resolved by SEC.
- **Tests:** the hosted tests set real-data settings. New tests refuse demo data on Vercel, refuse a model on a laptop, and check that live mode hides demo runs while a demo site still shows them (25 passed in the affected files).

## What this does not prove

- **A full hosted research on the real model.** It needs the hosted settings and a hosted model, which only you can provide. A research takes minutes with a live model.

## Deployment steps (after approval)

1. Set the variables above in Vercel (Production and Preview).
2. Merge `fix/research-runs-execute-in-request` (D-103), then `fix/real-data-only-hosted`, into `main`, and let Vercel redeploy. The live site already runs D-103, so `main` must catch up with it.
3. Open https://marketlens-lemon.vercel.app/. The banner should read LIVE DATA, and `/api/health` should show `data_mode: live`.
4. Start an AAPL research with real data.
5. Decide what to do with the demo rows: keep them hidden, or delete them (ask first).

## Acceptance criteria

- [ ] The hosted banner reads LIVE DATA, and `/api/health` shows `app_mode: live`, `data_mode: live`, `llm_mode: openai`.
- [ ] A research on the live site completes with live data, with no "Scripted" or "Synthetic" notice.
- [ ] Demo runs are not listed, searchable or openable on the live site.

## Approval

- [ ] Reviewed: code and tests
- [ ] Variables set in Vercel
- [ ] Approved to merge and deploy
