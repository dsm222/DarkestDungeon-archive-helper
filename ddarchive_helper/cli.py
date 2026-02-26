from __future__ import annotations

import argparse
import queue
from pathlib import Path

from .config import AppConfig, load_or_create_config
from .decoder import SaveDecoder
from .gui import run_gui
from .logger import ActionLogger
from .monitor import MonitorEngine
from .snapshots import SnapshotManager


def run_verify(config: AppConfig, decoder: SaveDecoder) -> int:
    ok = True
    print("== Verify ==")
    print(f"save_root: {config.save_root or '(not set)'}")
    print(f"profile: {config.profile}")
    print(f"profile_dir: {config.profile_dir}")
    print(f"jar_path: {config.jar_file}")
    print(f"snapshots_root: {config.snapshots_root}")

    if not config.save_root:
        ok = False
        print("ERROR: save_root is not configured")
    elif not Path(config.save_root).exists():
        ok = False
        print("ERROR: save_root does not exist")
    if not config.profile_dir.exists():
        ok = False
        print("ERROR: profile directory does not exist")
    if not config.jar_file.exists():
        ok = False
        print("ERROR: DDSaveEditor.jar does not exist")

    try:
        decoder.ensure_ready()
        print("Java + jar: OK")
    except Exception as exc:
        ok = False
        print(f"ERROR: decoder not ready: {exc}")

    if config.profile_dir.exists() and config.jar_file.exists():
        try:
            inraid = decoder.read_inraid(config.profile_dir)
            print(f"inraid: {inraid}")
        except Exception as exc:
            ok = False
            print(f"ERROR: read inraid failed: {exc}")
        if config.save_root:
            try:
                cloud = decoder.read_steam_cloud_enabled(Path(config.save_root))
                print(f"steam_cloud_enabled: {cloud}")
            except Exception as exc:
                ok = False
                print(f"ERROR: read steam cloud failed: {exc}")

    return 0 if ok else 1


def run_monitor(engine: MonitorEngine) -> int:
    engine.start(with_hotkey=True)
    print("Monitor running. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                event = engine.events.get(timeout=1.0)
            except queue.Empty:
                continue
            et = event.get("type")
            if et == "snapshot_created":
                snap = event.get("snapshot", {})
                print(f"[snapshot] {snap.get('bucket')} {snap.get('snapshot_id')} {snap.get('reason')}")
            elif et == "anchor_set":
                snap = event.get("snapshot", {})
                print(f"[anchor] {snap.get('bucket')} {snap.get('snapshot_id')}")
            elif et == "error":
                print(f"[error] {event.get('message')}")
            elif et == "info":
                print(f"[info] {event.get('message')}")
    except KeyboardInterrupt:
        print("\nStopping monitor...")
    finally:
        engine.stop()
    return 0


def main() -> int:
    script_dir = Path(__file__).resolve().parent.parent
    config_path = script_dir / "config.json"
    config = load_or_create_config(config_path)

    parser = argparse.ArgumentParser(description="Darkest Dungeon restore helper")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("gui", help="Start Tkinter GUI")
    sub.add_parser("monitor", help="Run background monitor without GUI")
    sub.add_parser("verify", help="Verify environment and decode access")

    args = parser.parse_args()

    config.logs_root.mkdir(parents=True, exist_ok=True)
    logger = ActionLogger(config.logs_root / "actions.log")
    decoder = SaveDecoder(config.jar_file, logger)
    manager = SnapshotManager(config, decoder, logger)
    engine = MonitorEngine(config, decoder, manager, logger)

    if args.command == "verify":
        return run_verify(config, decoder)
    if args.command == "monitor":
        return run_monitor(engine)
    if args.command == "gui":
        return run_gui(engine, manager, logger, config_path)
    return 1
