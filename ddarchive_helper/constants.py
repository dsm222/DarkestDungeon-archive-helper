BUCKET_CLOSED_MANUAL = "closed_manual"
BUCKET_RUNTIME_F5 = "runtime_f5"
BUCKET_PRE_RAID_AUTO = "pre_raid_auto"
BUCKET_RUNTIME_POLL_TEMP = "_runtime_poll_temp"
BUCKETS = (
    BUCKET_CLOSED_MANUAL,
    BUCKET_RUNTIME_F5,
    BUCKET_PRE_RAID_AUTO,
    BUCKET_RUNTIME_POLL_TEMP,
)
GUI_BUCKETS = (BUCKET_CLOSED_MANUAL, BUCKET_RUNTIME_F5, BUCKET_PRE_RAID_AUTO)

BUCKET_TITLES = {
    BUCKET_CLOSED_MANUAL: "关闭游戏的存档",
    BUCKET_RUNTIME_F5: "运行时手动F5存档",
    BUCKET_PRE_RAID_AUTO: "进本前自动存档",
}

BUCKET_COLUMN_SPECS = {
    BUCKET_CLOSED_MANUAL: (
        ("created", "时间", 190, "w"),
        ("reason", "原因", 220, "w"),
        ("ok", "完整性", 90, "center"),
    ),
    BUCKET_RUNTIME_F5: (
        ("created", "时间", 220, "w"),
        ("ok", "完整性", 90, "center"),
    ),
    BUCKET_PRE_RAID_AUTO: (
        ("created", "时间", 220, "w"),
        ("ok", "完整性", 90, "center"),
    ),
}

BUCKET_LABELS = {
    BUCKET_CLOSED_MANUAL: "关闭游戏的存档",
    BUCKET_RUNTIME_F5: "运行时手动F5存档",
    BUCKET_PRE_RAID_AUTO: "进本前自动存档",
    BUCKET_RUNTIME_POLL_TEMP: "运行时临时轮询存档",
}

REASON_LABELS = {
    "manual_click": "手动保存（关闭游戏）",
    "hotkey_f5": "手动F5保存",
    "poll": "运行时轮询（临时）",
    "pre_restore_backup": "回档前自动备份",
    "pre_raid_auto": "进本前最后自动存档",
    "unknown": "未知",
}

DEFAULT_CONFIG = {
    "save_root": "",
    "profile": 0,
    "jar_path": r"tools\DDSaveEditor.jar",
    "state_poll_interval_ms": 1000,
    "inraid_state_poll_interval_ms": 10000,
    "runtime_snapshot_interval_ms": 5000,
    "retention_per_bucket": 50,
    "integrity_retry": 3,
    "quiet_window_ms": 800,
    "snapshots_dir": "snapshots",
    "logs_dir": "logs",
}

STEAM_APP_ID = "262060"
