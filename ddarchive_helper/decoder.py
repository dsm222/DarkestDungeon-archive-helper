from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import DDHelperError
from .logger import ActionLogger


class SaveDecoder:
    def __init__(self, jar_path: Path, logger: ActionLogger) -> None:
        self.jar_path = jar_path
        self.logger = logger

    def ensure_ready(self) -> None:
        if not self.jar_path.exists():
            raise DDHelperError(f"DDSaveEditor.jar not found: {self.jar_path}")
        try:
            subprocess.run(["java", "-version"], capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise DDHelperError("Java runtime not found in PATH") from exc

    def decode_file(self, src_file: Path) -> Dict[str, Any]:
        if not src_file.exists():
            raise DDHelperError(f"Missing file: {src_file}")
        with tempfile.TemporaryDirectory(prefix="dd_decode_") as tmp_dir:
            out_file = Path(tmp_dir) / "decoded.json"
            cmd = [
                "java",
                "-jar",
                str(self.jar_path),
                "decode",
                "-o",
                str(out_file),
                str(src_file),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise DDHelperError(
                    f"Decode failed for {src_file.name}: {result.stderr.strip() or result.stdout.strip()}"
                )
            return json.loads(out_file.read_text(encoding="utf-8"))

    def read_inraid(self, profile_dir: Path) -> Optional[bool]:
        decoded = self.decode_file(profile_dir / "persist.game.json")
        base_root = decoded.get("base_root", {})
        inraid = base_root.get("inraid")
        if isinstance(inraid, bool):
            return inraid
        return None

    def read_steam_cloud_enabled(self, remote_root: Path) -> Optional[bool]:
        init_file = remote_root / "steam_init.json"
        if not init_file.exists():
            return None
        decoded = self.decode_file(init_file)
        base_root = decoded.get("base_root", {})
        value = base_root.get("steam_cloud_enabled")
        if isinstance(value, bool):
            return value
        return None
