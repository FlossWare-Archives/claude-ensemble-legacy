"""Tests for the Claude Code model provider."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from providers.claude_code import ClaudeCodeProvider
from providers.model_provider import ModelRequest


def _with_provider_metadata(payload: dict[str, object]) -> dict[str, object]:
    """Keep legacy response-parser tests compatible with enforced provider metadata."""
    model_usage = payload.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        payload["modelUsage"] = {
            "claude-sonnet-test": {
                "provider": "vertex",
                "inputTokens": 1,
                "outputTokens": 1,
            }
        }
    else:
        payload["modelUsage"] = {
            key: ({**value, "provider": "vertex"} if isinstance(value, dict) else value)
            for key, value in model_usage.items()
        }
    return payload


@pytest.fixture(autouse=True)
def _mock_auth_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy provider tests exercise CLI response behavior, not local CLI auth."""
    result = subprocess.CompletedProcess(
        args=["claude", "auth", "status"],
        returncode=0,
        stdout=json.dumps({"loggedIn": True, "apiProvider": "vertex"}),
        stderr="",
    )
    monkeypatch.setattr(
        "providers.claude_code.subprocess.run",
        lambda *args, **kwargs: result,
    )


def _process(
    stdout: str,
    returncode: int = 0,
    stderr: str = "",
    *,
    pid: int = 1234,
):
    process = MagicMock()
    process.stdout = stdout
    process.stderr = stderr
    process.returncode = returncode
    process.pid = pid
    process.poll.return_value = returncode
    process.communicate.return_value = (stdout, stderr)
    return process


def _run_provider(
    payload: dict[str, object],
    *,
    model: str | None = None,
    provider: ClaudeCodeProvider | None = None,
):
    process = _process(json.dumps(_with_provider_metadata(payload)))
    provider = provider or ClaudeCodeProvider()
    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch(
        "providers.claude_code.subprocess.Popen", return_value=process
    ) as popen:
        response = provider.generate(ModelRequest("hello", model=model))
    return response, process, popen


def test_first_party_auth_status_is_accepted_for_anthropic() -> None:
    provider = ClaudeCodeProvider(expected_provider="anthropic")
    result = subprocess.CompletedProcess(
        args=["claude", "auth", "status"],
        returncode=0,
        stdout=json.dumps({"loggedIn": True, "apiProvider": "firstParty"}),
        stderr="",
    )
    with patch("providers.claude_code.subprocess.run", return_value=result):
        provider._verify_upstream_provider_before_invocation("/usr/bin/claude")


def test_first_party_response_metadata_is_accepted_for_anthropic() -> None:
    provider = ClaudeCodeProvider(expected_provider="anthropic")
    payload = {
        "result": "hello",
        "modelUsage": {
            "claude-sonnet-test": {
                "provider": "firstParty",
                "inputTokens": 1,
                "outputTokens": 1,
            }
        },
    }
    process = _process(json.dumps(payload))
    auth = subprocess.CompletedProcess(
        args=["claude", "auth", "status"],
        returncode=0,
        stdout=json.dumps({"loggedIn": True, "apiProvider": "firstParty"}),
        stderr="",
    )
    with patch("providers.claude_code.shutil.which", return_value="/usr/bin/claude"), patch(
        "providers.claude_code.subprocess.run", return_value=auth
    ), patch("providers.claude_code.subprocess.Popen", return_value=process):
        response = provider.generate(ModelRequest("hello"))
    assert response.text == "hello"


def test_vertex_auth_status_is_rejected_for_anthropic() -> None:
    provider = ClaudeCodeProvider(expected_provider="anthropic")
    result = subprocess.CompletedProcess(
        args=["claude", "auth", "status"],
        returncode=0,
        stdout=json.dumps({"loggedIn": True, "apiProvider": "vertex"}),
        stderr="",
    )
    with patch("providers.claude_code.subprocess.run", return_value=result):
        with pytest.raises(RuntimeError, match="expected 'anthropic'"):
            provider._verify_upstream_provider_before_invocation("/usr/bin/claude")


def test_parses_success_and_usage_metadata() -> None:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "model": "claude-sonnet-test",
        "result": "hello",
        "total_cost_usd": 0.0123,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 25,
            "cache_read_input_tokens": 40,
            "cache_creation_input_tokens": 10,
            "thinking_tokens": 7,
        },
    }

    response, process, popen = _run_provider(payload, model="sonnet")

    assert response.provider == "claude-code"
    assert response.model == "claude-sonnet-test"
    assert response.text == "hello"
    assert response.input_tokens == 100
    assert response.output_tokens == 25
    assert response.cache_read_tokens == 40
    assert response.cache_creation_tokens == 10
    assert response.thinking_tokens == 7
    assert response.cost_usd == 0.0123
    assert response.raw_metadata["result"] == payload["result"]
    assert response.raw_metadata["modelUsage"]["claude-sonnet-test"]["provider"] == "vertex"

    args = popen.call_args.args[0]
    assert args[:3] == ["/usr/bin/claude", "--print", "--output-format=json"]
    assert args[-3:] == [
        "--model",
        "sonnet",
        "Follow the complete task supplied on standard input and respond to it directly.",
    ]
    assert popen.call_args.kwargs["start_new_session"] is True
    assert process.communicate.call_args.kwargs["input"] == "hello"


def test_parses_model_usage_when_top_level_metadata_is_absent() -> None:
    payload = {
        "result": "hello",
        "modelUsage": {
            "claude-haiku-test": {
                "inputTokens": 11,
                "outputTokens": 5,
                "cacheReadInputTokens": 3,
                "cacheCreationInputTokens": 2,
                "costUSD": 0.001,
            }
        },
    }

    response, _, _ = _run_provider(payload)

    assert response.model == "claude-haiku-test"
    assert response.input_tokens == 11
    assert response.output_tokens == 5
    assert response.cache_read_tokens == 3
    assert response.cache_creation_tokens == 2
    assert response.cost_usd == 0.001


def test_missing_executable_is_clear() -> None:
    provider = ClaudeCodeProvider()
    with patch("providers.claude_code.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="executable not found"):
            provider.generate(ModelRequest("hello"))


def test_nonzero_exit_preserves_stderr() -> None:
    process = _process("", returncode=2, stderr="authentication failed")
    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch("providers.claude_code.subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError, match="authentication failed"):
            ClaudeCodeProvider().generate(ModelRequest("hello"))


def test_malformed_json_is_rejected() -> None:
    process = _process("not-json")
    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch("providers.claude_code.subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError, match="malformed JSON"):
            ClaudeCodeProvider().generate(ModelRequest("hello"))


def test_timeout_terminates_process_group_and_reports_timeout() -> None:
    process = _process("")
    process.poll.return_value = None
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("claude", 1),
        ("", ""),
    ]

    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch("providers.claude_code.subprocess.Popen", return_value=process), patch(
        "providers.claude_code.os.killpg", side_effect=[None, None, ProcessLookupError]
    ) as killpg:
        with pytest.raises(TimeoutError, match="timed out after 1.0s"):
            ClaudeCodeProvider().generate(ModelRequest("hello", timeout=1.0))

    assert killpg.call_args_list == [
        ((process.pid, signal.SIGTERM), {}),
        ((process.pid, 0), {}),
        ((process.pid, 0), {}),
    ]
    assert process.communicate.call_args_list[0].kwargs["timeout"] == 1.0
    assert process.communicate.call_args_list[1].kwargs["timeout"] == 1.0
    assert process.terminate.call_count == 0


def test_timeout_escalates_to_sigkill_when_process_group_survives() -> None:
    process = _process("")
    process.poll.return_value = None
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("claude", 1),
        subprocess.TimeoutExpired("claude", 1),
        ("", ""),
    ]

    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch("providers.claude_code.subprocess.Popen", return_value=process), patch(
        "providers.claude_code.os.killpg", side_effect=[None, None, None]
    ) as killpg:
        with pytest.raises(TimeoutError):
            ClaudeCodeProvider().generate(ModelRequest("hello", timeout=1.0))

    assert killpg.call_args_list == [
        ((process.pid, signal.SIGTERM), {}),
        ((process.pid, 0), {}),
        ((process.pid, signal.SIGKILL), {}),
    ]


def test_timeout_cleanup_reports_failure_if_sigkill_does_not_close_pipes() -> None:
    process = _process("")
    process.poll.return_value = None
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("claude", 1),
        subprocess.TimeoutExpired("claude", 1),
        subprocess.TimeoutExpired("claude", 1),
    ]

    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch("providers.claude_code.subprocess.Popen", return_value=process), patch(
        "providers.claude_code.os.killpg", side_effect=[None, None, None]
    ) as killpg:
        with pytest.raises(
            RuntimeError, match="cleanup did not complete after SIGKILL"
        ):
            ClaudeCodeProvider().generate(ModelRequest("hello", timeout=1.0))

    assert killpg.call_args_list == [
        ((process.pid, signal.SIGTERM), {}),
        ((process.pid, 0), {}),
        ((process.pid, signal.SIGKILL), {}),
    ]
    assert process.communicate.call_count == 3


def test_env_overrides_preserve_inherited_environment() -> None:
    payload = {"result": "hello"}

    provider = ClaudeCodeProvider(env={"ENSEMBLE_TEST_OVERRIDE": "override"})
    with patch.dict(
        "providers.claude_code.os.environ",
        {"ENSEMBLE_TEST_INHERITED": "inherited"},
        clear=False,
    ), patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch(
        "providers.claude_code.subprocess.Popen", return_value=_process(json.dumps(_with_provider_metadata(payload)))
    ) as popen:
        provider.generate(ModelRequest("hello"))

    run_env = popen.call_args.kwargs["env"]
    assert run_env["ENSEMBLE_TEST_INHERITED"] == "inherited"
    assert run_env["ENSEMBLE_TEST_OVERRIDE"] == "override"


@pytest.mark.parametrize(
    "payload",
    [
        {"is_error": True, "result": "provider failed"},
        {"subtype": "error", "error": "provider failed"},
    ],
)
def test_provider_error_response_is_rejected(payload: dict[str, object]) -> None:
    process = _process(json.dumps(_with_provider_metadata(payload)))
    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch("providers.claude_code.subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError, match="Claude Code reported an error"):
            ClaudeCodeProvider().generate(ModelRequest("hello"))


@pytest.mark.parametrize(
    "payload",
    [
        {"result": "hello", "usage": {"input_tokens": True}},
        {"result": "hello", "usage": {"output_tokens": -1}},
        {"result": "hello", "total_cost_usd": True},
        {"result": "hello", "total_cost_usd": -0.01},
    ],
)
def test_invalid_accounting_metadata_is_rejected(payload: dict[str, object]) -> None:
    process = _process(json.dumps(_with_provider_metadata(payload)))
    with patch(
        "providers.claude_code.shutil.which", return_value="/usr/bin/claude"
    ), patch("providers.claude_code.subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError, match="invalid|negative"):
            ClaudeCodeProvider().generate(ModelRequest("hello"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
def test_generate_timeout_kills_ready_descendant_process_group(tmp_path) -> None:
    helper = tmp_path / "fake_claude_timeout.py"
    ready_file = tmp_path / "ready-timeout-test"
    helper.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import subprocess
import sys
import time

ready = pathlib.Path(os.environ["READY_FILE"])
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
ready.write_text(f"{os.getpid()}:{child.pid}")
time.sleep(30)
"""
    )
    helper.chmod(0o755)

    provider = ClaudeCodeProvider(
        executable=str(helper),
        env={"READY_FILE": str(ready_file)},
    )
    with patch("providers.claude_code.shutil.which", return_value=str(helper)):
        with pytest.raises(TimeoutError, match="timed out after 2.0s"):
            provider.generate(ModelRequest("hello", timeout=2.0))

    # The helper is launched by generate(), so readiness can only be checked
    # after the invocation returns. A generous timeout avoids coupling the
    # test to cold-start interpreter scheduling while the helper records its
    # parent and descendant PIDs.
    assert ready_file.exists(), "helper failed to write ready_file before timeout"

    parent_pid, child_pid = map(int, ready_file.read_text().split(":"))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("descendant process survived generate() timeout cleanup")

    with pytest.raises(ProcessLookupError):
        os.killpg(parent_pid, 0)


@pytest.mark.parametrize(
    ("model_request", "message"),
    [
        (
            ModelRequest(
                "current question",
                messages=(
                    {"role": "user", "content": "prior question"},
                    {"role": "assistant", "content": "prior answer"},
                ),
            ),
            "does not support prior conversation messages",
        ),
        (ModelRequest("hello", temperature=0), "does not support temperature overrides"),
        (ModelRequest("hello", max_tokens=8), "does not support max_tokens overrides"),
    ],
)
def test_unsupported_request_contract_fails_before_cli_invocation(
    model_request: ModelRequest, message: str
) -> None:
    with patch("providers.claude_code.shutil.which", return_value="/usr/bin/claude"), patch(
        "providers.claude_code.subprocess.Popen"
    ) as popen:
        with pytest.raises(ValueError, match=message):
            ClaudeCodeProvider().generate(model_request)
    popen.assert_not_called()
