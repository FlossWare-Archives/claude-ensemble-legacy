#!/usr/bin/env python3
"""
Extract GA-optimized candidate parameters and record them through Learning/Memory.
Runtime settings are read-only for fallback/provenance; candidates are not applied.
Tracks parameter evolution over time for analysis.
"""

import argparse
import fcntl
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ga_tuning.learning_artifact import build_ga_learning_artifact, candidate_digest, scored_candidates
from ga_tuning.parameter_schema import validate_parameter
from learning.learning_client import LearningClient
from learning.portable_artifacts import LearningArtifact

class ParameterExtractor:
    def __init__(self, results_dir: Path, settings_json: Path, tracking_log: Path):
        self.results_dir = Path(results_dir)
        self.settings_json = Path(settings_json)
        self.tracking_log = Path(tracking_log)
        self.tracking_log.parent.mkdir(parents=True, exist_ok=True)

    def get_latest_results(self) -> Tuple[Path, Path]:
        """Get latest GA results files"""
        best_params_files = sorted(
            self.results_dir.glob("ga_best_parameters_*.json"),
            reverse=True
        )
        fitness_files = sorted(
            self.results_dir.glob("ga_fitness_history_*.json"),
            reverse=True
        )

        if not best_params_files or not fitness_files:
            raise FileNotFoundError("No GA results found")

        return best_params_files[0], fitness_files[0]

    def extract_parameters(self, best_params_file: Path, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Extract parameters from a parsed GA result snapshot (or load one for standalone use)."""
        if data is None:
            with open(best_params_file, encoding="utf-8") as f:
                data = json.load(f)

        params = {}

        def best_candidate(system):
            # Share the exact scored-candidate selection rule with artifact creation.
            candidates = scored_candidates(data.get(system))
            if not candidates:
                return None
            candidate, _ = candidates[0]
            nested = candidate.get('parameters')
            return nested if isinstance(nested, dict) else candidate

        def available_values(system, names):
            candidate = best_candidate(system)
            if candidate is None:
                return None
            # Do not substitute hard-coded defaults for missing GA values. The
            # existing settings remain the fallback and are captured in the artifact.
            values = {}
            for name in names:
                if name not in candidate:
                    continue
                raw_value = candidate[name]
                values[name] = validate_parameter(system, name, raw_value)
            return values or None

        # 1. Compression parameters
        comp = available_values('compression', ('compression_level', 'target_reduction'))
        if comp is not None:
            params['compression'] = comp

        # 2. Thompson Router parameters
        thompson = available_values('thompson', ('alpha_prior', 'beta_prior', 'cost_weight'))
        if thompson is not None:
            params['thompson'] = thompson

        # 3. Caching parameters
        caching = available_values('caching', ('ttl_seconds', 'cache_threshold'))
        if caching is not None:
            params['caching'] = caching

        # 4. Capability Matrix parameters
        matrix = available_values('matrix', ('domain_weight', 'complexity_weight', 'task_weight'))
        if matrix is not None:
            params['matrix'] = matrix

        # 5. Dashboard/Learning parameters
        dashboard = available_values('dashboard', ('learning_rate', 'exploration_decay', 'alert_threshold'))
        if dashboard is not None:
            params['dashboard'] = dashboard

        return params

    def log_parameter_evolution(self, params: Dict[str, Any], timestamp: str, run_id: str = None) -> None:
        """Durably log parameter changes once per GA run, including after a partial retry."""
        already_logged = False
        if run_id and self.tracking_log.exists():
            marker = json.dumps(run_id)
            already_logged = f'"run_id": {marker}' in self.tracking_log.read_text(encoding="utf-8")

        if not already_logged:
            entry = {
                'timestamp': timestamp,
                'run_id': run_id,
                'parameters': params
            }
            with self.tracking_log.open('a', encoding="utf-8") as handle:
                if self.tracking_log.stat().st_size == 0:
                    handle.write("# GA Parameter Evolution Log\n\n")
                handle.write(f"## {timestamp}\n\n")
                handle.write("```json\n")
                handle.write(json.dumps(entry, indent=2))
                handle.write("\n```\n\n")
                handle.flush()
                os.fsync(handle.fileno())

        # Even the idempotent path syncs the existing audit entry before callers
        # are allowed to discard the pending snapshot.
        if already_logged and self.tracking_log.exists():
            with self.tracking_log.open("rb") as handle:
                os.fsync(handle.fileno())
        self._fsync_directory(self.tracking_log.parent)

    def _remove_pending_durably(self, pending_path: Path, pending_bytes: bytes) -> None:
        """Remove a pending snapshot, restoring it if the unlink sync fails."""
        pending_path.unlink()
        try:
            self._fsync_directory(pending_path.parent)
        except OSError:
            # The receipt is already durable, but the unlink may not be. Restore
            # the immutable snapshot so a subsequent recovery has a safe retry path.
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=pending_path.parent,
                    prefix=f".{pending_path.name}.", suffix=".restore",
                    delete=False,
                ) as handle:
                    temp_path = Path(handle.name)
                    handle.write(pending_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, pending_path)
                temp_path = None
                try:
                    self._fsync_directory(pending_path.parent)
                except OSError:
                    # Preserve the live retry file even if the filesystem remains
                    # unable to confirm directory durability; propagate original error.
                    pass
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
            raise


    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Persist directory-entry changes before discarding a recovery snapshot."""
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _recover_pending_locked(self) -> int:
        """Retry immutable pending artifacts without running the optimizer."""
        pending_files = sorted(self.results_dir.glob(".*.learning-artifact.json"))
        if not pending_files:
            print("No pending GA learning artifacts.")
            return 0

        recovered = 0
        failures = []
        for pending_path in pending_files:
            try:
                if pending_path.is_symlink():
                    raise ValueError(f"Refusing symlinked pending artifact: {pending_path.name}")
                pending_bytes = pending_path.read_bytes()
                artifact = LearningArtifact.from_json(
                    pending_bytes.decode("utf-8")
                )
                run_id = artifact.payload.get("run_id")
                expected_name = f".{run_id}.learning-artifact.json"
                if not isinstance(run_id, str) or pending_path.name != expected_name:
                    raise ValueError(
                        f"Pending artifact filename/run ID mismatch: {pending_path.name}"
                    )
                if artifact.artifact_type != "ga.tuning.result":
                    raise ValueError(f"Unexpected artifact type: {artifact.artifact_type}")

                receipt_path = self.results_dir / f".{run_id}.ingested"
                digest = candidate_digest(artifact)
                if receipt_path.exists():
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    if (
                        not isinstance(receipt, dict)
                        or receipt.get("receipt_version") != 1
                        or receipt.get("run_id") != run_id
                        or receipt.get("candidate_digest") != digest
                        or receipt.get("learning_ack") != {"ok": True, "memory": True}
                        or not isinstance(receipt.get("acknowledged_at"), str)
                        or not receipt["acknowledged_at"].strip()
                    ):
                        raise RuntimeError(f"Existing receipt does not verify run {run_id}")
                    # The local receipt can be forged. Verify durable service-side
                    # persistence again using the same idempotent run identity before
                    # deleting the only recovery snapshot.
                    response = LearningClient().record_artifact(artifact.to_dict())
                    if response.get("ok") is not True or response.get("memory") is not True:
                        raise RuntimeError(
                            f"Learning/Memory could not verify receipt for run {run_id}: {response}"
                        )
                    # Re-establish receipt directory-entry durability before
                    # deleting the only recovery snapshot.
                    try:
                        self._fsync_directory(self.results_dir)
                    except OSError as exc:
                        raise RuntimeError(
                            f"existing receipt durability verification failed for run {run_id}: {exc}"
                        ) from exc
                    artifact_data = artifact.to_dict()
                    params = artifact_data["payload"].get("selected_parameters", {})
                    timestamp = artifact_data["payload"].get("run_timestamp", run_id)
                    self.log_parameter_evolution(params, timestamp, run_id=run_id)
                    try:
                        self._remove_pending_durably(pending_path, pending_bytes)
                    except OSError as exc:
                        state = (
                            "pending snapshot path is present after restoration; directory-entry durability may be uncertain"
                            if pending_path.exists()
                            else "pending snapshot path may be absent"
                        )
                        raise RuntimeError(
                            f"post-ingestion cleanup durability failure for run {run_id}; "
                            f"{state}; receipt remains recorded: {exc}"
                        ) from exc
                    print(f"{run_id}: valid receipt already exists; removed stale pending file.")
                    recovered += 1
                    continue

                response = LearningClient().record_artifact(artifact.to_dict())
                if response.get("ok") is not True or response.get("memory") is not True:
                    raise RuntimeError(
                        f"Learning/Memory did not acknowledge durable storage: {response}"
                    )

                artifact_data = artifact.to_dict()
                params = artifact_data["payload"].get("selected_parameters", {})
                timestamp = artifact_data["payload"].get("run_timestamp", run_id)
                self.log_parameter_evolution(params, timestamp, run_id=run_id)

                receipt = {
                    "receipt_version": 1,
                    "run_id": run_id,
                    "candidate_digest": digest,
                    "learning_ack": {"ok": True, "memory": True},
                    "acknowledged_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
                temp_path = receipt_path.with_suffix(".tmp")
                try:
                    try:
                        with temp_path.open("w", encoding="utf-8") as handle:
                            json.dump(receipt, handle, sort_keys=True)
                            handle.write("\n")
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temp_path, receipt_path)
                        self._fsync_directory(self.results_dir)
                    except OSError as exc:
                        raise RuntimeError(
                            f"receipt persistence failed for run {run_id}: {exc}"
                        ) from exc
                finally:
                    temp_path.unlink(missing_ok=True)

                try:
                    self._remove_pending_durably(pending_path, pending_bytes)
                except OSError as exc:
                    state = (
                        "pending snapshot path is present after restoration; directory-entry durability may be uncertain"
                        if pending_path.exists()
                        else "pending snapshot path may be absent"
                    )
                    raise RuntimeError(
                        f"post-ingestion cleanup durability failure for run {run_id}; "
                        f"{state}; receipt remains recorded: {exc}"
                    ) from exc
                print(f"{run_id}: Learning and Memory acknowledged; receipt written.")
                recovered += 1
            except Exception as exc:
                failures.append((pending_path.name, str(exc)))
                print(f"{pending_path.name}: recovery failed: {exc}")

        print(f"Pending artifact recovery: {recovered}/{len(pending_files)} completed.")
        if failures:
            raise RuntimeError(
                "Some pending GA artifacts remain unresolved: "
                + "; ".join(f"{name}: {error}" for name, error in failures)
            )
        return recovered

    def _with_ingestion_lock(self, operation):
        """Serialize every artifact-ingestion path across scheduler processes."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.results_dir / ".ga-ingestion.lock"
        with lock_path.open("a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                return operation()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def recover_pending(self) -> int:
        """Recover pending artifacts under the same inter-process lock as run()."""
        return self._with_ingestion_lock(self._recover_pending_locked)

    def run(self) -> None:
        """Run GA ingestion under the results-directory inter-process lock."""
        self._with_ingestion_lock(self._run_locked)

    def _run_locked(self) -> None:
        """Extract, acknowledge, and log one immutable GA candidate snapshot."""
        try:
            best_params_file, _ = self.get_latest_results()
            timestamp = datetime.now(timezone.utc).isoformat()

            # Read the optimizer result exactly once. Parameter application and
            # artifact provenance must describe this same immutable in-memory snapshot.
            best_by_system = json.loads(best_params_file.read_text(encoding="utf-8"))
            params = self.extract_parameters(best_params_file, data=best_by_system)
            with self.settings_json.open(encoding="utf-8") as handle:
                current_settings = json.load(handle)
            fallback_parameters = {
                key: value for key, value in current_settings.get("env", {}).items()
                if key.startswith("GA_")
            }
            summary_file = best_params_file.with_name(
                best_params_file.name.replace("ga_best_parameters_", "ga_summary_", 1)
            )
            if not summary_file.is_file():
                raise FileNotFoundError(f"GA summary not found for run: {best_params_file.name}")
            with summary_file.open(encoding="utf-8") as handle:
                summary = json.load(handle)
            artifact = build_ga_learning_artifact(
                summary,
                best_by_system,
                fallback_parameters,
                best_parameters_source=best_params_file.name,
                summary_source=summary_file.name,
            )

            run_id = artifact.payload["run_id"]
            pending_artifact = self.results_dir / f".{run_id}.learning-artifact.json"
            # The artifact's run identity intentionally excludes mutable fallbacks.
            # Persist the first submitted payload and reuse it until application and
            # logging complete, so a retry cannot reconstruct different content.
            if pending_artifact.exists():
                artifact = LearningArtifact.from_json(
                    pending_artifact.read_text(encoding="utf-8")
                )
                if artifact.payload.get("run_id") != run_id:
                    raise RuntimeError(f"Pending GA artifact does not match run ID {run_id}")
            else:
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        dir=self.results_dir,
                        prefix=f".{run_id}.",
                        suffix=".tmp",
                        delete=False,
                    ) as handle:
                        temp_path = Path(handle.name)
                        handle.write(artifact.to_json() + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, pending_artifact)
                    temp_path = None
                finally:
                    if temp_path is not None:
                        try:
                            temp_path.unlink()
                        except FileNotFoundError:
                            pass

            ingested_marker = self.results_dir / f".{run_id}.ingested"
            if ingested_marker.exists():
                # Marker existence alone is not proof of ingestion. Require a
                # complete receipt matching this immutable candidate snapshot.
                try:
                    receipt = json.loads(ingested_marker.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"Invalid GA ingestion receipt for run {run_id}; refusing to skip ingestion"
                    ) from exc
                expected_digest = candidate_digest(artifact)
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("receipt_version") != 1
                    or receipt.get("run_id") != run_id
                    or receipt.get("candidate_digest") != expected_digest
                    or receipt.get("learning_ack") != {"ok": True, "memory": True}
                    or not isinstance(receipt.get("acknowledged_at"), str)
                    or not receipt["acknowledged_at"].strip()
                ):
                    raise RuntimeError(
                        f"GA ingestion receipt does not verify run {run_id}; refusing to skip ingestion"
                    )
                # A local receipt is not authoritative evidence: anyone who can
                # write the results directory can create one. Re-submit the exact
                # immutable artifact and require a fresh Learning + Memory ack;
                # the service deduplicates by the stable artifact run identity.
                response = LearningClient().record_artifact(artifact.to_dict())
                if response.get("ok") is not True or response.get("memory") is not True:
                    raise RuntimeError(
                        f"Learning/Memory could not verify receipt for run {run_id}: {response}"
                    )
                self.log_parameter_evolution(params, timestamp, run_id=run_id)
                pending_artifact.unlink(missing_ok=True)
                print(f"GA run {run_id} has a verified Learning/Memory receipt; skipping duplicate ingestion.")
                return

            print("Created GA learning artifact:")
            print(artifact.to_json())

            # Canonical Learning service must acknowledge Memory persistence before
            # the candidate can be marked as durably recorded. GA parameters are
            # not applied to runtime settings until production consumers are verified.
            learning = LearningClient()
            response = learning.record_artifact(artifact.to_dict())
            if not response.get("ok") or not response.get("memory"):
                raise RuntimeError(
                    "Learning service did not durably acknowledge the GA artifact; "
                    "candidate was not marked ingested: " + str(response.get("error", response))
                )

            print("Recorded GA candidate parameters (not applied to runtime settings):")
            print(json.dumps(params, indent=2))

            self.log_parameter_evolution(params, timestamp, run_id=run_id)
            marker_temp = ingested_marker.with_suffix(".tmp")
            receipt = {
                "receipt_version": 1,
                "run_id": run_id,
                "candidate_digest": candidate_digest(artifact),
                "learning_ack": {"ok": True, "memory": True},
                "acknowledged_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            try:
                with marker_temp.open("w", encoding="utf-8") as handle:
                    json.dump(receipt, handle, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(marker_temp, ingested_marker)
                # Persist the directory entry so a successful receipt rename
                # survives a crash before the pending artifact is removed.
                directory_fd = os.open(self.results_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                marker_temp.unlink(missing_ok=True)
            pending_artifact.unlink(missing_ok=True)
            print(f"✓ Logged candidate evolution to {self.tracking_log}")
            print("Runtime settings were not changed: no GA parameter has a verified production consumer yet.")

        except Exception as e:
            print(f"✗ Error: {e}")
            raise


def parse_args(argv=None):
    """Parse explicit runtime paths; never default to a repository-local settings file."""
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Record GA candidate results; do not apply runtime parameters.")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=repo_root / "ga_tuning" / "results",
        help="Directory containing GA result JSON files (default: this checkout's ga_tuning/results).",
    )
    parser.add_argument(
        "--settings-path",
        type=Path,
        default=Path(os.environ.get("CLAUDE_SETTINGS_PATH", Path.home() / ".claude" / "settings.json")),
        help="Runtime settings JSON used for fallback/provenance only (default: CLAUDE_SETTINGS_PATH or ~/.claude/settings.json).",
    )
    parser.add_argument(
        "--recover-pending",
        action="store_true",
        help="Retry existing pending GA artifacts without running the optimizer.",
    )
    parser.add_argument(
        "--evolution-log",
        type=Path,
        default=repo_root / "ga_tuning" / "parameter_evolution.md",
        help="Parameter evolution audit log.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    results_dir = args.results_dir.expanduser().resolve()
    settings_json = args.settings_path.expanduser().resolve()
    tracking_log = args.evolution_log.expanduser().resolve()

    if not results_dir.is_dir():
        raise FileNotFoundError(f"GA results directory does not exist: {results_dir}")
    print(f"GA results: {results_dir}")
    print(f"Evolution log: {tracking_log}")
    extractor = ParameterExtractor(results_dir, settings_json, tracking_log)
    if args.recover_pending:
        # Recovery uses only immutable pending snapshots and durable acknowledgements.
        # It must not depend on mutable runtime settings.
        extractor.recover_pending()
        return

    if not settings_json.is_file():
        raise FileNotFoundError(
            f"Runtime settings file does not exist: {settings_json}. "
            "Set CLAUDE_SETTINGS_PATH or pass --settings-path explicitly."
        )
    try:
        settings = json.loads(settings_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON settings from {settings_json}: {exc}") from exc
    if not isinstance(settings, dict) or not isinstance(settings.get("env", {}), dict):
        raise ValueError(f"Settings must be a JSON object with an optional object-valued 'env': {settings_json}")

    print(f"Runtime settings: {settings_json}")
    extractor.run()


if __name__ == "__main__":
    main()
