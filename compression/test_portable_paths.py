"""Regression tests for checkout-independent compression artifact paths."""

import sys
from pathlib import Path

COMPRESSION_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPRESSION_DIR))

import phase2_verification
import summarizer
import test_critical_fixes


def test_summarizer_artifact_path_is_relative_to_module() -> None:
    expected = Path(summarizer.__file__).resolve().parent / "worker1_summarizer_metrics.json"
    assert summarizer.METRICS_OUTPUT_PATH == expected


def test_phase2_artifact_path_is_relative_to_module() -> None:
    expected = Path(phase2_verification.__file__).resolve().parent / "phase2_verdict.json"
    assert phase2_verification.VERDICT_OUTPUT_PATH == expected


def test_critical_fixes_test_imports_from_its_checkout() -> None:
    test_file = Path(test_critical_fixes.__file__).resolve()
    assert test_file.parent == COMPRESSION_DIR
    assert Path(summarizer.__file__).resolve().parent == test_file.parent
