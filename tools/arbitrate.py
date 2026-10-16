#!/usr/bin/env python3
"""Arbitration CLI for local code reviews and file analysis."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbitration.orchestrator import ArbitrationOrchestrator, TaskType
from tools.pr_context import load_pull_request_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)


def _git_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Not inside a Git repository: {path}")
    return Path(result.stdout.strip()).resolve()


def _prepare_review_context(
    orch: ArbitrationOrchestrator,
    target: str,
    target_branch: str,
    context_dir: str | None,
) -> tuple[Path, str]:
    if target.startswith("PR#"):
        number = target[3:]
        if not number.isdigit():
            raise ValueError(f"Invalid pull request target: {target}")
        repo_path = _git_root(Path.cwd())
        load_pull_request_context(orch, repo_path, number)
        subject = f"PR #{number} in {repo_path.name}"
    else:
        target_path = Path(target).expanduser().resolve()
        if not target_path.exists():
            raise FileNotFoundError(f"Review target does not exist: {target_path}")
        is_file = target_path.is_file()
        repo_path = _git_root(target_path.parent if is_file else target_path)
        if is_file:
            try:
                relative = target_path.relative_to(repo_path).as_posix()
            except ValueError as exc:
                raise ValueError(f"Review file must be inside its Git repository: {target_path}") from exc
            orch.context_manager.load_git_diff(repo_path, target_branch, paths=[relative])
            # Include the complete requested file even if it has no diff against the target.
            orch.context_manager.load_files([target_path])
            subject = f"file {relative} in {repo_path.name}"
        else:
            orch.context_manager.load_git_diff(repo_path, target_branch)
            subject = f"changes in {repo_path.name} against {target_branch}"
            if not orch.context_manager.changed_files:
                raise ValueError(
                    f"No changes found between the worktree and {target_branch}. "
                    "Check the target branch or select a changed file."
                )
            orch.context_manager.load_related_context(
                repo_path, orch.context_manager.changed_files
            )

    if context_dir:
        directory = Path(context_dir).expanduser().resolve()
        orch.context_manager.load_directory(directory, max_files=30)
    return repo_path, subject


def cmd_code_review(args: argparse.Namespace) -> None:
    """Run one or more worker/arbiter review tiers."""
    prior_report = ""
    for tier in range(1, args.tiers + 1):
        orch = ArbitrationOrchestrator(
            TaskType.CODE_REVIEW,
            (
                f"Review {args.repo_path} for correctness, security, performance, "
                f"and test coverage. Tier {tier} of {args.tiers}."
                + (
                    "\n\nCritically re-review the previous tier's report. Verify each "
                    "finding against the supplied source; reject unsupported claims.\n\n"
                    + prior_report
                    if prior_report
                    else ""
                )
            ),
        )
        _, subject = _prepare_review_context(
            orch, args.repo_path, args.target_branch, args.context_dir
        )
        logger.info("Starting code review tier %s/%s: %s", tier, args.tiers, subject)
        orch.auto_phases(num_phases=args.phases)
        orch.run()
        prior_report = orch.report()
        print(f"\n=== REVIEW TIER {tier}/{args.tiers} ===\n")
        print(prior_report)


def _run_file_analysis(args: argparse.Namespace, task_type: TaskType, description: str) -> None:
    files = [Path(name).expanduser().resolve() for name in args.files]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError("Input file(s) not found: " + ", ".join(missing))
    orch = ArbitrationOrchestrator(task_type, description.format(count=len(files)))
    orch.context_manager.load_files(files)
    orch.auto_phases(num_phases=args.phases)
    orch.run()
    print(orch.report())


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-phase model arbitration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("code-review", help="Review a repository, file, or PR#N")
    review.add_argument("repo_path", help="Git repository directory, file path, or PR#123")
    review.add_argument("--target-branch", default="main", help="Base branch for local changes")
    review.add_argument("--context-dir", help="Additional directory context")
    review.add_argument("--phases", type=int, default=1, help="Worker/arbiter phases per tier")
    review.add_argument("--tiers", type=int, default=1, help="Number of review/re-review tiers")
    review.set_defaults(func=cmd_code_review)

    bug = subparsers.add_parser("bug-analysis", help="Analyze bugs across files")
    bug.add_argument("files", nargs="+", help="Code files to analyze")
    bug.add_argument("--phases", type=int, default=2)
    bug.set_defaults(func=lambda args: _run_file_analysis(
        args, TaskType.BUG_ANALYSIS, "Analyze bug across {count} files"
    ))

    audit = subparsers.add_parser("security-audit", help="Run a security-focused audit")
    audit.add_argument("files", nargs="+", help="Code files to audit")
    audit.add_argument("--phases", type=int, default=3)
    audit.set_defaults(func=lambda args: _run_file_analysis(
        args, TaskType.SECURITY_AUDIT, "Security audit of {count} files"
    ))

    args = parser.parse_args()
    if getattr(args, "phases", 1) < 1:
        parser.error("--phases must be at least 1")
    if getattr(args, "tiers", 1) < 1:
        parser.error("--tiers must be at least 1")

    try:
        args.func(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
