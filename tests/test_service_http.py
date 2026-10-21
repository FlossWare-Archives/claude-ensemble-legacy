import json
import urllib.error
import urllib.request

from shared.service_http import start_service_http


def test_service_http_health_ping_and_operation_contract():
    def process(raw):
        payload = json.loads(raw)
        action = payload.get("op")
        if action == "ping":
            return json.dumps({"ok": True, "message": "pong"})
        if action == "echo":
            return json.dumps({"ok": True, "value": payload.get("value")})
        return json.dumps({"ok": False, "error": "unknown operation"})

    server = start_service_http("learning", 0, process, "op")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/health", timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"ok": True, "service": "learning"}

        with urllib.request.urlopen(base + "/learning/ping", timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read())["message"] == "pong"

        body = json.dumps({"op": "echo", "value": "contract"}).encode()
        request = urllib.request.Request(
            base + "/learning", data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read())["value"] == "contract"

        body = json.dumps({"op": "unknown"}).encode()
        request = urllib.request.Request(base + "/learning", data=body, method="POST")
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("negative service acknowledgment must not return 2xx")
        except urllib.error.HTTPError as error:
            assert error.code == 503
            assert json.loads(error.read())["ok"] is False
    finally:
        server.shutdown()
        server.server_close()
