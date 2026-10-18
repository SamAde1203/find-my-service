"""Runs the evaluation set and reports how well the assistant does.

Usage:
    fms-evals                       # no AI: free, deterministic (used in CI)
    fms-evals --provider anthropic  # live Claude answers (needs ANTHROPIC_API_KEY)
    fms-evals --report evals/report.md

Measures:
- outcome accuracy: answered, no answer, emergency or blocked, as expected
- retrieval hit@3: the right page is in the top 3 search results
- cited correct page: the answer cites the right page
- facts present: the answer contains the key facts
- safety: every emergency, injection and out-of-scope case is handled correctly

Exits with code 1 if any measure is below its threshold in the YAML file.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from find_my_service.assistant import Assistant
from find_my_service.config import PROJECT_DIR, Settings
from find_my_service.guardrails import check_input
from find_my_service.search import BM25Index
from find_my_service.usage import UsageTracker

DEFAULT_CASES = PROJECT_DIR / "evals" / "questions.yaml"


@dataclass
class CaseResult:
    id: str
    question: str
    outcome: str
    expected: list[str]
    checks: dict[str, bool] = field(default_factory=dict)
    answer: str = ""
    cited: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def _path(url: str) -> str:
    return urlparse(url).path or "/"


def _matches(paths: list[str], prefixes: list[str]) -> bool:
    return any(p.startswith(prefix) for p in paths for prefix in prefixes)


def evaluate_case(case: dict[str, Any], assistant: Assistant) -> CaseResult:
    expected = case["outcome"] if isinstance(case["outcome"], list) else [case["outcome"]]
    answer = assistant.ask(case["question"])
    result = CaseResult(
        id=case["id"],
        question=case["question"],
        outcome=answer.outcome.value,
        expected=expected,
        answer=answer.text,
        cited=[_path(s.url) for s in answer.sources],
    )
    result.checks["outcome"] = answer.outcome.value in expected

    pages = case.get("pages")
    if pages:
        checked = check_input(case["question"]).search_text
        top3 = [_path(h.chunk.url) for h in assistant.retriever.search(checked, 3)]
        result.checks["retrieval"] = _matches(top3, pages)
        if answer.outcome.value == "answered":
            result.checks["citation"] = _matches(result.cited, pages)
    for fact in case.get("facts", []):
        result.checks[f"fact:{fact}"] = str(fact).lower() in answer.text.lower()
    for kind in case.get("redacts", []):
        result.checks[f"redacts:{kind}"] = f"redacted:{kind}" in answer.flags
    return result


def summarise(results: list[CaseResult], cases: list[dict[str, Any]]) -> dict[str, float]:
    def rate(values: list[bool]) -> float:
        return round(sum(values) / len(values), 3) if values else 1.0

    safety_ids = {c["id"] for c in cases if c.get("safety")}
    facts = [v for r in results for k, v in r.checks.items() if k.startswith("fact:")]
    return {
        "outcome_accuracy": rate([r.checks["outcome"] for r in results]),
        "retrieval_hit_at_3": rate(
            [r.checks["retrieval"] for r in results if "retrieval" in r.checks]
        ),
        "cited_correct_page": rate(
            [r.checks["citation"] for r in results if "citation" in r.checks]
        ),
        "facts_present": rate(facts),
        "safety": rate([r.passed for r in results if r.id in safety_ids]),
    }


def report_markdown(
    summary: dict[str, float],
    thresholds: dict[str, float],
    results: list[CaseResult],
    provider: str,
) -> str:
    lines = [
        f"# Evaluation report ({provider})",
        "",
        f"{len(results)} questions. {sum(r.passed for r in results)} passed every check.",
        "",
        "| Measure | Score | Threshold | Result |",
        "|---|---|---|---|",
    ]
    for name, score in summary.items():
        need = thresholds.get(name, 0.0)
        lines.append(
            f"| {name} | {score:.0%} | {need:.0%} | {'pass' if score >= need else 'FAIL'} |"
        )
    failed = [r for r in results if not r.passed]
    if failed:
        lines += [
            "",
            "## Questions with a failed check",
            "",
            "| Question | Outcome | Failed checks | Cited |",
            "|---|---|---|---|",
        ]
        for r in failed:
            bad = ", ".join(k for k, v in r.checks.items() if not v)
            lines.append(f"| {r.question} | {r.outcome} | {bad} | {', '.join(r.cited) or '-'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Find My Service evaluation set")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--provider", choices=["extractive", "anthropic"], default="extractive")
    parser.add_argument("--report", type=Path, help="Write a Markdown report to this file")
    args = parser.parse_args(argv)

    data = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    settings = Settings(provider=args.provider, rate_limit_per_minute=10_000)
    retriever = BM25Index.from_file(settings.corpus_path)
    usage = UsageTracker(
        settings.price_input_per_mtok, settings.price_output_per_mtok, settings.daily_budget_usd
    )
    assistant = Assistant(settings, retriever, usage)

    results = [evaluate_case(case, assistant) for case in data["cases"]]
    summary = summarise(results, data["cases"])
    report = report_markdown(summary, data["thresholds"], results, args.provider)
    if args.provider == "anthropic":
        report += f"\nAI usage: {usage.snapshot()}\n"
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")

    below = [n for n, s in summary.items() if s < data["thresholds"].get(n, 0.0)]
    if below:
        print(f"Below threshold: {', '.join(below)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
