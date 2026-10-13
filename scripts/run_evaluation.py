"""Run the deterministic evaluation suite and write reports/evaluation/latest.{json,md}.

Usage: ``uv run python scripts/run_evaluation.py`` (fixture data, fixture LLM, no network, no
credentials). Exit status is 1 when any case fails or a release gate is missed. Re-running
overwrites the latest report; nothing else is modified.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ["MARKETLENS_DOTENV"] = "disabled"

from marketlens_core.config import Settings  # noqa: E402

for _name in Settings.model_fields:  # deterministic mode: never pick up local keys or modes
    os.environ.pop(_name.upper(), None)

from marketlens_core import __version__  # noqa: E402

from tests.evaluation.harness import load_cases, run_cases, summarize  # noqa: E402

REPORT_DIR = REPO_ROOT / "reports" / "evaluation"
SCHEMA_VERSION = 1


def _git(git: str, *args: str) -> str:
    command = [git, *args]
    return subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603


def git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown (git not installed)"
    try:
        head, dirty = _git(git, "rev-parse", "--short", "HEAD"), _git(git, "status", "--porcelain")
    except subprocess.CalledProcessError:
        return "no commits yet (working tree not committed)"
    return head + ("-dirty" if dirty else "")


def markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# MarketLens evaluation report",
        "",
        f"- Generated: {report['generated_at']} · app {report['app_version']} · commit `{report['git_commit']}`",
        f"- Mode: **{report['modes']['data_mode']} data, {report['modes']['llm_mode']} LLM** (synthetic fixtures; "
        "deterministic, no network or credentials). This measures system behavior, not market or business outcomes.",
        f"- Cases: **{s['passed']}/{s['cases']} passed**; safety-critical: {s['safety_critical']['cases']} "
        f"({len(s['safety_critical']['failed'])} failed); wall time {report['elapsed_seconds']} s",
        "",
        "## Release gates (EVALUATION.md §3, computed per §11)",
        "",
        "| Metric | Value | Target | Result |",
        "|---|---:|---:|---|",
    ]
    for key, gate in s["gates"].items():
        lines.append(
            f"| {key.replace('_', ' ')} | {gate['value']} | {gate['target']} | {'pass' if gate['passed'] else '**FAIL**'} |"
        )
    c = s["counts"]
    lines += [
        "",
        f"Numeric checks: {c['numeric_checks']} (failures {c['numeric_failures']}); material claims grounded "
        f"{c['grounded_material_claims']}/{c['material_claims']}; missing-category labeling {c['labeling_ok']}/"
        f"{c['labeling_needed']}; invalid inputs blocked {c['invalid_inputs_blocked']}/{c['invalid_inputs_expected']}; "
        f"unauthorized or misused write/voice attempts rejected {c['unauthorized_attempts_rejected']}/"
        f"{c['unauthorized_attempts_expected']}; "
        f"tool-scored cases {c['tool_scored_cases']}.",
        "",
        "## Cases by category",
        "",
        "| Category | Cases | Passed | Planned |",
        "|---|---:|---:|---:|",
    ]
    for category, row in s["by_category"].items():
        lines.append(f"| {category.replace('_', ' ')} | {row['cases']} | {row['passed']} | {row['target']} |")
    lat = s["latency_ms"]
    lines += [
        "",
        "## Latency and calls (fixture mode, measured)",
        "",
        f"- Research runs measured: {lat['runs_measured']}; run duration p50 {lat['run_p50']} ms, p95 {lat['run_p95']} ms",
        f"- Median MCP tool calls per run: {s['per_run']['mcp_calls_median']}; median LLM calls per run: {s['per_run']['llm_calls_median']}",
        "- Step latency p50 (ms): " + ", ".join(f"{k} {v:.0f}" for k, v in lat["steps_p50"].items()),
        "- Cost: not measured (fixture LLM; no metered usage).",
        "",
        "## Failed cases",
        "",
    ]
    failed = [r for r in report["cases"] if not r["passed"]]
    lines += [f"- `{r['case_id']}`: " + "; ".join(r["failures"][:3]) for r in failed] or ["None."]
    lines += [
        "",
        "## Not run in this report",
        "",
        "- Live provider, real-model (OpenAI) and real-speech (LiveKit) checks: credential-gated; see `PROJECT_STATUS.md`.",
        "- Conversational voice behavior with a real model (clarifying questions, refusals) is covered by "
        "credential-gated tests in `services/voice_agent/tests/live`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filter", help="only run cases whose id contains this text")
    args = parser.parse_args()
    cases = load_cases()
    if args.filter:
        cases = [c for c in cases if args.filter in c.case_id]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="marketlens-eval-") as workdir:
        results = run_cases(cases, Path(workdir))
    elapsed = round(time.monotonic() - started, 1)
    summary = summarize(results)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app_version": __version__,
        "git_commit": git_commit(),
        "python": platform.python_version(),
        "modes": {"data_mode": "fixtures", "llm_mode": "fixture", "voice_mode": "per case (LiveKit not contacted)"},
        "elapsed_seconds": elapsed,
        "summary": summary,
        "cases": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "kind": r.kind,
                "safety_critical": r.safety_critical,
                "description": r.description,
                "passed": r.passed,
                "failures": r.failures,
                "metrics": r.metrics,
                "duration_s": r.duration_s,
            }
            for r in results.values()
        ],
    }
    if not args.filter:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "latest.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        (REPORT_DIR / "latest.md").write_text(markdown(report), encoding="utf-8")
    print(f"{summary['passed']}/{summary['cases']} cases passed in {elapsed}s")
    for key, gate in summary["gates"].items():
        print(f"  {'ok  ' if gate['passed'] else 'FAIL'} {key}: {gate['value']} (target {gate['target']})")
    if not args.filter:
        print(f"report: {REPORT_DIR / 'latest.md'}")
    ok = not summary["failed"] and all(g["passed"] for g in summary["gates"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
