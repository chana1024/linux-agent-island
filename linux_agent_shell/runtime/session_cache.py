from __future__ import annotations

import json
from pathlib import Path

from ..models import AgentSession


class SessionCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[AgentSession]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        sessions: list[AgentSession] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                sessions.append(AgentSession.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return sessions

    def save(self, sessions: list[AgentSession]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [session.to_dict() for session in sessions]
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
