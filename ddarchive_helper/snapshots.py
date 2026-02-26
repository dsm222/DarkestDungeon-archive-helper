from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import AppConfig
from .constants import (
    BUCKET_CLOSED_MANUAL,
    BUCKET_PRE_RAID_AUTO,
    BUCKET_RUNTIME_POLL_TEMP,
    BUCKETS,
)
from .decoder import SaveDecoder
from .errors import DDHelperError
from .logger import ActionLogger
from .system import is_darkest_running
from .utils import (
    build_manifest,
    dir_size_bytes,
    iso_utc,
    manifest_digest,
    manifest_equal_for_copy,
    parse_iso_utc,
    snapshot_id,
)


@dataclass
class SnapshotInfo:
    snapshot_id: str
    bucket: str
    reason: str
    created_at: str
    profile: int
    inraid_at_capture: Optional[bool]
    pre_raid_anchor: bool
    integrity_ok: bool
    source_hash: str
    snapshot_size_bytes: int
    path: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "bucket": self.bucket,
            "reason": self.reason,
            "created_at": self.created_at,
            "profile": self.profile,
            "inraid_at_capture": self.inraid_at_capture,
            "pre_raid_anchor": self.pre_raid_anchor,
            "integrity_ok": self.integrity_ok,
            "source_hash": self.source_hash,
            "snapshot_size_bytes": self.snapshot_size_bytes,
            "path": self.path,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "SnapshotInfo":
        return SnapshotInfo(
            snapshot_id=str(data["snapshot_id"]),
            bucket=str(data["bucket"]),
            reason=str(data["reason"]),
            created_at=str(data["created_at"]),
            profile=int(data["profile"]),
            inraid_at_capture=data.get("inraid_at_capture"),
            pre_raid_anchor=bool(data.get("pre_raid_anchor", False)),
            integrity_ok=bool(data.get("integrity_ok", False)),
            source_hash=str(data.get("source_hash", "")),
            snapshot_size_bytes=int(data.get("snapshot_size_bytes", 0)),
            path=str(data["path"]),
        )


class SnapshotManager:
    def __init__(self, config: AppConfig, decoder: SaveDecoder, logger: ActionLogger) -> None:
        self.config = config
        self.decoder = decoder
        self.logger = logger
        self.lock = threading.RLock()
        self.config.snapshots_root.mkdir(parents=True, exist_ok=True)

    def _bucket_dir(self, bucket: str) -> Path:
        if bucket not in BUCKETS:
            raise DDHelperError(f"Unknown bucket: {bucket}")
        return self.config.snapshots_root / bucket / f"profile_{self.config.profile}"

    def _meta_file(self, snapshot_dir: Path) -> Path:
        return snapshot_dir / "meta.json"

    def _write_meta(self, snapshot_dir: Path, info: SnapshotInfo) -> None:
        meta = self._meta_file(snapshot_dir)
        meta.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")

    def _read_meta(self, snapshot_dir: Path) -> Optional[SnapshotInfo]:
        meta = self._meta_file(snapshot_dir)
        if not meta.exists():
            return None
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            return SnapshotInfo.from_dict(data)
        except Exception:
            return None

    def _copy_profile_tree(self, src: Path, dst: Path) -> None:
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

    def _wait_for_quiet(self, src: Path, quiet_window_ms: int) -> None:
        if quiet_window_ms <= 0:
            return
        first = build_manifest(src, include_hash=False)
        time.sleep(quiet_window_ms / 1000.0)
        second = build_manifest(src, include_hash=False)
        if first != second:
            raise DDHelperError("Source files changed during quiet window")

    def _latest_source_hash(self, bucket: str) -> Optional[str]:
        snapshots = self.list_snapshots(bucket=bucket, include_invalid=True)
        if not snapshots:
            return None
        return snapshots[0].source_hash

    def _validate_staging(self, staging_dir: Path) -> bool:
        try:
            value = self.decoder.read_inraid(staging_dir)
            return isinstance(value, bool)
        except Exception as exc:
            self.logger.error(f"Staging validation failed: {exc}")
            return False

    def _cleanup_dir(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def _bucket_retention(self, bucket: str) -> int:
        if bucket == BUCKET_RUNTIME_POLL_TEMP:
            return 1
        return self.config.retention_per_bucket

    def _apply_retention(self, bucket: str) -> None:
        max_keep = self._bucket_retention(bucket)
        snapshots = self.list_snapshots(bucket=bucket, include_invalid=True)
        for stale in snapshots[max_keep:]:
            stale_dir = Path(stale.path)
            self._cleanup_dir(stale_dir)

    def clear_bucket(self, bucket: str) -> None:
        with self.lock:
            bucket_dir = self.config.snapshots_root / bucket / f"profile_{self.config.profile}"
            if not bucket_dir.exists():
                return
            for child in bucket_dir.iterdir():
                if child.is_dir():
                    self._cleanup_dir(child)

    def capture_snapshot(
        self,
        bucket: str,
        reason: str,
        inraid_at_capture: Optional[bool],
        dedupe_on_source_hash: bool = False,
    ) -> Optional[SnapshotInfo]:
        with self.lock:
            src_profile = self.config.profile_dir
            if not src_profile.exists():
                raise DDHelperError(f"Profile directory not found: {src_profile}")
            bucket_dir = self._bucket_dir(bucket)
            bucket_dir.mkdir(parents=True, exist_ok=True)

            retries = max(1, self.config.integrity_retry)
            last_error = "unknown"

            for attempt in range(1, retries + 1):
                snap_id = snapshot_id(prefix=reason)
                staging_dir = bucket_dir / f".staging_{snap_id}"
                final_dir = bucket_dir / snap_id
                try:
                    self._cleanup_dir(staging_dir)
                    self._wait_for_quiet(src_profile, self.config.quiet_window_ms)

                    before_manifest = build_manifest(src_profile, include_hash=True)
                    source_hash = manifest_digest(before_manifest)
                    if dedupe_on_source_hash and source_hash == self._latest_source_hash(bucket):
                        return None

                    staging_dir.mkdir(parents=True, exist_ok=True)
                    self._copy_profile_tree(src_profile, staging_dir)

                    after_manifest = build_manifest(src_profile, include_hash=True)
                    if before_manifest != after_manifest:
                        raise DDHelperError("Source files changed while copying")

                    staging_manifest = build_manifest(staging_dir, include_hash=True)
                    if not manifest_equal_for_copy(before_manifest, staging_manifest):
                        raise DDHelperError("Copied snapshot mismatch")

                    integrity_ok = self._validate_staging(staging_dir)
                    if not integrity_ok:
                        raise DDHelperError("Decode validation failed")

                    info = SnapshotInfo(
                        snapshot_id=snap_id,
                        bucket=bucket,
                        reason=reason,
                        created_at=iso_utc(),
                        profile=self.config.profile,
                        inraid_at_capture=inraid_at_capture,
                        pre_raid_anchor=False,
                        integrity_ok=True,
                        source_hash=source_hash,
                        snapshot_size_bytes=sum(v["size"] for v in before_manifest.values()),
                        path=str(final_dir),
                    )
                    self._write_meta(staging_dir, info)
                    if final_dir.exists():
                        raise DDHelperError(f"Snapshot id collision: {final_dir}")
                    staging_dir.rename(final_dir)
                    self.logger.info(f"Snapshot created: {final_dir}")
                    self._apply_retention(bucket)
                    return info
                except Exception as exc:
                    last_error = str(exc)
                    self.logger.error(
                        f"Capture failed bucket={bucket} reason={reason} attempt={attempt}/{retries}: {exc}"
                    )
                    self._cleanup_dir(staging_dir)
                    time.sleep(0.2)
            raise DDHelperError(f"Capture failed after {retries} attempts: {last_error}")

    def list_snapshots(self, bucket: Optional[str] = None, include_invalid: bool = False) -> List[SnapshotInfo]:
        with self.lock:
            result: List[SnapshotInfo] = []
            targets: Iterable[str] = (bucket,) if bucket else BUCKETS
            for entry in targets:
                bucket_dir = self._bucket_dir(entry)
                if not bucket_dir.exists():
                    continue
                for child in bucket_dir.iterdir():
                    if not child.is_dir() or child.name.startswith(".staging_"):
                        continue
                    info = self._read_meta(child)
                    if info is None:
                        if include_invalid:
                            synthetic = SnapshotInfo(
                                snapshot_id=child.name,
                                bucket=entry,
                                reason="unknown",
                                created_at=iso_utc(datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)),
                                profile=self.config.profile,
                                inraid_at_capture=None,
                                pre_raid_anchor=False,
                                integrity_ok=False,
                                source_hash="",
                                snapshot_size_bytes=dir_size_bytes(child),
                                path=str(child),
                            )
                            result.append(synthetic)
                        continue
                    result.append(info)
            result.sort(key=lambda x: x.created_at, reverse=True)
            if not include_invalid:
                result = [x for x in result if x.integrity_ok]
            return result

    def promote_latest_poll_to_pre_raid(self, at_or_before: datetime) -> Optional[SnapshotInfo]:
        with self.lock:
            polls = self.list_snapshots(bucket=BUCKET_RUNTIME_POLL_TEMP, include_invalid=False)
            if not polls:
                return None

            target: Optional[SnapshotInfo] = None
            for snap in polls:
                created = parse_iso_utc(snap.created_at)
                if created <= at_or_before:
                    if target is None or parse_iso_utc(target.created_at) < created:
                        target = snap
            if target is None:
                return None

            pre_raid_snaps = self.list_snapshots(bucket=BUCKET_PRE_RAID_AUTO, include_invalid=False)
            for snap in pre_raid_snaps:
                info = self._read_meta(Path(snap.path))
                if info is None or not info.pre_raid_anchor:
                    continue
                info.pre_raid_anchor = False
                self._write_meta(Path(info.path), info)

            src_dir = Path(target.path)
            dst_bucket = self._bucket_dir(BUCKET_PRE_RAID_AUTO)
            dst_bucket.mkdir(parents=True, exist_ok=True)
            new_id = snapshot_id(prefix="pre_raid_auto")
            dst_dir = dst_bucket / new_id
            shutil.copytree(src_dir, dst_dir)

            promoted = target
            promoted.snapshot_id = new_id
            promoted.bucket = BUCKET_PRE_RAID_AUTO
            promoted.reason = "pre_raid_auto"
            promoted.pre_raid_anchor = True
            promoted.path = str(dst_dir)
            self._write_meta(dst_dir, promoted)

            self._apply_retention(BUCKET_PRE_RAID_AUTO)
            self.clear_bucket(BUCKET_RUNTIME_POLL_TEMP)
            self.logger.info(f"Pre-raid snapshot promoted: {promoted.bucket}/{promoted.snapshot_id}")
            return promoted

    def restore_snapshot(self, target: SnapshotInfo) -> SnapshotInfo:
        with self.lock:
            if is_darkest_running():
                raise DDHelperError("Darkest.exe is running. Close the game before restore.")

            target_dir = Path(target.path)
            if not target_dir.exists():
                raise DDHelperError(f"Target snapshot not found: {target_dir}")

            backup = self.capture_snapshot(
                bucket=BUCKET_CLOSED_MANUAL,
                reason="pre_restore_backup",
                inraid_at_capture=None,
                dedupe_on_source_hash=False,
            )
            if backup is None:
                raise DDHelperError("Failed to create pre-restore backup")

            profile_dir = self.config.profile_dir
            parent = profile_dir.parent
            new_dir = parent / f"{profile_dir.name}.__new"
            old_dir = parent / f"{profile_dir.name}.__old"

            self._cleanup_dir(new_dir)
            self._cleanup_dir(old_dir)

            try:
                new_dir.mkdir(parents=True, exist_ok=True)
                for item in target_dir.iterdir():
                    if item.name == "meta.json":
                        continue
                    target_item = new_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, target_item)
                    else:
                        shutil.copy2(item, target_item)

                profile_dir.rename(old_dir)
                new_dir.rename(profile_dir)

                value = self.decoder.read_inraid(profile_dir)
                if not isinstance(value, bool):
                    raise DDHelperError("Restore validation failed")

                self._cleanup_dir(old_dir)
                self.logger.info(
                    f"Restore complete from {target.bucket}/{target.snapshot_id}; pre-backup={backup.snapshot_id}"
                )
                return backup
            except Exception as exc:
                self.logger.error(f"Restore failed, rolling back: {exc}")
                try:
                    if profile_dir.exists():
                        self._cleanup_dir(profile_dir)
                    if old_dir.exists():
                        old_dir.rename(profile_dir)
                except Exception as rollback_exc:
                    self.logger.error(f"Rollback failed: {rollback_exc}")
                raise
            finally:
                self._cleanup_dir(new_dir)
