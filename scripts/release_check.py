#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTOS_ROOT = ROOT.parent / "AgentOS"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Kachu Phase 6 release checks.")
    parser.add_argument("--skip-kachu-tests", action="store_true")
    parser.add_argument("--skip-agentos-tests", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    return parser.parse_args()


def _run(command: list[str], cwd: Path) -> None:
    print(f"\n==> Running in {cwd.name}: {' '.join(command)}")
    completed = subprocess.run(command, cwd=str(cwd), check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    args = parse_args()

    if not args.skip_kachu_tests:
        _run([sys.executable, "-m", "pytest"], ROOT)

    if not args.skip_agentos_tests:
        _run([sys.executable, "-m", "pytest"], AGENTOS_ROOT)

    if not args.skip_smoke:
        _run([sys.executable, str(ROOT / "scripts" / "smoke_phase6.py")], ROOT)

    print("\nPhase 6 release checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())