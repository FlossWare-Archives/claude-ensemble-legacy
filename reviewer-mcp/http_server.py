#!/usr/bin/env python3
"""Streamable HTTP-style JSON-RPC adapter for the reviewer MCP."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from broker import handle


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/mcp":
            self.send_error(404)
            return
        expected = os.environ.get("MCP_AUTH_TOKEN")
        supplied = self.headers.get("Authorization", "")
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        if expected:
            if supplied != "Bearer " + expected:
                self.send_error(401)
                return
        elif host not in LOOPBACK_HOSTS:
            self.send_error(401, "MCP_AUTH_TOKEN is required for non-loopback MCP_HOST")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                self.send_error(400)
                return
            request = json.loads(self.rfile.read(length))
            response = handle(request)
            if response is None:
                self.send_response(202)
                self.end_headers()
                return
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                # Disconnect does not cancel a Jules job; the durable session
                # registry lets an identical retry resume/retrieve its result.
                return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            payload = json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32000, "message": str(exc)}
            }).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8790"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
