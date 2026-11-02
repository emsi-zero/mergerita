"""Start a mock GitLab app on a free port in a thread, for tests."""

import contextlib
import socket
import threading
import time
from collections.abc import Iterator

import uvicorn

from gitlab_mock.app import create_app

STARTUP_TIMEOUT_SECONDS = 10


@contextlib.contextmanager
def running_mock(
    token: str = "mock-token",
    webhook_url: str | None = None,
    webhook_secret: str = "",
) -> Iterator[str]:
    """Serve the mock and yield its base URL, for example http://127.0.0.1:5001."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(token, webhook_url, webhook_secret),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("gitlab_mock did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=STARTUP_TIMEOUT_SECONDS)
