"""Deterministic evaluation suite (EVALUATION.md §4): every versioned case must pass, and the
release gates of §3/§10 must hold. The same harness produces the evaluation report via
``scripts/run_evaluation.py``."""

from __future__ import annotations

import pytest

from tests.evaluation.harness import CATEGORY_TARGETS, CaseResult, load_cases, run_cases, summarize

CASES = load_cases()


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict[str, CaseResult]:
    return run_cases(CASES, tmp_path_factory.mktemp("evaluation"))


def test_case_set_meets_the_planned_distribution() -> None:
    assert len(CASES) >= 40
    counts = {category: sum(c.category == category for c in CASES) for category in CATEGORY_TARGETS}
    assert all(counts[c] >= target for c, target in CATEGORY_TARGETS.items()), counts


@pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
def test_case(results: dict[str, CaseResult], case_id: str) -> None:
    result = results[case_id]
    assert result.passed, "\n".join(result.failures)


def test_release_gates(results: dict[str, CaseResult]) -> None:
    summary = summarize(results)
    failed = {k: v for k, v in summary["gates"].items() if not v["passed"]}
    assert not failed, failed
    assert summary["safety_critical"]["failed"] == []
