from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Callable


class EventSocketServer:
    def __init__(self, socket_path: Path, on_event: Callable[[dict[str, object]], None]) -> None:
        self.socket_path = socket_path
        self.on_event = on_event
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(str(self.socket_path))
        self._sock = sock
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._sock is not None:
            self._sock.close()
        if self.socket_path.exists():
            self.socket_path.unlink()

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stopped.is_set():
            try:
                data = self._sock.recv(65536)
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            self.on_event(payload)


def emit_runtime_event(socket_path: Path, payload: dict[str, object]) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        try:
            sock.connect(str(socket_path))
            sock.send(json.dumps(payload).encode("utf-8"))
        except FileNotFoundError:
            return
    finally:
        sock.close()
