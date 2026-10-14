"""Regression coverage for recovery of immutable GA learning snapshots."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ga_tuning import extract_and_apply_parameters as module
from ga_tuning.learning_artifact import candidate_digest
from learning.portable_artifacts import LearningArtifact


class PendingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.results = self.root / "results"
        self.results.mkdir()
        self.settings = self.root / "missing-settings.json"
        self.audit = self.root / "evolution.md"
        self.extractor = module.ParameterExtractor(self.results, self.settings, self.audit)

    def tearDown(self):
        self.temp.cleanup()

    def make_pending(self, run_id="ga-test-001", artifact_type="ga.tuning.result", filename_run_id=None):
        artifact = LearningArtifact.create(
            artifact_type,
            {
                "run_id": run_id,
                "run_timestamp": "2026-10-09T12:00:00",
                "selected_parameters": {"caching": {"ttl_seconds": 60}},
                "candidate_population": {"candidates": {"caching": []}},
                "objectives": {},
            },
        )
        file_run_id = filename_run_id or run_id
        path = self.results / f".{file_run_id}.learning-artifact.json"
        path.write_text(artifact.to_json(), encoding="utf-8")
        return artifact, path

    def write_receipt(self, artifact):
        run_id = artifact.payload["run_id"]
        receipt = {
            "receipt_version": 1,
            "run_id": run_id,
            "candidate_digest": candidate_digest(artifact),
            "learning_ack": {"ok": True, "memory": True},
            "acknowledged_at": "2026-10-09T16:00:00Z",
        }
        (self.results / f".{run_id}.ingested").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

    def test_success_submits_exact_saved_artifact_and_writes_receipt(self):
        artifact, pending = self.make_pending()
        with patch.object(module.LearningClient, "record_artifact", return_value={"ok": True, "memory": True}) as send:
            self.assertEqual(self.extractor.recover_pending(), 1)
        send.assert_called_once_with(artifact.to_dict())
        self.assertFalse(pending.exists())
        receipt = json.loads((self.results / ".ga-test-001.ingested").read_text(encoding="utf-8"))
        self.assertEqual(receipt["candidate_digest"], candidate_digest(artifact))
        self.assertEqual(receipt["learning_ack"], {"ok": True, "memory": True})

    def test_negative_ack_retains_pending_and_writes_no_receipt(self):
        _, pending = self.make_pending()
        with patch.object(module.LearningClient, "record_artifact", return_value={"ok": False, "memory": False}):
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                self.extractor.recover_pending()
        self.assertTrue(pending.exists())
        self.assertFalse((self.results / ".ga-test-001.ingested").exists())

    def test_filename_run_id_mismatch_fails_closed(self):
        _, pending = self.make_pending(filename_run_id="ga-wrong-name")
        with patch.object(module.LearningClient, "record_artifact") as send:
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                self.extractor.recover_pending()
        send.assert_not_called()
        self.assertTrue(pending.exists())

    def test_unexpected_artifact_type_fails_closed(self):
        _, pending = self.make_pending(artifact_type="other.type")
        with patch.object(module.LearningClient, "record_artifact") as send:
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                self.extractor.recover_pending()
        send.assert_not_called()
        self.assertTrue(pending.exists())

    def test_valid_receipt_cleans_pending_without_resubmitting_and_repairs_audit_log(self):
        artifact, pending = self.make_pending()
        self.write_receipt(artifact)
        with patch.object(module.LearningClient, "record_artifact") as send:
            self.assertEqual(self.extractor.recover_pending(), 1)
        send.assert_not_called()
        self.assertFalse(pending.exists())
        self.assertIn('"run_id": "ga-test-001"', self.audit.read_text(encoding="utf-8"))

    def test_malformed_receipt_retains_pending(self):
        _, pending = self.make_pending()
        (self.results / ".ga-test-001.ingested").write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unresolved"):
            self.extractor.recover_pending()
        self.assertTrue(pending.exists())

    def test_receipt_directory_sync_failure_retains_pending_then_retry_cleans_it(self):
        artifact, pending = self.make_pending()
        self.write_receipt(artifact)
        with patch.object(self.extractor, "_fsync_directory", side_effect=OSError("sync failed")):
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                self.extractor.recover_pending()
        self.assertTrue(pending.exists())
        self.assertEqual(self.extractor.recover_pending(), 1)
        self.assertFalse(pending.exists())

    def test_mixed_success_and_failure_keeps_failed_snapshot_and_raises(self):
        _, successful = self.make_pending("ga-success")
        _, failed = self.make_pending("ga-failure")
        def acknowledge_by_run(artifact_data):
            if artifact_data["payload"]["run_id"] == "ga-success":
                return {"ok": True, "memory": True}
            return {"ok": False, "memory": False}

        with patch.object(
            module.LearningClient,
            "record_artifact",
            side_effect=acknowledge_by_run,
        ):
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                self.extractor.recover_pending()
        self.assertFalse(successful.exists())
        self.assertTrue((self.results / ".ga-success.ingested").exists())
        self.assertTrue(failed.exists())
        self.assertFalse((self.results / ".ga-failure.ingested").exists())

    def test_receipt_replace_failure_retains_pending_snapshot(self):
        _, pending = self.make_pending()
        with patch.object(module.os, "replace", side_effect=OSError("rename failed")):
            with patch.object(
                module.LearningClient,
                "record_artifact",
                return_value={"ok": True, "memory": True},
            ):
                with self.assertRaisesRegex(RuntimeError, "unresolved"):
                    self.extractor.recover_pending()
        self.assertTrue(pending.exists())
        self.assertFalse((self.results / ".ga-test-001.ingested").exists())

    def test_new_receipt_directory_sync_failure_is_repaired_by_retry(self):
        _, pending = self.make_pending()
        with patch.object(self.extractor, "_fsync_directory", side_effect=OSError("sync failed")):
            with patch.object(
                module.LearningClient,
                "record_artifact",
                return_value={"ok": True, "memory": True},
            ):
                with self.assertRaisesRegex(RuntimeError, "unresolved"):
                    self.extractor.recover_pending()
        self.assertTrue(pending.exists())
        self.assertTrue((self.results / ".ga-test-001.ingested").exists())

        self.assertEqual(self.extractor.recover_pending(), 1)
        self.assertFalse(pending.exists())

    def test_post_unlink_directory_sync_failure_restores_pending_snapshot(self):
        _, pending = self.make_pending()
        real_sync = self.extractor._fsync_directory
        calls = 0

        def fail_second_sync(directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("post-unlink sync failed")
            return real_sync(directory)

        with patch.object(self.extractor, "_fsync_directory", side_effect=fail_second_sync):
            with patch.object(
                module.LearningClient,
                "record_artifact",
                return_value={"ok": True, "memory": True},
            ):
                with self.assertRaisesRegex(RuntimeError, "unresolved"):
                    self.extractor.recover_pending()

        self.assertTrue(pending.exists(), "failed cleanup must leave a retryable snapshot")
        self.assertTrue((self.results / ".ga-test-001.ingested").exists())
        self.assertEqual(self.extractor.recover_pending(), 1)
        self.assertFalse(pending.exists())

    def test_symlinked_pending_artifact_is_rejected_without_submission(self):
        _, original = self.make_pending()
        link = self.results / ".ga-linked.learning-artifact.json"
        link.symlink_to(original)
        with patch.object(module.LearningClient, "record_artifact") as send:
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                self.extractor.recover_pending()
        send.assert_not_called()
        self.assertTrue(original.exists())
        self.assertTrue(link.is_symlink())

    def test_cli_recovery_does_not_require_settings_or_run_normal_ingestion(self):
        with patch.object(module.ParameterExtractor, "recover_pending", return_value=0) as recover, \
             patch.object(module.ParameterExtractor, "run") as normal:
            module.main([
                "--results-dir", str(self.results),
                "--settings-path", str(self.settings),
                "--evolution-log", str(self.audit),
                "--recover-pending",
            ])
        recover.assert_called_once_with()
        normal.assert_not_called()

    def test_cli_normal_ingestion_still_requires_settings(self):
        with self.assertRaisesRegex(FileNotFoundError, "Runtime settings file does not exist"):
            module.main([
                "--results-dir", str(self.results),
                "--settings-path", str(self.settings),
                "--evolution-log", str(self.audit),
            ])


if __name__ == "__main__":
    unittest.main()
