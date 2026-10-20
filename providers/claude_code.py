"""Claude Code CLI model provider.

This provider deliberately treats Claude Code as the authentication/runtime
boundary. Ensemble does not read Claude Code credentials or call Anthropic
directly.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from typing import Any, Mapping, Sequence

from .model_provider import ModelProvider, ModelRequest, ModelResponse
from .model_capabilities import ModelCapabilities


class ClaudeCodeProvider(ModelProvider):
    """Invoke Claude models through the installed Claude Code CLI."""

    name = "claude-code"

    def __init__(
        self,
        *,
        executable: str = "claude",
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        default_model: str | None = None,
        expected_provider: str | None = None,
    ) -> None:
        self.executable = executable
        self.env = dict(env) if env is not None else None
        self.cwd = cwd
        self.default_model = default_model
        configured_provider = (
            expected_provider
            if expected_provider is not None
            else os.environ.get("ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER", "vertex")
        )
        if not isinstance(configured_provider, str) or not configured_provider.strip():
            raise ValueError("Expected upstream provider must be a non-empty value")
        self.expected_provider = configured_provider.strip().lower()

    @property
    def capabilities(self) -> ModelCapabilities:
        """Expose the exact controls supported by the Claude Code CLI adapter."""
        return ModelCapabilities(
            provider=self.name,
            supports_system_prompt=True,
            supports_timeout_override=True,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        # Defense in depth: callers should use build_model_request(), but direct
        # provider invocations must enforce the same declared capability contract.
        self.capabilities.validate(request)

        executable = shutil.which(self.executable)
        if executable is None:
            raise RuntimeError(
                f"Claude Code executable not found: {self.executable!r}. "
                "Install Claude Code and ensure it is on PATH."
            )

        command: list[str] = [
            executable,
            "--print",
            "--output-format=json",
        ]
        model = request.model or self.default_model
        if model:
            command.extend(["--model", model])
        if request.system_prompt:
            command.extend(["--system-prompt", request.system_prompt])

        # Claude Code can fail to consume stdin-only prompts in some runtime
        # configurations. Keep the full task on stdin, and provide a short
        # positional instruction telling the CLI to process that input.
        command.append(
            "Follow the complete task supplied on standard input and respond to it directly."
        )

        if self.expected_provider:
            self._verify_upstream_provider_before_invocation(executable)

        process = self._start_process(command)
        try:
            stdout, stderr = process.communicate(
                input=request.prompt,
                timeout=request.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate_process_tree(process)
            raise TimeoutError(
                f"Claude Code invocation timed out after {request.timeout:.1f}s"
            ) from exc
        except OSError as exc:
            self._terminate_process_tree(process)
            raise RuntimeError(f"Failed to communicate with Claude Code: {exc}") from exc

        if process.returncode != 0:
            detail = (stderr or stdout).strip()
            if len(detail) > 2000:
                detail = detail[:2000] + "..."
            raise RuntimeError(
                f"Claude Code exited with status {process.returncode}"
                + (f": {detail}" if detail else "")
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude Code returned malformed JSON") from exc

        response = self._parse_response(payload, requested_model=model)
        if self.expected_provider:
            actual_provider = self._reported_upstream_provider(payload, response.model)
            if actual_provider is None:
                raise RuntimeError(
                    "Claude Code did not consistently report its upstream provider; "
                    f"expected {self.expected_provider!r}"
                )
            if self._normalize_provider_label(actual_provider) != self.expected_provider:
                raise RuntimeError(
                    f"Claude Code reported upstream provider {actual_provider!r}, "
                    f"expected {self.expected_provider!r}; response rejected"
                )
        return response

    @staticmethod
    def _normalize_provider_label(value: str) -> str:
        """Normalize Claude Code auth aliases to CE's canonical provider names."""
        normalized = value.strip().lower()
        return "anthropic" if normalized == "firstparty" else normalized

    def _verify_upstream_provider_before_invocation(self, executable: str) -> None:
        """Check CLI auth routing before sending the prompt to the CLI."""
        try:
            result = subprocess.run(
                [executable, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10.0,
                env=self._run_env(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                "Unable to verify Claude Code's upstream provider before invocation; "
                "refusing to send the prompt"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                "Claude Code auth status failed; refusing to send the prompt "
                "without verifying its upstream provider"
            )
        try:
            status = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Claude Code auth status did not return valid JSON; "
                "refusing to send the prompt"
            ) from exc
        if not isinstance(status, dict):
            raise RuntimeError(
                "Claude Code auth status returned an unexpected response; "
                "refusing to send the prompt"
            )

        actual = status.get("apiProvider")
        if not isinstance(actual, str) or not actual.strip():
            raise RuntimeError(
                "Claude Code auth status did not identify apiProvider; "
                "refusing to send the prompt"
            )
        if self._normalize_provider_label(actual) != self.expected_provider:
            raise RuntimeError(
                f"Claude Code is configured for upstream provider {actual.strip()!r}, "
                f"expected {self.expected_provider!r}; refusing to send the prompt"
            )

    def _start_process(self, command: list[str]) -> subprocess.Popen[str]:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "cwd": self.cwd,
            "env": self._run_env(),
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Isolate the CLI and its descendants so timeout cleanup cannot signal
            # the gateway process or leave child processes holding our pipes open.
            kwargs["start_new_session"] = True

        try:
            return subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise RuntimeError(f"Failed to execute Claude Code: {exc}") from exc

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        """Terminate Claude Code and its descendants with bounded cleanup."""
        if os.name == "nt":
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        "Claude Code process cleanup did not complete after kill"
                    ) from exc
            return

        # _start_process creates a fresh session, making the child PID the process
        # group ID. TERM first, then escalate to KILL if the group or its inherited
        # pipes survive the bounded grace period.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            # Preserve a best-effort fallback if process-group signaling is denied.
            if process.poll() is None:
                process.terminate()

        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except OSError:
            pass

        try:
            process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError:
                process.kill()
            try:
                process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    "Claude Code process cleanup did not complete after SIGKILL"
                ) from exc
        else:
            # The direct child can exit while a descendant keeps the process group
            # alive and stdout/stderr pipes open. Check the group again before return.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            except OSError:
                pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
                except OSError:
                    process.kill()
                try:
                    process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        "Claude Code process cleanup did not complete after SIGKILL"
                    ) from exc
    def _parse_response(
        self,
        payload: Any,
        *,
        requested_model: str | None,
    ) -> ModelResponse:
        if not isinstance(payload, dict):
            raise RuntimeError("Claude Code JSON response must be an object")

        if payload.get("is_error") is True or payload.get("subtype") not in (None, "success"):
            detail = payload.get("result") or payload.get("error") or payload.get("subtype")
            raise RuntimeError(f"Claude Code reported an error: {detail}")

        text = payload.get("result")
        if not isinstance(text, str):
            raise RuntimeError("Claude Code JSON response does not contain a string result")

        usage = payload.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        model_usage = payload.get("modelUsage")
        if not isinstance(model_usage, dict):
            model_usage = {}

        model = self._response_model(payload, model_usage, requested_model)
        selected_usage = self._selected_model_usage(model_usage, model)

        input_tokens = self._int_value(
            usage, "input_tokens", selected_usage, "inputTokens"
        )
        output_tokens = self._int_value(
            usage, "output_tokens", selected_usage, "outputTokens"
        )
        cache_read_tokens = self._int_value(
            usage, "cache_read_input_tokens", selected_usage, "cacheReadInputTokens"
        )
        cache_creation_tokens = self._int_value(
            usage,
            "cache_creation_input_tokens",
            selected_usage,
            "cacheCreationInputTokens",
        )
        thinking_tokens = self._first_int(
            usage,
            ("thinking_tokens", "thinkingTokens"),
            selected_usage,
            ("thinkingTokens", "thinking_tokens"),
        )

        cost = payload.get("total_cost_usd")
        if cost is None:
            cost = selected_usage.get("costUSD")
        cost_usd = self._nonnegative_float(cost, "cost_usd")

        return ModelResponse(
            provider=self.name,
            model=model,
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            thinking_tokens=thinking_tokens,
            cost_usd=cost_usd,
            raw_metadata=payload,
        )

    @staticmethod
    def _reported_upstream_provider(
        payload: dict[str, Any], model: str
    ) -> str | None:
        """Return the provider reported by Claude Code, not this adapter's name."""
        model_usage = payload.get("modelUsage")
        if not isinstance(model_usage, dict):
            return None
        if not model_usage:
            return None
        providers: set[str] = set()
        for usage in model_usage.values():
            if not isinstance(usage, dict):
                return None
            provider = usage.get("provider")
            if not isinstance(provider, str) or not provider.strip():
                return None
            providers.add(provider.strip().lower())
        if len(providers) != 1:
            return None
        return next(iter(providers))

    @staticmethod
    def _response_model(
        payload: dict[str, Any],
        model_usage: dict[str, Any],
        requested_model: str | None,
    ) -> str:
        model = payload.get("model")
        if isinstance(model, str) and model:
            return model
        if requested_model:
            return requested_model
        if len(model_usage) == 1:
            only_model = next(iter(model_usage))
            if isinstance(only_model, str) and only_model:
                return only_model
        return "unknown"

    @staticmethod
    def _selected_model_usage(
        model_usage: dict[str, Any], model: str
    ) -> dict[str, Any]:
        value = model_usage.get(model)
        return value if isinstance(value, dict) else {}

    def _run_env(self) -> dict[str, str] | None:
        if self.env is None:
            return None
        run_env = os.environ.copy()
        run_env.update(self.env)
        return run_env

    @staticmethod
    def _nonnegative_float(value: Any, field: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"Claude Code returned invalid {field}: {value!r}")
        if value < 0:
            raise RuntimeError(f"Claude Code returned negative {field}: {value!r}")
        return float(value)

    @staticmethod
    def _int_value(
        primary: dict[str, Any],
        primary_key: str,
        secondary: dict[str, Any],
        secondary_key: str,
    ) -> int:
        value = primary.get(primary_key)
        if value is None:
            value = secondary.get(secondary_key)
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"Claude Code returned invalid token count: {value!r}")
        if value < 0:
            raise RuntimeError(f"Claude Code returned negative token count: {value!r}")
        return int(value)

    @staticmethod
    def _first_int(
        primary: dict[str, Any],
        primary_keys: Sequence[str],
        secondary: dict[str, Any],
        secondary_keys: Sequence[str],
    ) -> int:
        for mapping, keys in ((primary, primary_keys), (secondary, secondary_keys)):
            for key in keys:
                value = mapping.get(key)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise RuntimeError(f"Claude Code returned invalid token count: {value!r}")
                if value < 0:
                    raise RuntimeError(f"Claude Code returned negative token count: {value!r}")
                return int(value)
        return 0
