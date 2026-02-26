from __future__ import annotations

import subprocess


def is_darkest_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Darkest.exe"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout.lower()
        return "darkest.exe" in output
    except Exception:
        return False
