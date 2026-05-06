#!/usr/bin/env python3
"""Wrapper for the shared known issues engine."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> int:
    wrapper_path = Path(__file__).resolve()
    engine_path = wrapper_path.parents[2] / "claude-skill-kit" / "scripts" / "known_issues.py"
    if not engine_path.exists():
        print(f"error: shared engine not found at {engine_path}", file=sys.stderr)
        return 1
    sys.argv[0] = str(engine_path)
    runpy.run_path(str(engine_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
