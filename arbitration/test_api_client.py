from types import SimpleNamespace
from unittest.mock import Mock

from arbitration.api_client import MultiModelClient


def test_cost_logging_failure_does_not_discard_successful_model_response():
    response = SimpleNamespace(
        model="sonnet",
        provider="claude-code",
        text="successful answer",
        input_tokens=12,
        output_tokens=7,
        request_id="request-1",
        raw_metadata={},
        cost_usd=0.0,
    )
    provider = Mock()
    provider.name = "claude-code"
    provider.generate.return_value = response
    registry = Mock()
    registry.resolve_model.return_value = ("claude-code", "sonnet")
    registry.resolve.return_value = provider
    cost_logger = Mock()
    cost_logger.log_call.side_effect = OSError("cost sink unavailable")

    client = MultiModelClient(registry=registry, cost_logger=cost_logger)
    metric = Mock()
    client._record_metric = metric

    actual = client.call_model_response(model="sonnet", prompt="hello")

    assert actual is response
    provider.generate.assert_called_once()
    metric.assert_called_once()
    assert metric.call_args.kwargs["status"] == "success"
    assert metric.call_args.kwargs.get("error_type") is None
