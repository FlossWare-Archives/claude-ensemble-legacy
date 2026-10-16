#!/usr/bin/env python3
"""Multi-phase arbitration using the canonical model-provider contract."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from providers import ClaudeCodeProvider, ModelProvider, ModelRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)


class TaskType(Enum):
    CODE_REVIEW = "code_review"
    BUG_ANALYSIS = "bug_analysis"
    DESIGN_VALIDATION = "design_validation"
    SECURITY_AUDIT = "security_audit"
    ARCHITECTURE = "architecture"


@dataclass
class WorkerResult:
    """Actual worker execution result. Errors are never represented as success."""

    model: str
    phase: int
    analysis: str = ""
    confidence: float | None = None
    key_findings: list[str] | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class TeachingSignal:
    """Structured explanation produced by an arbiter for later workers."""

    phase: int
    rationale: str
    supporting_evidence: tuple[str, ...] = ()
    rejected_alternatives: tuple[str, ...] = ()
    next_phase_questions: tuple[str, ...] = ()

    @classmethod
    def from_arbiter(cls, result: "ArbiterResult") -> "TeachingSignal":
        return cls(
            phase=result.phase,
            rationale=result.rationale,
            supporting_evidence=tuple(result.supporting_evidence or []),
            rejected_alternatives=tuple(result.rejected_alternatives or []),
            next_phase_questions=tuple(result.next_phase_questions or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "rationale": self.rationale,
            "supporting_evidence": list(self.supporting_evidence),
            "rejected_alternatives": list(self.rejected_alternatives),
            "next_phase_questions": list(self.next_phase_questions),
        }


@dataclass(init=False)
class ArbiterResult:
    """Actual arbiter execution result.

    The constructor preserves the pre-pipeline synthesis and selected_best
    keyword arguments while also accepting the new adjudicated_result and
    selected_worker names.
    """

    model: str
    phase: int
    adjudicated_result: str
    selected_worker: str | None = None
    rationale: str = ""
    supporting_evidence: list[str] | None = None
    rejected_alternatives: list[str] | None = None
    next_phase_questions: Optional[list[str]] = None

    def __init__(
        self,
        model: str,
        phase: int,
        synthesis: str | None = None,
        selected_best: str | None = None,
        rationale: str = "",
        next_phase_questions: Optional[list[str]] = None,
        *,
        adjudicated_result: str | None = None,
        selected_worker: str | None = None,
        supporting_evidence: list[str] | None = None,
        rejected_alternatives: list[str] | None = None,
    ) -> None:
        if synthesis is not None and adjudicated_result is not None and synthesis != adjudicated_result:
            raise ValueError("synthesis and adjudicated_result must match when both are supplied")
        if selected_best is not None and selected_worker is not None and selected_best != selected_worker:
            raise ValueError("selected_best and selected_worker must match when both are supplied")

        result = adjudicated_result if adjudicated_result is not None else synthesis
        if result is None:
            raise TypeError("ArbiterResult requires synthesis or adjudicated_result")

        worker = selected_worker if selected_worker is not None else selected_best
        self.model = model
        self.phase = phase
        self.adjudicated_result = result
        self.selected_worker = worker
        self.rationale = rationale
        self.supporting_evidence = supporting_evidence
        self.rejected_alternatives = rejected_alternatives
        self.next_phase_questions = next_phase_questions

    @property
    def synthesis(self) -> str:
        """Backward-compatible alias for the adjudicated pipeline result."""
        return self.adjudicated_result

    @property
    def selected_best(self) -> str | None:
        """Backward-compatible alias for the selected worker, when identified."""
        return self.selected_worker



@dataclass
class PhaseConfig:
    phase: int
    workers: list[str]
    arbiter: str
    instructions: str


class ModelPool:
    """Select configured Claude Code models with per-phase role isolation.

    Model labels may be reused by later phases. Within a phase, a model cannot
    be both a worker and an arbiter, and each role is selected at most once.
    This keeps the default three-model pool usable for multi-phase arbitration
    without pretending that three labels provide six distinct executions.
    """

    def __init__(self) -> None:
        self.all_models = {
            "cheap": ["haiku"],
            "balanced": ["sonnet"],
            "expensive": ["opus"],
        }
        self.used_workers: set[str] = set()
        self.used_arbiters: set[str] = set()

    def reset_phase(self) -> None:
        """Release role reservations before selecting models for a new phase."""
        self.used_workers.clear()
        self.used_arbiters.clear()

    def get_workers(self, count: int, tier: str = "balanced") -> list[str]:
        if count < 1:
            raise ValueError("count must be greater than zero")
        if tier not in self.all_models:
            raise ValueError(f"Unknown model tier: {tier}")

        available = [
            model
            for model in self.all_models[tier]
            if model not in self.used_workers and model not in self.used_arbiters
        ]
        for other_tier in self.all_models:
            if other_tier == tier:
                continue
            available.extend(
                model
                for model in self.all_models[other_tier]
                if model not in self.used_workers
                and model not in self.used_arbiters
                and model not in available
            )

        if len(available) < count:
            raise ValueError(
                f"Only {len(available)} worker model(s) are available; "
                f"{count} requested"
            )

        selected = available[:count]
        self.used_workers.update(selected)
        return selected

    def get_arbiter(self, tier: str = "expensive") -> str:
        if tier not in self.all_models:
            raise ValueError(f"Unknown model tier: {tier}")

        available = [
            model
            for model in self.all_models[tier]
            if model not in self.used_arbiters and model not in self.used_workers
        ]
        if not available:
            for other_tier in self.all_models:
                available.extend(
                    model
                    for model in self.all_models[other_tier]
                    if model not in self.used_arbiters
                    and model not in self.used_workers
                    and model not in available
                )
        if not available:
            raise ValueError(
                "No unused model is available for an arbiter in the current phase"
            )
        arbiter = available[0]
        self.used_arbiters.add(arbiter)
        return arbiter


class ContextManager:
    """Load the evidence supplied to workers and arbiters."""

    def __init__(self, task_type: TaskType):
        self.task_type = task_type
        self.context: dict[str, Any] = {}
        self.git_diff: str | None = None
        self.files: dict[str, str] = {}
        self.directories: dict[str, int] = {}
        self.changed_files: list[Path] = []
        self.stage_history: list[dict[str, Any]] = []

    def load_git_diff(
        self,
        repo_path: Path,
        target_branch: str = "main",
        paths: list[str] | None = None,
    ) -> None:
        """Load changes against the target branch, including staged/unstaged edits."""
        import subprocess

        path_args = ["--", *paths] if paths else []
        result = subprocess.run(
            ["git", "diff", target_branch, *path_args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
        self.git_diff = result.stdout

        result = subprocess.run(
            ["git", "diff", "--name-only", target_branch, *path_args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git diff --name-only failed: {result.stderr.strip()}")

        self.changed_files = [repo_path / name for name in result.stdout.splitlines() if name]
        self.load_files(self.changed_files)

    def load_directory(
        self,
        dir_path: Path,
        extensions: list[str] | None = None,
        max_files: int = 50,
    ) -> None:
        extensions = extensions or [".py", ".js", ".ts", ".tsx", ".go", ".java", ".sql", ".md"]
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise ValueError(f"Not a directory: {dir_path}")

        files_loaded = 0
        for ext in extensions:
            for file_path in dir_path.glob(f"**/*{ext}"):
                if files_loaded >= max_files:
                    break
                try:
                    content = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if len(content) < 50000:
                    self.files[str(file_path)] = content
                    files_loaded += 1
        self.directories[str(dir_path)] = files_loaded

    def load_files(self, file_paths: list[Path]) -> None:
        for path in file_paths:
            path = Path(path)
            if path.is_dir():
                self.load_directory(path)
                continue
            try:
                self.files[str(path)] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("Could not load %s: %s", path, exc)

    def load_related_context(self, repo_path: Path, changed_files: list[Path]) -> None:
        import re

        imports: set[str] = set()
        for file_path in changed_files:
            content = self.files.get(str(file_path), "")
            for pattern in (
                r'^\s*import\s+["\']([^"\']+)["\']',
                r'^\s*from\s+([^\s]+)\s+import',
                r'^\s*import\s+([^\s]+)',
            ):
                imports.update(re.findall(pattern, content, re.MULTILINE))

        for imp in list(imports)[:10]:
            for path in (
                repo_path / f"{imp.replace('.', '/')}.py",
                repo_path / f"{imp.replace('.', '/')}.js",
                repo_path / f"{imp.replace('.', '/')}.ts",
                repo_path / f"{imp}.py",
                repo_path / f"{imp}.js",
            ):
                if path.exists() and str(path) not in self.files:
                    try:
                        self.files[str(path)] = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        pass
                    break

    def get_worker_context(self) -> str:
        """Return the complete evidence plus complete prior-stage execution state.

        ContextManager retains full file contents so stage handoff does not
        silently discard source material. Provider/model context limits are a
        separate concern and must be handled by the provider layer rather than
        mutating the review state here.
        """
        parts: list[str] = []
        if self.git_diff:
            parts.append(f"## Changes (Git Diff)\n\n~~~diff\n{self.git_diff}\n~~~")
        if self.files:
            parts.append(f"\n## Complete Files ({len(self.files)} files)")
            for path, content in sorted(self.files.items()):
                parts.append(f"\n### {path}\n\n~~~\n{content}\n~~~")
        return "\n".join(parts) or "(No additional repository context supplied.)"

    def add_stage_result(
        self,
        phase: int,
        worker_results: list["WorkerResult"],
        arbiter_result: "ArbiterResult",
    ) -> None:
        """Persist the complete stage result for subsequent stage handoff."""
        self.stage_history.append(
            {
                "phase": phase,
                "workers": [
                    {
                        "model": result.model,
                        "succeeded": result.succeeded,
                        "analysis": result.analysis,
                        "confidence": result.confidence,
                        "key_findings": result.key_findings or [],
                        "error": result.error,
                    }
                    for result in worker_results
                ],
                "arbiter": {
                    "model": arbiter_result.model,
                    "adjudicated_result": arbiter_result.adjudicated_result,
                    "selected_worker": arbiter_result.selected_worker,
                    "rationale": arbiter_result.rationale,
                    "supporting_evidence": arbiter_result.supporting_evidence or [],
                    "rejected_alternatives": arbiter_result.rejected_alternatives or [],
                    "next_phase_questions": arbiter_result.next_phase_questions or [],
                },
                "teaching_signal": TeachingSignal.from_arbiter(arbiter_result).to_dict(),
            }
        )

    def get_stage_context(self) -> str:
        """Return base evidence plus complete prior-stage execution state."""
        parts = [self.get_worker_context()]
        for stage in self.stage_history:
            parts.append(f"\n## Prior Arbitration Stage {stage['phase']}")
            parts.append("### Worker Results")
            for worker in stage["workers"]:
                status = "succeeded" if worker["succeeded"] else "failed"
                parts.append(f"\n#### Worker {worker['model']} ({status})")
                if worker["analysis"]:
                    parts.append(worker["analysis"])
                if worker["key_findings"]:
                    parts.append("Key findings: " + "; ".join(worker["key_findings"]))
                if worker["error"]:
                    parts.append("Error: " + worker["error"])
            arbiter = stage["arbiter"]
            parts.append("\n### Arbiter Adjudication")
            parts.append(arbiter["adjudicated_result"])
            if arbiter["selected_worker"]:
                parts.append(f"Selected worker: {arbiter['selected_worker']}")
            # New stage records carry an explicit teaching signal. Legacy or
            # externally restored stage records may not, so preserve the
            # canonical arbiter fields as the compatibility fallback.
            signal = stage.get("teaching_signal") or {
                "rationale": arbiter.get("rationale", ""),
                "supporting_evidence": arbiter.get("supporting_evidence", []),
                "rejected_alternatives": arbiter.get("rejected_alternatives", []),
                "next_phase_questions": arbiter.get("next_phase_questions", []),
            }
            if any(signal.get(key) for key in ("rationale", "supporting_evidence", "rejected_alternatives", "next_phase_questions")):
                parts.append("\n### Arbiter Teaching Signal")
                if signal.get("rationale"):
                    parts.append("Rationale: " + signal["rationale"])
                if signal.get("supporting_evidence"):
                    parts.append("Evidence: " + "; ".join(signal["supporting_evidence"]))
                if signal.get("rejected_alternatives"):
                    parts.append("Rejected alternatives: " + "; ".join(signal["rejected_alternatives"]))
                if signal.get("next_phase_questions"):
                    parts.append("Questions for the next stage: " + "; ".join(signal["next_phase_questions"]))
        return "\n".join(parts)


class ArbitrationOrchestrator:
    """Execute workers and arbiters through real ModelProvider implementations."""

    def __init__(
        self,
        task_type: TaskType,
        task_description: str,
        *,
        providers: Mapping[str, ModelProvider] | None = None,
        default_provider: ModelProvider | None = None,
    ):
        self.task_type = task_type
        self.task_description = task_description
        self.context_manager = ContextManager(task_type)
        self.model_pool = ModelPool()
        self.phases: list[PhaseConfig] = []
        self.results: list[tuple[list[WorkerResult], ArbiterResult]] = []
        self.final_arbiter_output: str | None = None
        self.providers = dict(providers or {})
        # Claude Code is the explicit default for the built-in Claude model
        # aliases. Other model labels still require explicit registration.
        self.default_provider = default_provider or ClaudeCodeProvider()

    def add_phase(self, workers: list[str], arbiter: str, instructions: str) -> None:
        self.phases.append(PhaseConfig(len(self.phases) + 1, workers, arbiter, instructions))

    def auto_phases(self, num_phases: int = 1) -> None:
        """Create phases, reusing model labels across phases when requested.

        Role reservations reset between phases. A worker and arbiter can never
        share a model within the same phase, but the same labels may be reused
        by later phases. The built-in pool therefore supports arbitrary phase
        counts without pretending its three labels are distinct models forever.
        """
        if num_phases < 1:
            raise ValueError("num_phases must be greater than zero")

        for i in range(num_phases):
            # Model reuse is allowed across phases, but role collisions are
            # forbidden within each phase.
            self.model_pool.reset_phase()
            workers = self.model_pool.get_workers(2, "balanced")
            arbiter = self.model_pool.get_arbiter("expensive")
            self.add_phase(workers, arbiter, self._get_phase_instructions(i))

    def _get_phase_instructions(self, phase_idx: int) -> str:
        base = f"Analyze this {self.task_type.value} carefully.\n\n{self.task_description}"
        return (
            f"{base}\n\n"
            "Independently evaluate the current task and all supplied evidence. "
            "Assess correctness, completeness, risks, and alternatives. "
            "If prior-stage results are present, evaluate them critically rather than "
            "assuming they are correct."
        )

    def _provider_for(self, model: str) -> ModelProvider:
        provider = self.providers.get(model)
        if provider is not None:
            return provider
        if model.startswith(("claude-", "haiku", "sonnet", "opus")):
            return self.default_provider
        raise ValueError(
            f"No ModelProvider configured for model {model!r}; "
            "refusing to fabricate a model response"
        )

    def run(self) -> str:
        if not self.phases:
            raise ValueError("At least one arbitration phase is required")

        current_context = self.context_manager.get_stage_context()
        for phase_config in self.phases:
            worker_results = self._run_workers(phase_config, current_context)
            if not worker_results:
                raise RuntimeError(f"Phase {phase_config.phase} has no worker results")
            if not any(result.succeeded for result in worker_results):
                failures = "; ".join(
                    f"{result.model}: {result.error or 'no usable response'}"
                    for result in worker_results
                )
                raise RuntimeError(
                    f"Phase {phase_config.phase} has no successful workers; "
                    f"refusing to synthesize a review from failures alone ({failures})"
                )

            arbiter_result = self._run_arbiter(phase_config, worker_results, current_context)
            self.results.append((worker_results, arbiter_result))
            self.context_manager.add_stage_result(phase_config.phase, worker_results, arbiter_result)
            current_context = self.context_manager.get_stage_context()

        self.final_arbiter_output = self.results[-1][1].adjudicated_result
        return self.final_arbiter_output

    def _worker_request(self, phase_config: PhaseConfig, model: str, context: str) -> ModelRequest:
        prompt = (
            f"{phase_config.instructions}\n\n"
            f"## Original Task and Evidence\n\n{self.task_description}\n\n"
            f"## Current Phase Evidence\n\n{context}\n\n"
            "Return your actual analysis. Do not claim to have inspected evidence "
            "that is not present in the supplied context."
        )
        # Increase timeout to 300s to account for API latency and large prompts
        return ModelRequest(prompt=prompt, model=model, timeout=300.0)

    def _run_one_worker(
        self, phase_config: PhaseConfig, model: str, context: str
    ) -> WorkerResult:
        logger.info("Starting worker %s for phase %s", model, phase_config.phase)
        try:
            response = self._provider_for(model).generate(
                self._worker_request(phase_config, model, context)
            )
            logger.info("Worker %s completed phase %s", model, phase_config.phase)
            return WorkerResult(
                model=model,
                phase=phase_config.phase,
                analysis=response.text,
                key_findings=[],
            )
        except Exception as exc:
            logger.error("Worker %s failed in phase %s: %s", model, phase_config.phase, exc)
            return WorkerResult(
                model=model,
                phase=phase_config.phase,
                error=str(exc),
                key_findings=[],
            )

    def _run_workers(self, phase_config: PhaseConfig, context: str) -> list[WorkerResult]:
        """Run CLI-backed workers serially to avoid competing for one CLI runtime."""
        results: list[WorkerResult] = []
        for model in phase_config.workers:
            results.append(self._run_one_worker(phase_config, model, context))
        return results

    def _run_arbiter(
        self,
        phase_config: PhaseConfig,
        worker_results: list[WorkerResult],
        context: str,
    ) -> ArbiterResult:
        worker_sections: list[str] = []
        for result in worker_results:
            if result.succeeded:
                worker_sections.append(f"### Worker {result.model}\n\n{result.analysis}")
            else:
                worker_sections.append(f"### Worker {result.model} FAILED\n\n{result.error}")

        prompt = (
            f"{phase_config.instructions}\n\n"
            f"Task description: {self.task_description}\n\n"
            "You are the arbiter. Adjudicate all worker results below. "
            "Select the best-supported result based on the supplied evidence. "
            "Do not invent worker findings, execution, token counts, or evidence. "
            "Distinguish worker failures from successful results. "
            "Return JSON with these keys: adjudicated_result (string), selected_worker "
            "(string or null), rationale (string), supporting_evidence (array of strings), "
            "rejected_alternatives (array of strings), next_phase_questions (array of strings). "
            "The adjudicated_result is the semantic result that must be handed to the next stage.\n\n"
            f"## Original Evidence\n\n{context}\n\n"
            f"## Worker Results\n\n{chr(10).join(chr(10) + section for section in worker_sections)}"
        )

        logger.info(
            "Starting arbiter %s for phase %s",
            phase_config.arbiter,
            phase_config.phase,
        )
        try:
            # Increase timeout to 600s for arbiter (handles larger prompts with worker results)
            response = self._provider_for(phase_config.arbiter).generate(
                ModelRequest(prompt=prompt, model=phase_config.arbiter, timeout=600.0)
            )
            logger.info(
                "Arbiter %s completed phase %s",
                phase_config.arbiter,
                phase_config.phase,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Arbiter {phase_config.arbiter} failed in phase "
                f"{phase_config.phase}: {exc}"
            ) from exc

        try:
            parsed = self._parse_arbiter_response(response.text)
        except ValueError as exc:
            raise RuntimeError(
                f"Arbiter {phase_config.arbiter} returned invalid structured output "
                f"in phase {phase_config.phase}: {exc}"
            ) from exc
        return ArbiterResult(
            model=phase_config.arbiter,
            phase=phase_config.phase,
            adjudicated_result=parsed["adjudicated_result"],
            selected_worker=parsed["selected_worker"],
            rationale=parsed["rationale"],
            supporting_evidence=parsed["supporting_evidence"],
            rejected_alternatives=parsed["rejected_alternatives"],
            next_phase_questions=parsed["next_phase_questions"],
        )

    @staticmethod
    def _parse_arbiter_response(text: str) -> dict[str, Any]:
        """Parse the required structured arbiter response.

        Arbiter output is a protocol boundary, not free-form model prose.
        Malformed or incomplete JSON is rejected so a later stage cannot
        mistake an unstructured response for a valid adjudication.
        """
        import json

        if not isinstance(text, str):
            raise ValueError("Arbiter response must be text")

        # Models sometimes wrap otherwise-valid JSON in a Markdown fence or
        # add a short preamble. Extract one JSON object, then enforce the same
        # strict field/type contract below.
        candidates = [text.strip()]
        import re

        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
        if fenced:
            candidates.insert(0, fenced.group(1).strip())

        payload = None
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue

        if payload is None:
            start = text.find("{")
            if start >= 0:
                try:
                    payload, _ = json.JSONDecoder().raw_decode(text[start:])
                except json.JSONDecodeError as exc:
                    raise ValueError("Arbiter response must contain a valid JSON object") from exc
            else:
                raise ValueError("Arbiter response must contain a valid JSON object")

        if not isinstance(payload, dict):
            raise ValueError("Arbiter response must be a JSON object")
        if not isinstance(payload.get("adjudicated_result"), str):
            raise ValueError("Arbiter response requires a string adjudicated_result")

        def strings(name: str) -> list[str]:
            value = payload.get(name, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"Arbiter response field {name!r} must be an array of strings")
            return value

        if payload.get("selected_worker") is not None and not isinstance(payload.get("selected_worker"), str):
            raise ValueError("Arbiter response field 'selected_worker' must be a string or null")
        if not isinstance(payload.get("rationale", ""), str):
            raise ValueError("Arbiter response field 'rationale' must be a string")

        return {
            "adjudicated_result": payload["adjudicated_result"],
            "selected_worker": payload.get("selected_worker"),
            "rationale": payload.get("rationale", ""),
            "supporting_evidence": strings("supporting_evidence"),
            "rejected_alternatives": strings("rejected_alternatives"),
            "next_phase_questions": strings("next_phase_questions"),
        }


    def report(self) -> str:
        lines = [
            "=" * 70,
            "MULTI-PHASE ARBITRATION REPORT",
            "=" * 70,
            f"Generated: {datetime.now().isoformat()}",
            f"Task: {self.task_type.value}",
            f"Description: {self.task_description}",
            f"Total Phases: {len(self.phases)}",
        ]
        for phase_idx, (worker_results, arbiter_result) in enumerate(self.results, 1):
            lines.extend(
                [
                    f"\n--- PHASE {phase_idx} ---",
                    f"Workers: {', '.join(w.model for w in worker_results)}",
                    f"Failed workers: {sum(not w.succeeded for w in worker_results)}",
                    f"Arbiter: {arbiter_result.model}",
                    f"\nAdjudicated result:\n{arbiter_result.adjudicated_result}",
                ]
            )
        return "\n".join(lines)


if __name__ == "__main__":
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the changes in this PR for correctness, performance, and security.",
    )
    orchestrator.auto_phases()
    orchestrator.run()
    print(orchestrator.report())
