import json

from shared.service_http import start_service_http
from shared.thompson_client import ThompsonClient


def test_thompson_client_uses_rest_when_no_socket_is_supplied():
    def process(raw):
        payload = json.loads(raw)
        if payload.get("action") == "ping":
            return json.dumps({"ok": True, "message": "pong"})
        return json.dumps({"ok": False, "error": "unsupported operation"})

    server = start_service_http("thompson", 0, process, "action")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        client = ThompsonClient(service_url=base, enable_circuit_breaker=False)
        assert client.ping() is True
    finally:
        server.shutdown()
        server.server_close()
