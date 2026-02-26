from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .constants import DEFAULT_CONFIG, STEAM_APP_ID
from .errors import DDHelperError
from .utils import discover_profiles


@dataclass
class AppConfig:
    save_root: str
    profile: int
    jar_path: str
    state_poll_interval_ms: int
    inraid_state_poll_interval_ms: int
    runtime_snapshot_interval_ms: int
    retention_per_bucket: int
    integrity_retry: int
    quiet_window_ms: int
    snapshots_dir: str
    logs_dir: str
    base_dir: Path = field(repr=False)

    @property
    def profile_dir(self) -> Path:
        return Path(self.save_root) / f"profile_{self.profile}"

    @property
    def snapshots_root(self) -> Path:
        val = Path(self.snapshots_dir)
        if not val.is_absolute():
            val = self.base_dir / val
        return val

    @property
    def logs_root(self) -> Path:
        val = Path(self.logs_dir)
        if not val.is_absolute():
            val = self.base_dir / val
        return val

    @property
    def jar_file(self) -> Path:
        val = Path(self.jar_path)
        if not val.is_absolute():
            val = self.base_dir / val
        return val


def config_to_dict(config: AppConfig) -> Dict[str, Any]:
    return {
        "save_root": config.save_root,
        "profile": config.profile,
        "jar_path": config.jar_path,
        "state_poll_interval_ms": config.state_poll_interval_ms,
        "inraid_state_poll_interval_ms": config.inraid_state_poll_interval_ms,
        "runtime_snapshot_interval_ms": config.runtime_snapshot_interval_ms,
        "retention_per_bucket": config.retention_per_bucket,
        "integrity_retry": config.integrity_retry,
        "quiet_window_ms": config.quiet_window_ms,
        "snapshots_dir": config.snapshots_dir,
        "logs_dir": config.logs_dir,
    }


def save_config(config: AppConfig, config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config_to_dict(config), indent=2), encoding="utf-8")


def _read_steam_path_from_registry() -> Optional[Path]:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
        if isinstance(steam_path, str) and steam_path.strip():
            return Path(steam_path.replace("/", "\\"))
    except Exception:
        return None
    return None


def _steam_userdata_roots() -> List[Path]:
    candidates: List[Path] = []

    steam_from_registry = _read_steam_path_from_registry()
    if steam_from_registry:
        candidates.append(steam_from_registry / "userdata")

    env_pf86 = os.environ.get("ProgramFiles(x86)")
    if env_pf86:
        candidates.append(Path(env_pf86) / "Steam" / "userdata")

    env_pf = os.environ.get("ProgramFiles")
    if env_pf:
        candidates.append(Path(env_pf) / "Steam" / "userdata")

    candidates.append(Path("C:/Steam/userdata"))

    result: List[Path] = []
    seen = set()
    for cand in candidates:
        key = str(cand).lower()
        if key in seen:
            continue
        seen.add(key)
        if cand.exists() and cand.is_dir():
            result.append(cand)
    return result


def _score_save_root(path: Path) -> Tuple[int, float]:
    score = 0
    profiles = discover_profiles(path)
    score += len(profiles) * 10
    if (path / "steam_init.json").exists():
        score += 2
    if (path / "profile_0").exists():
        score += 1
    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = 0.0
    return score, mtime


def find_save_root_candidates() -> List[Path]:
    found: List[Path] = []
    for userdata in _steam_userdata_roots():
        pattern = f"*/{STEAM_APP_ID}/remote"
        for remote in userdata.glob(pattern):
            if remote.is_dir():
                found.append(remote.resolve())

    unique: List[Path] = []
    seen = set()
    for item in found:
        key = str(item).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    unique.sort(key=lambda p: _score_save_root(p), reverse=True)
    return unique


def detect_save_root() -> Optional[Path]:
    candidates = find_save_root_candidates()
    if not candidates:
        return None
    return candidates[0]


def normalize_save_root_path(path: Path) -> Path:
    val = path.expanduser()

    if re.fullmatch(r"profile_\d+", val.name) and val.parent.name.lower() == "remote":
        return val.parent.resolve()

    if val.name.lower() == "remote":
        return val.resolve()

    if val.name == STEAM_APP_ID and (val / "remote").is_dir():
        return (val / "remote").resolve()

    if val.name.isdigit() and (val / STEAM_APP_ID / "remote").is_dir():
        return (val / STEAM_APP_ID / "remote").resolve()

    if val.name.lower() == "userdata":
        matches = [x for x in val.glob(f"*/{STEAM_APP_ID}/remote") if x.is_dir()]
        if len(matches) == 1:
            return matches[0].resolve()

    return val.resolve()


def save_root_looks_valid(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if discover_profiles(path):
        return True
    return (path / "steam_init.json").exists()


def sync_profile_to_existing(config: AppConfig) -> None:
    if not config.save_root:
        return
    profiles = discover_profiles(Path(config.save_root))
    if profiles and config.profile not in profiles:
        config.profile = profiles[0]


def set_save_root(config: AppConfig, selected: Path) -> Path:
    root = normalize_save_root_path(selected)
    if not root.exists() or not root.is_dir():
        raise DDHelperError(f"save_root does not exist: {root}")
    config.save_root = str(root)
    sync_profile_to_existing(config)
    return root


def _build_config(data: Dict[str, Any], base_dir: Path) -> AppConfig:
    return AppConfig(
        save_root=str(data.get("save_root", "")).strip(),
        profile=int(data["profile"]),
        jar_path=str(data["jar_path"]),
        state_poll_interval_ms=int(data["state_poll_interval_ms"]),
        inraid_state_poll_interval_ms=int(data["inraid_state_poll_interval_ms"]),
        runtime_snapshot_interval_ms=int(data["runtime_snapshot_interval_ms"]),
        retention_per_bucket=int(data["retention_per_bucket"]),
        integrity_retry=int(data["integrity_retry"]),
        quiet_window_ms=int(data["quiet_window_ms"]),
        snapshots_dir=str(data["snapshots_dir"]),
        logs_dir=str(data["logs_dir"]),
        base_dir=base_dir,
    )


def load_or_create_config(config_path: Path) -> AppConfig:
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data: Dict[str, Any] = dict(DEFAULT_CONFIG)
    if config_path.exists():
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise DDHelperError("config.json must be an object")
        data.update(loaded)
    else:
        config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    config = _build_config(data, base_dir=config_path.parent.resolve())

    save_root_path = Path(config.save_root) if config.save_root else None
    needs_detect = save_root_path is None or (not save_root_path.exists())
    if needs_detect:
        detected = detect_save_root()
        if detected is not None:
            config.save_root = str(detected)
            sync_profile_to_existing(config)
            save_config(config, config_path)

    return config
