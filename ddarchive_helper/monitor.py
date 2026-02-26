from __future__ import annotations

import ctypes
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from .config import AppConfig
from .constants import BUCKET_CLOSED_MANUAL, BUCKET_RUNTIME_F5, BUCKET_RUNTIME_POLL_TEMP
from .decoder import SaveDecoder
from .errors import DDHelperError
from .logger import ActionLogger
from .snapshots import SnapshotInfo, SnapshotManager
from .system import is_darkest_running
from .utils import iso_utc, now_utc


class WinPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WinMsg(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt", WinPoint),
        ("lPrivate", ctypes.c_uint),
    ]


class HotkeyListener(threading.Thread):
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    VK_F5 = 0x74

    def __init__(self, on_f5: Any, logger: ActionLogger) -> None:
        super().__init__(daemon=True)
        self.on_f5 = on_f5
        self.logger = logger
        self._thread_id: Optional[int] = None
        self._registered = False
        self._stop_evt = threading.Event()
        self._ready_evt = threading.Event()

    def run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        ok = bool(user32.RegisterHotKey(None, 1, 0, self.VK_F5))
        self._registered = ok
        if ok:
            self.logger.info("Global hotkey F5 registered")
        else:
            self.logger.error("Failed to register global hotkey F5")
        self._ready_evt.set()

        msg = WinMsg()
        while not self._stop_evt.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == self.WM_HOTKEY:
                try:
                    self.on_f5()
                except Exception as exc:
                    self.logger.error(f"Hotkey callback failed: {exc}")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._registered:
            user32.UnregisterHotKey(None, 1)
            self.logger.info("Global hotkey F5 unregistered")

    def start(self) -> None:
        super().start()
        self._ready_evt.wait(timeout=2.0)

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        self.join(timeout=2.0)

    @property
    def registered(self) -> bool:
        return self._registered


class MonitorEngine:
    def __init__(self, config: AppConfig, decoder: SaveDecoder, manager: SnapshotManager, logger: ActionLogger) -> None:
        self.config = config
        self.decoder = decoder
        self.manager = manager
        self.logger = logger
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._hotkey: Optional[HotkeyListener] = None
        self._f5_requested = threading.Event()
        self._last_inraid: Optional[bool] = None
        self._last_inraid_read_time = 0.0
        self._last_poll_time = 0.0
        self._running = False
        self.game_running: bool = False
        self.inraid: Optional[bool] = None
        self.cloud_enabled: Optional[bool] = None
        self.last_error: str = ""
        self._state_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def _emit(self, event_type: str, **payload: Any) -> None:
        data = {"type": event_type, "ts": iso_utc(), **payload}
        self.events.put(data)

    def _set_error(self, message: str) -> None:
        with self._state_lock:
            self.last_error = message
        self.logger.error(message)
        self._emit("error", message=message)

    def _update_state(self, game_running: bool, inraid: Optional[bool]) -> None:
        with self._state_lock:
            self.game_running = game_running
            self.inraid = inraid

    def start(self, with_hotkey: bool = True) -> None:
        if self._running:
            return
        self.decoder.ensure_ready()
        self.manager.clear_bucket(BUCKET_RUNTIME_POLL_TEMP)
        self.manager.clear_bucket("runtime_poll")
        self._stop_evt.clear()
        self._running = True
        self._emit("info", message="监控已启动")
        if with_hotkey:
            self._hotkey = HotkeyListener(on_f5=self.request_f5_snapshot, logger=self.logger)
            self._hotkey.start()
            self._emit("hotkey", registered=self._hotkey.registered)
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._hotkey:
            self._hotkey.stop()
            self._hotkey = None
        self._running = False
        self._emit("info", message="监控已停止")

    def request_f5_snapshot(self) -> None:
        self._f5_requested.set()
        self._emit("info", message="收到F5请求")

    def on_profile_changed(self) -> None:
        self._f5_requested.clear()
        self._last_inraid = None
        self._last_inraid_read_time = 0.0
        self._last_poll_time = 0.0
        self.game_running = False
        self.inraid = None
        self.last_error = ""
        self._emit("info", message=f"已切换到档位 profile_{self.config.profile}")
        self._emit("state", game_running=False, inraid=None, cloud_enabled=self.cloud_enabled)

    def on_save_root_changed(self) -> None:
        self._f5_requested.clear()
        self._last_inraid = None
        self._last_inraid_read_time = 0.0
        self._last_poll_time = 0.0
        self.game_running = False
        self.inraid = None
        self.last_error = ""
        self._emit("info", message=f"已切换存档目录: {self.config.save_root}")
        self._emit("state", game_running=False, inraid=None, cloud_enabled=self.cloud_enabled)

    def _should_read_inraid(self, now_time: float, game_running: bool) -> bool:
        if not game_running:
            return False
        if self._f5_requested.is_set():
            return True
        if self._last_inraid is True:
            interval_ms = max(1000, self.config.inraid_state_poll_interval_ms)
            return (now_time - self._last_inraid_read_time) * 1000 >= interval_ms
        return True

    def trigger_manual_closed_snapshot(self) -> SnapshotInfo:
        if is_darkest_running():
            raise DDHelperError("Game is running; closed-game save is disabled.")
        inraid = None
        try:
            inraid = self.decoder.read_inraid(self.config.profile_dir)
        except Exception:
            inraid = None
        snap = self.manager.capture_snapshot(
            bucket=BUCKET_CLOSED_MANUAL,
            reason="manual_click",
            inraid_at_capture=inraid,
            dedupe_on_source_hash=False,
        )
        if snap is None:
            raise DDHelperError("Manual snapshot skipped unexpectedly")
        self._emit("snapshot_created", snapshot=snap.to_dict())
        return snap

    def trigger_f5_snapshot(self, source_reason: str = "hotkey_f5") -> Optional[SnapshotInfo]:
        if not is_darkest_running():
            self._emit("info", message="游戏未运行，忽略F5存档")
            return None
        inraid = None
        try:
            inraid = self.decoder.read_inraid(self.config.profile_dir)
        except Exception:
            inraid = None
        snap = self.manager.capture_snapshot(
            bucket=BUCKET_RUNTIME_F5,
            reason=source_reason,
            inraid_at_capture=inraid,
            dedupe_on_source_hash=False,
        )
        if snap:
            self._emit("snapshot_created", snapshot=snap.to_dict())
        return snap

    def restore(self, snapshot: SnapshotInfo) -> SnapshotInfo:
        pre_backup = self.manager.restore_snapshot(snapshot)
        self._emit("restore_done", snapshot=snapshot.to_dict(), pre_backup=pre_backup.to_dict())
        return pre_backup

    def _refresh_cloud_flag(self) -> None:
        if not self.config.save_root:
            self.cloud_enabled = None
            return
        try:
            value = self.decoder.read_steam_cloud_enabled(Path(self.config.save_root))
            self.cloud_enabled = value
        except Exception as exc:
            self._set_error(f"Steam cloud status read failed: {exc}")

    def _run_loop(self) -> None:
        self._refresh_cloud_flag()
        self._last_inraid_read_time = 0.0
        self._last_poll_time = 0.0

        while not self._stop_evt.is_set():
            start = time.time()
            try:
                game_running = is_darkest_running()
                inraid: Optional[bool] = self._last_inraid

                if self._should_read_inraid(start, game_running):
                    try:
                        inraid = self.decoder.read_inraid(self.config.profile_dir)
                        self._last_inraid_read_time = start
                    except Exception as exc:
                        self._last_inraid_read_time = start
                        self._set_error(f"inraid read failed: {exc}")
                elif not game_running:
                    inraid = None

                self._update_state(game_running, inraid)
                self._emit("state", game_running=game_running, inraid=inraid, cloud_enabled=self.cloud_enabled)

                if self._f5_requested.is_set():
                    self._f5_requested.clear()
                    try:
                        self.trigger_f5_snapshot(source_reason="hotkey_f5")
                    except Exception as exc:
                        self._set_error(f"F5 snapshot failed: {exc}")

                if self._last_inraid is not None and inraid is not None and (not self._last_inraid and inraid):
                    try:
                        anchor = self.manager.promote_latest_poll_to_pre_raid(now_utc())
                        if anchor:
                            self._emit("anchor_set", snapshot=anchor.to_dict())
                    except Exception as exc:
                        self._set_error(f"Anchor update failed: {exc}")

                if inraid is True and self._last_inraid is not True:
                    self.manager.clear_bucket(BUCKET_RUNTIME_POLL_TEMP)

                if not game_running:
                    self.manager.clear_bucket(BUCKET_RUNTIME_POLL_TEMP)

                self._last_inraid = inraid if inraid is not None else self._last_inraid

                if (
                    game_running
                    and inraid is False
                    and (start - self._last_poll_time) * 1000 >= self.config.runtime_snapshot_interval_ms
                ):
                    try:
                        snap = self.manager.capture_snapshot(
                            bucket=BUCKET_RUNTIME_POLL_TEMP,
                            reason="poll",
                            inraid_at_capture=inraid,
                            dedupe_on_source_hash=True,
                        )
                        if snap:
                            self._emit("info", message="已生成运行时临时轮询存档")
                    except Exception as exc:
                        self._set_error(f"Polling snapshot failed: {exc}")
                    self._last_poll_time = time.time()

            except Exception as exc:
                self._set_error(f"Monitor loop error: {exc}\n{traceback.format_exc()}")

            elapsed = (time.time() - start) * 1000
            target_interval_ms = self.config.state_poll_interval_ms
            if game_running and inraid is True:
                target_interval_ms = max(self.config.state_poll_interval_ms, self.config.inraid_state_poll_interval_ms)
            wait_ms = max(50, target_interval_ms - int(elapsed))
            self._stop_evt.wait(wait_ms / 1000.0)
