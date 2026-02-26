from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path


class ActionLogger:
    def __init__(self, log_file: Path) -> None:
        self._log_file = log_file
        self._lock = threading.Lock()
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(self, level: str, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}\n"
        with self._lock:
            with self._log_file.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def info(self, message: str) -> None:
        self.log("INFO", message)

    def error(self, message: str) -> None:
        self.log("ERROR", message)
