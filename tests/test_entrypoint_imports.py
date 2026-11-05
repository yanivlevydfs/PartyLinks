"""The production entry point (`python -m partylinks serve`) imports cli first, in a fresh interpreter: a
circular import the test run never meets (tests import api first) took a deploy down (Nov 2026)."""

from __future__ import annotations

import subprocess
import sys


def test_cli_imports_in_a_fresh_interpreter() -> None:
    for module in ("partylinks.cli", "partylinks.artistdb", "partylinks.worker.runner"):
        done = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"{module}: {done.stderr.strip().splitlines()[-1:]}"
