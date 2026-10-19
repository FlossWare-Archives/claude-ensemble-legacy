"""Tests for GA learning artifact construction and provenance."""
import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from ga_tuning.extract_and_apply_parameters import ParameterExtractor
from ga_tuning.learning_artifact import build_ga_learning_artifact
from learning.portable_artifacts import LearningArtifact, LearningArtifactStore


class GATuningLearningArtifactTests(unittest.TestCase):
    def test_duplicate_record_retries_require_successful_fsync(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LearningArtifactStore(Path(temp_dir) / "artifacts.jsonl")
            artifact = LearningArtifact.create(
                "ga.tuning.result",
                {"run_id": "ga-fsync-retry", "selected_parameters": {"compression": {"level": 4}}},
            )
            key = "ga.tuning.result:ga-fsync-retry"

            with patch("learning.portable_artifacts.os.fsync", side_effect=OSError("sync failed")):
                with self.assertRaisesRegex(OSError, "sync failed"):
                    store.record_once(artifact, key)

            self.assertEqual(len(store.read()), 1)

            with patch(
                "learning.portable_artifacts.os.fsync",
                side_effect=[OSError("sync still failing"), None],
            ) as fsync:
                with self.assertRaisesRegex(OSError, "sync still failing"):
                    store.record_once(artifact, key)
                self.assertEqual(store.record_once(artifact, key), "duplicate")
                self.assertEqual(fsync.call_count, 2)

            self.assertEqual(len(store.read()), 1)

    def test_nested_ga_output_preserves_candidates_and_blocks_knowledge_promotion(self):
        artifact = build_ga_learning_artifact(
            {
                "timestamp": "20261008_160000",
                "population_size": 50,
                "generations": 25,
                "total_evaluations": 1250,
            },
            {
                "compression": [
                    {"parameters": {"compression_level": 4.0, "target_reduction": 0.4}, "fitness": 0.91},
                    {"parameters": {"compression_level": 3.0, "target_reduction": 0.35}, "fitness": 0.82},
                ],
                "thompson": [],
            },
            {"compression": {"compression_level": 3.0}, "thompson": {"alpha_prior": 2.0}},
            best_parameters_source="ga_best_parameters_20261008_160000.json",
            summary_source="ga_summary_20261008_160000.json",
        )
        data = artifact.to_dict()
        self.assertEqual(data["artifact_type"], "ga.tuning.result")
        self.assertTrue(data["payload"]["run_id"].startswith("ga-20261008_160000-"))
        self.assertEqual(data["payload"]["selected_parameters"]["compression"]["compression_level"], 4.0)
        self.assertEqual(len(data["payload"]["candidate_population"]["candidates"]["compression"]), 2)
        self.assertEqual(data["payload"]["fallback_parameters"]["thompson"]["alpha_prior"], 2.0)
        self.assertFalse(data["payload"]["knowledge_promotion"]["eligible"])
        self.assertIsNone(data["payload"]["evidence"]["confidence"])

    def test_empty_and_partial_results_are_valid_and_do_not_invent_systems(self):
        artifact = build_ga_learning_artifact(
            {"timestamp": "20261008_160000"},
            {"caching": [], "matrix": [{"parameters": {"task_weight": 0.2}, "fitness": 0.5}]},
            {"compression": {"compression_level": 3.0}},
            best_parameters_source="best.json",
            summary_source="summary.json",
        )
        payload = artifact.to_dict()["payload"]
        self.assertEqual(payload["evaluation_scope"]["systems_evaluated"], ["matrix"])
        self.assertEqual(payload["selected_parameters"], {"matrix": {"task_weight": 0.2}})
        self.assertIn("compression", payload["fallback_parameters"])


    def test_parameter_extractor_reads_current_nested_ga_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            best = root / "ga_best_parameters_20261008_160000.json"
            best.write_text(json.dumps({
                "compression": [{"parameters": {"compression_level": 4.5, "target_reduction": 0.42}, "fitness": 0.9}],
                "thompson": [],
            }), encoding="utf-8")
            extractor = ParameterExtractor(root, root / "settings.json", root / "evolution.md")
            params = extractor.extract_parameters(best)
            self.assertEqual(params["compression"]["compression_level"], 4.5)
            self.assertEqual(params["compression"]["target_reduction"], 0.42)
            self.assertNotIn("thompson", params)


    def test_partial_candidate_is_extracted_without_runtime_settings_writer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            best = root / "ga_best_parameters_20261008_160000.json"
            best.write_text(json.dumps({
                "compression": [{"parameters": {"compression_level": 4.5}, "fitness": 0.9}],
            }), encoding="utf-8")
            settings = root / "settings.json"
            original = {
                "env": {
                    "GA_COMPRESSION_LEVEL": "2.0",
                    "GA_COMPRESSION_TARGET": "0.61",
                }
            }
            settings.write_text(json.dumps(original), encoding="utf-8")
            extractor = ParameterExtractor(root, settings, root / "evolution.md")
            params = extractor.extract_parameters(best)
            self.assertEqual(params, {"compression": {"compression_level": 4.5}})
            self.assertFalse(hasattr(extractor, "update_settings_json"))
            self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), original)

    def test_memory_ack_failure_leaves_settings_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timestamp = "20261008_160000"
            best = root / f"ga_best_parameters_{timestamp}.json"
            best.write_text(json.dumps({
                "compression": [{"parameters": {"compression_level": 4.5}, "fitness": 0.9}],
            }), encoding="utf-8")
            (root / f"ga_fitness_history_{timestamp}.json").write_text("[]", encoding="utf-8")
            (root / f"ga_summary_{timestamp}.json").write_text(json.dumps({
                "timestamp": timestamp,
                "population_size": 10,
                "generations": 2,
                "total_evaluations": 20,
            }), encoding="utf-8")
            settings = root / "settings.json"
            original = {"env": {"GA_COMPRESSION_LEVEL": "2.0", "GA_COMPRESSION_TARGET": "0.61"}}
            settings.write_text(json.dumps(original), encoding="utf-8")
            tracking = root / "evolution.md"
            extractor = ParameterExtractor(root, settings, tracking)

            with patch(
                "ga_tuning.extract_and_apply_parameters.LearningClient"
            ) as client_class:
                client_class.return_value.record_artifact.return_value = {
                    "ok": False, "memory": False, "error": "Memory unavailable"
                }
                with self.assertRaisesRegex(RuntimeError, "candidate was not marked ingested"):
                    extractor.run()

            self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), original)
            self.assertFalse(tracking.exists())

    def test_repeated_successful_run_is_idempotent_without_applying_unverified_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timestamp = "20261008_160000"
            best = root / f"ga_best_parameters_{timestamp}.json"
            best.write_text(json.dumps({
                "compression": [{"parameters": {"compression_level": 4.5}, "fitness": 0.9}],
            }), encoding="utf-8")
            (root / f"ga_fitness_history_{timestamp}.json").write_text("[]", encoding="utf-8")
            (root / f"ga_summary_{timestamp}.json").write_text(json.dumps({
                "timestamp": timestamp,
                "population_size": 10,
                "generations": 2,
                "total_evaluations": 20,
            }), encoding="utf-8")
            settings = root / "settings.json"
            settings.write_text(json.dumps({
                "env": {"GA_COMPRESSION_LEVEL": "2.0", "GA_COMPRESSION_TARGET": "0.61"}
            }), encoding="utf-8")
            tracking = root / "evolution.md"
            extractor = ParameterExtractor(root, settings, tracking)

            with patch(
                "ga_tuning.extract_and_apply_parameters.LearningClient"
            ) as client_class:
                client_class.return_value.record_artifact.return_value = {
                    "ok": True, "memory": True, "artifact_status": "stored"
                }
                extractor.run()
                first_settings = json.loads(settings.read_text(encoding="utf-8"))
                extractor.run()
                second_settings = json.loads(settings.read_text(encoding="utf-8"))

            self.assertEqual(first_settings, second_settings)
            self.assertEqual(client_class.return_value.record_artifact.call_count, 2)
            first_artifact = client_class.return_value.record_artifact.call_args_list[0].args[0]
            second_artifact = client_class.return_value.record_artifact.call_args_list[1].args[0]
            self.assertEqual(first_artifact, second_artifact)
            log = tracking.read_text(encoding="utf-8")
            self.assertNotIn("GA_TUNING_RUN_ID", first_settings["env"])
            run_id = client_class.return_value.record_artifact.call_args.args[0]["payload"]["run_id"]
            self.assertEqual(log.count(json.dumps(run_id)), 1)
            self.assertEqual(len(list(root.glob(".*.ingested"))), 1)

    def test_unscored_candidate_is_not_applied_or_recorded_as_selected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            best = root / "ga_best_parameters_20261008_160000.json"
            best.write_text(json.dumps({
                "compression": [
                    {"parameters": {"compression_level": 1.0}, "fitness": None},
                    {"parameters": {"compression_level": 4.5}, "fitness": 0.9},
                ],
            }), encoding="utf-8")
            extractor = ParameterExtractor(root, root / "settings.json", root / "evolution.md")
            params = extractor.extract_parameters(best)
            self.assertEqual(params, {"compression": {"compression_level": 4.5}})

            artifact = build_ga_learning_artifact(
                {"timestamp": "20261008_160000"},
                json.loads(best.read_text(encoding="utf-8")),
                {},
                best_parameters_source=best.name,
                summary_source="ga_summary_20261008_160000.json",
            )
            payload = artifact.to_dict()["payload"]
            self.assertEqual(payload["selected_parameters"]["compression"]["compression_level"], 4.5)
            self.assertEqual(payload["objectives"]["compression"], 0.9)


    def test_non_finite_candidate_parameters_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            best = root / "ga_best_parameters_20261008_160000.json"
            best.write_text(json.dumps({
                "compression": [{
                    "parameters": {"compression_level": float("nan")},
                    "fitness": 0.9,
                }],
            }), encoding="utf-8")
            extractor = ParameterExtractor(root, root / "settings.json", root / "evolution.md")
            with self.assertRaisesRegex(ValueError, "finite"):
                extractor.extract_parameters(best)

            with self.assertRaisesRegex(ValueError, "finite"):
                build_ga_learning_artifact(
                    {"timestamp": "20261008_160000"},
                    json.loads(best.read_text(encoding="utf-8")),
                    {},
                    best_parameters_source=best.name,
                    summary_source="ga_summary_20261008_160000.json",
                )

    def test_missing_or_malformed_summary_timestamp_is_rejected(self):
        for summary in ({}, {"timestamp": "not-a-ga-timestamp"}):
            with self.subTest(summary=summary):
                with self.assertRaisesRegex(ValueError, "timestamp"):
                    build_ga_learning_artifact(
                        summary,
                        {"compression": [{"parameters": {"compression_level": 4.0}, "fitness": 0.9}]},
                        {},
                        best_parameters_source="best.json",
                        summary_source="summary.json",
                    )

    def test_run_identity_is_stable_and_distinguishes_same_second_results(self):
        summary = {"timestamp": "20261008_160000", "generations": 2}
        first = {"compression": [
            {"parameters": {"compression_level": 4.0}, "fitness": 0.9}
        ]}
        same = build_ga_learning_artifact(
            summary, first, {}, best_parameters_source="best.json",
            summary_source="summary.json",
        )
        retry = build_ga_learning_artifact(
            summary, first, {"GA_COMPRESSION_LEVEL": "4.0"},
            best_parameters_source="best.json", summary_source="summary.json",
        )
        changed = build_ga_learning_artifact(
            summary,
            {"compression": [
                {"parameters": {"compression_level": 5.0}, "fitness": 0.95}
            ]},
            {}, best_parameters_source="best.json", summary_source="summary.json",
        )
        self.assertEqual(same.payload["run_id"], retry.payload["run_id"])
        self.assertNotEqual(same.payload["run_id"], changed.payload["run_id"])

    def test_candidate_ranks_are_contiguous_after_unscored_candidates_are_removed(self):
        artifact = build_ga_learning_artifact(
            {"timestamp": "20261008_160000"},
            {"compression": [
                {"parameters": {"compression_level": 1.0}, "fitness": None},
                {"parameters": {"compression_level": 4.5}, "fitness": 0.9},
                {"parameters": {"compression_level": 3.5}, "fitness": 0.8},
            ]},
            {}, best_parameters_source="best.json", summary_source="summary.json",
        )
        candidates = artifact.payload["candidate_population"]["candidates"]["compression"]
        self.assertEqual([candidate["rank"] for candidate in candidates], [1, 2])


    def test_retry_reuses_original_artifact_when_fallback_settings_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timestamp = "20261008_160000"
            best = root / f"ga_best_parameters_{timestamp}.json"
            best.write_text(json.dumps({
                "compression": [{"parameters": {"compression_level": 4.5}, "fitness": 0.9}],
            }), encoding="utf-8")
            (root / f"ga_fitness_history_{timestamp}.json").write_text("[]", encoding="utf-8")
            (root / f"ga_summary_{timestamp}.json").write_text(json.dumps({
                "timestamp": timestamp, "population_size": 10,
                "generations": 2, "total_evaluations": 20,
            }), encoding="utf-8")
            settings = root / "settings.json"
            settings.write_text(json.dumps({
                "env": {"GA_COMPRESSION_LEVEL": "2.0", "GA_COMPRESSION_TARGET": "0.61"}
            }), encoding="utf-8")
            store = LearningArtifactStore(root / "learning" / "portable_artifacts.jsonl")
            submitted = []
            memory_results = iter([False, True])

            def record_artifact(payload):
                submitted.append(payload)
                run_id = payload["payload"]["run_id"]
                store.record_once(LearningArtifact.from_dict(payload),
                                  f'{payload["artifact_type"]}:{run_id}')
                memory_ok = next(memory_results)
                return {"ok": memory_ok, "memory": memory_ok}

            extractor = ParameterExtractor(root, settings, root / "evolution.md")
            with patch("ga_tuning.extract_and_apply_parameters.LearningClient") as client_class:
                client_class.return_value.record_artifact.side_effect = record_artifact
                with self.assertRaisesRegex(RuntimeError, "candidate was not marked ingested"):
                    extractor.run()
                changed_settings = json.loads(settings.read_text(encoding="utf-8"))
                changed_settings["env"]["GA_COMPRESSION_TARGET"] = "0.99"
                settings.write_text(json.dumps(changed_settings), encoding="utf-8")
                extractor.run()

            self.assertEqual(submitted[0], submitted[1])
            self.assertEqual(submitted[1]["payload"]["fallback_parameters"]["GA_COMPRESSION_TARGET"], "0.61")
            self.assertEqual(len(store.read()), 1)
            final_settings = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(final_settings["env"]["GA_COMPRESSION_LEVEL"], "2.0")
            self.assertEqual(final_settings["env"]["GA_COMPRESSION_TARGET"], "0.99")
            self.assertNotIn("GA_TUNING_RUN_ID", final_settings["env"])
            self.assertEqual(list(root.glob("*.learning-artifact.json")), [])

    def test_run_uses_one_best_parameters_snapshot_without_applying_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            timestamp = "20261008_160000"
            best = root / f"ga_best_parameters_{timestamp}.json"
            best.write_text(json.dumps({
                "compression": [{"parameters": {"compression_level": 4.5}, "fitness": 0.9}],
            }), encoding="utf-8")
            (root / f"ga_fitness_history_{timestamp}.json").write_text("[]", encoding="utf-8")
            (root / f"ga_summary_{timestamp}.json").write_text(json.dumps({
                "timestamp": timestamp, "population_size": 10,
                "generations": 2, "total_evaluations": 20,
            }), encoding="utf-8")
            settings = root / "settings.json"
            settings.write_text(json.dumps({"env": {"GA_COMPRESSION_LEVEL": "2.0"}}), encoding="utf-8")
            extractor = ParameterExtractor(root, settings, root / "evolution.md")
            original_extract = extractor.extract_parameters
            submitted = []

            def replace_result_after_extraction(path, data=None):
                params = original_extract(path, data=data)
                best.write_text(json.dumps({
                    "compression": [{"parameters": {"compression_level": 1.5}, "fitness": 0.1}],
                }), encoding="utf-8")
                return params

            with patch.object(extractor, "extract_parameters", side_effect=replace_result_after_extraction):
                with patch("ga_tuning.extract_and_apply_parameters.LearningClient") as client_class:
                    client_class.return_value.record_artifact.side_effect = lambda payload: (
                        submitted.append(payload) or {"ok": True, "memory": True}
                    )
                    extractor.run()

            unchanged = json.loads(settings.read_text(encoding="utf-8"))["env"]["GA_COMPRESSION_LEVEL"]
            self.assertEqual(unchanged, "2.0")
            self.assertEqual(submitted[0]["payload"]["selected_parameters"]["compression"]["compression_level"], 4.5)
            self.assertEqual(submitted[0]["payload"]["objectives"]["compression"], 0.9)


    def _prepare_extractor_run(self, root):
        timestamp = "20261008_160000"
        best = root / f"ga_best_parameters_{timestamp}.json"
        best.write_text(json.dumps({
            "compression": [
                {"parameters": {"compression_level": 4.5, "target_reduction": 0.42}, "fitness": 0.9},
                {"parameters": {"compression_level": 3.5, "target_reduction": 0.35}, "fitness": 0.8},
            ],
        }), encoding="utf-8")
        (root / f"ga_fitness_history_{timestamp}.json").write_text("[]", encoding="utf-8")
        (root / f"ga_summary_{timestamp}.json").write_text(json.dumps({
            "timestamp": timestamp, "population_size": 10,
            "generations": 2, "total_evaluations": 20,
        }), encoding="utf-8")
        settings = root / "settings.json"
        settings.write_text(json.dumps({"env": {"GA_COMPRESSION_LEVEL": "2.0"}}), encoding="utf-8")
        return ParameterExtractor(root, settings, root / "evolution.md")

    def test_artifact_rejects_unknown_parameter_in_selected_candidate(self):
        with self.assertRaisesRegex(ValueError, "Unsupported GA parameter"):
            build_ga_learning_artifact(
                {"timestamp": "20261008_160000"},
                {"compression": [
                    {"parameters": {"compression_level": 4.0, "surprise_knob": 0.5}, "fitness": 0.9}
                ]},
                {}, best_parameters_source="best.json", summary_source="summary.json",
            )

    def test_artifact_rejects_out_of_range_parameter_in_second_ranked_candidate(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            build_ga_learning_artifact(
                {"timestamp": "20261008_160000"},
                {"compression": [
                    {"parameters": {"compression_level": 4.0}, "fitness": 0.9},
                    {"parameters": {"compression_level": 99.0}, "fitness": 0.8},
                ]},
                {}, best_parameters_source="best.json", summary_source="summary.json",
            )

    def test_empty_or_mismatched_ingestion_receipt_never_skips_ingestion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extractor = self._prepare_extractor_run(root)
            with patch("ga_tuning.extract_and_apply_parameters.LearningClient") as client_class:
                client_class.return_value.record_artifact.return_value = {
                    "ok": True, "memory": True, "artifact_status": "stored"
                }
                extractor.run()
                marker = next(root.glob(".*.ingested"))
                marker.write_text("", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "Invalid GA ingestion receipt"):
                    extractor.run()
                marker.write_text(json.dumps({
                    "receipt_version": 1,
                    "run_id": "ga-wrong-run",
                    "candidate_digest": "not-the-candidate-digest",
                    "learning_ack": {"ok": True, "memory": True},
                    "acknowledged_at": "2026-10-08T16:00:00Z",
                }), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "does not verify"):
                    extractor.run()
                client_class.return_value.record_artifact.assert_called_once()

    def test_interrupted_receipt_rename_recovers_by_idempotent_learning_submission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extractor = self._prepare_extractor_run(root)
            store = LearningArtifactStore(root / "learning" / "portable_artifacts.jsonl")
            original_replace = os.replace
            replace_calls = {"count": 0}

            def fail_receipt_replace_once(source, destination):
                replace_calls["count"] += 1
                if replace_calls["count"] == 2:
                    raise OSError("simulated receipt rename interruption")
                return original_replace(source, destination)

            def record_artifact(payload):
                artifact = LearningArtifact.from_dict(payload)
                run_id = artifact.payload["run_id"]
                store.record_once(artifact, f'{payload["artifact_type"]}:{run_id}')
                return {"ok": True, "memory": True, "artifact_status": "stored"}

            with patch("ga_tuning.extract_and_apply_parameters.LearningClient") as client_class:
                client_class.return_value.record_artifact.side_effect = record_artifact
                with patch("ga_tuning.extract_and_apply_parameters.os.replace", side_effect=fail_receipt_replace_once):
                    with self.assertRaisesRegex(OSError, "receipt rename interruption"):
                        extractor.run()
                self.assertEqual(len(list(root.glob(".*.learning-artifact.json"))), 1)
                self.assertFalse(list(root.glob(".*.ingested")))
                extractor.run()

            self.assertEqual(len(store.read()), 1)
            self.assertEqual(client_class.return_value.record_artifact.call_count, 2)
            self.assertEqual(len(list(root.glob(".*.ingested"))), 1)
            self.assertFalse(list(root.glob(".*.learning-artifact.json")))


if __name__ == "__main__":
    unittest.main()
