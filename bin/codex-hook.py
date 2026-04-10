#!/usr/bin/env python3
from pathlib import Path
import sys


def _project_root() -> Path:
    script_path = Path(__file__).resolve()
    root_marker_path = script_path.with_name("linux-agent-island-root.txt")
    try:
        root_marker = root_marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        root_marker = ""
    if root_marker:
        return Path(root_marker).expanduser()
    return script_path.parents[1]


sys.path.insert(0, str(_project_root()))

from linux_agent_island.hooks import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "codex", *sys.argv[1:]]
    raise SystemExit(main())
