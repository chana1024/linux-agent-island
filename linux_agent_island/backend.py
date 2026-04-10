from __future__ import annotations

from .app.backend import BackendService, main

__all__ = ["BackendService", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
