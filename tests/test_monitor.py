from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from markos_runtime.lifecycle.monitor import monitor_once


def test_independent_monitor_falls_back_when_primary_route_is_unavailable() -> None:
    received: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(503)
            self.end_headers()

        def do_POST(self) -> None:
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200 if self.path == "/secondary" else 503)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert monitor_once(base + "/health", base + "/primary", base + "/secondary") == "secondary"
        assert len(received) == 2 and all(b"synthetic_runtime_unavailable" in x for x in received)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.example/",
        "https://127.0.0.1/",
        "http://user:secret@127.0.0.1/",
        "http://127.0.0.1/?secret=x",
    ],
)
def test_synthetic_monitor_cannot_disclose_to_live_or_credentialled_endpoints(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError):
        monitor_once(endpoint, "http://127.0.0.1/", "http://127.0.0.1/")
