from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .constants import BUCKET_LABELS, REASON_LABELS
from .errors import DDHelperError


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: Optional[datetime] = None) -> str:
    val = dt or now_utc()
    return val.astimezone(timezone.utc).isoformat()


def parse_iso_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def snapshot_id(prefix: str = "snap") -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{prefix}"


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


def bucket_label(bucket: str) -> str:
    return BUCKET_LABELS.get(bucket, bucket)


def discover_profiles(save_root: Path) -> List[int]:
    if not save_root.exists():
        return []
    result: List[int] = []
    for child in save_root.iterdir():
        if not child.is_dir():
            continue
        match = re.fullmatch(r"profile_(\d+)", child.name)
        if not match:
            continue
        result.append(int(match.group(1)))
    return sorted(set(result))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path, include_hash: bool = True) -> Dict[str, Dict[str, Any]]:
    if not root.exists():
        raise DDHelperError(f"Missing directory: {root}")
    result: Dict[str, Dict[str, Any]] = {}
    for item in sorted(root.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(root).as_posix()
        stat = item.stat()
        entry: Dict[str, Any] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
        if include_hash:
            entry["sha256"] = file_sha256(item)
        result[rel] = entry
    return result


def manifest_digest(manifest: Dict[str, Dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(manifest.keys()):
        entry = manifest[rel]
        line = f"{rel}|{entry.get('size')}|{entry.get('mtime_ns')}|{entry.get('sha256', '')}\n"
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def manifest_equal_for_copy(
    source: Dict[str, Dict[str, Any]],
    copied: Dict[str, Dict[str, Any]],
) -> bool:
    if set(source.keys()) != set(copied.keys()):
        return False
    for rel in source:
        left = source[rel]
        right = copied[rel]
        if left.get("size") != right.get("size"):
            return False
        if left.get("sha256") != right.get("sha256"):
            return False
    return True


def dir_size_bytes(root: Path) -> int:
    total = 0
    for item in root.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def first_or_none(values: Iterable[Path]) -> Optional[Path]:
    for value in values:
        return value
    return None
