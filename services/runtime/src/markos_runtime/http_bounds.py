"""Absolute socket shutdown independent of read progress or a cancelled caller thread."""

import http.client
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress


@contextmanager
def bounded_connection(connection: http.client.HTTPSConnection, deadline: float) -> Iterator[None]:
    expired = threading.Event()
    sockets: list[socket.socket] = []

    def abort() -> None:
        expired.set()
        for current in sockets:
            with suppress(OSError):
                current.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                current.close()
        connection.close()

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    connection.timeout = min(
        connection.timeout if isinstance(connection.timeout, (int, float)) else remaining, remaining
    )
    timer = threading.Timer(remaining, abort)
    timer.daemon = True
    timer.start()
    try:
        connection.connect()
        if connection.sock is not None:
            sockets.append(connection.sock)
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError
        yield
    finally:
        timer.cancel()
        abort()
