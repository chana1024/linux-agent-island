#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linux_agent_island.hooks import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "codex", *sys.argv[1:]]
    raise SystemExit(main())
