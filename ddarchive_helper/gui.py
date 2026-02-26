from __future__ import annotations

import queue
import random
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover - Pillow is optional at runtime
    Image = None
    ImageTk = None

from .config import (
    detect_save_root,
    save_config,
    save_root_looks_valid,
    set_save_root,
)
from .constants import BUCKET_COLUMN_SPECS, BUCKET_TITLES, GUI_BUCKETS
from .logger import ActionLogger
from .monitor import MonitorEngine
from .snapshots import SnapshotInfo, SnapshotManager
from .utils import bucket_label, discover_profiles, reason_label


class GUIApp:
    def __init__(
        self,
        root: tk.Tk,
        engine: MonitorEngine,
        manager: SnapshotManager,
        logger: ActionLogger,
        config_path: Path,
    ) -> None:
        self.root = root
        self.engine = engine
        self.manager = manager
        self.logger = logger
        self.config_path = config_path
        self.assets_root = Path(__file__).resolve().parents[1] / "images"
        self.fonts_root = Path(__file__).resolve().parents[1] / "assets" / "fonts"
        self._icon_photo: Optional[tk.PhotoImage] = None
        self._icon_badge: Optional[tk.PhotoImage] = None
        self._girl_photo_raw: Optional[tk.PhotoImage] = None
        self._girl_photo: Optional[Any] = None
        self.root.title("暗黑地牢回档助手")
        self.root.geometry("1780x980")
        self.root.minsize(1480, 820)
        self._item_index: Dict[str, SnapshotInfo] = {}
        self._tree_columns: Dict[str, List[str]] = {}
        self._profile_values: List[str] = []
        self._settings_win: Optional[tk.Toplevel] = None
        self._settings_vars: Dict[str, tk.StringVar] = {}
        self._advanced_visible = False

        self.status_profile = tk.StringVar(value="当前档位：未知")
        self.status_save_root = tk.StringVar(value="游戏存档位置：未设置")
        self.status_game = tk.StringVar(value="游戏运行状态：未知")
        self.status_inraid = tk.StringVar(value="副本状态(inraid)：未知")
        self.status_cloud = tk.StringVar(value="Steam云同步：未知")
        self.status_error = tk.StringVar(value="最近错误：无")
        self.status_info = tk.StringVar(value="空闲待命")

        self.profile_var = tk.StringVar(value=str(self.engine.config.profile))
        self.save_root_var = tk.StringVar(value=self.engine.config.save_root)

        self._configure_theme()
        self._load_visual_assets()
        self._build_ui()
        self.root.after(20, self._apply_windows_dark_titlebar)
        has_root = self._ensure_save_root(interactive=False)
        self.refresh_profiles()
        self._poll_events()
        self.refresh_snapshots()
        self._refresh_state_labels()

        if not has_root:
            self.root.after(150, self.on_open_settings)

    def _pick_font(self, preferred: List[str], fallback: str) -> str:
        families = {name.lower(): name for name in tkfont.families(self.root)}
        for item in preferred:
            found = families.get(item.lower())
            if found:
                return found
        return fallback

    def _register_bundled_maple_font(self) -> None:
        if os.environ.get("DD_GUI_SAFE_MODE") == "1":
            return
        if sys.platform != "win32":
            return

        candidates = [
            self.fonts_root / "MapleMono-NF-CN-Regular.ttf",
            self.fonts_root / "MapleMono-NF-CN-Bold.ttf",
        ]
        existing = [path for path in candidates if path.exists()]
        if not existing:
            return

        try:
            import ctypes

            FR_PRIVATE = 0x10
            for font_file in existing:
                ctypes.windll.gdi32.AddFontResourceExW(str(font_file), FR_PRIVATE, 0)
        except Exception:
            return

    def _apply_windows_dark_titlebar(self) -> None:
        if os.environ.get("DD_GUI_SAFE_MODE") == "1":
            return
        if sys.platform != "win32":
            return
        try:
            import ctypes

            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            value = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE for different Windows builds
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attr,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
        except Exception:
            return

    def _configure_theme(self) -> None:
        self.colors = {
            "bg": "#120d0d",
            "panel": "#1e1717",
            "panel_alt": "#2a2020",
            "field": "#171212",
            "border": "#5a4740",
            "text": "#eadfcd",
            "title": "#f2e8d7",
            "muted": "#b29f8f",
            "warn": "#ff8b77",
            "accent": "#8f3126",
            "accent_hover": "#a03a2d",
            "accent_press": "#74281f",
            "gold": "#8a5a1e",
            "gold_hover": "#9d6a27",
            "gold_press": "#6f4819",
        }
        self.root.configure(bg=self.colors["bg"])

        self._register_bundled_maple_font()
        self.title_font = self._pick_font(
            [
                "Maple Mono NF CN",
                "Maple Mono",
                "Maple Mono NF",
                "Microsoft YaHei UI",
                "Segoe UI",
            ],
            "Microsoft YaHei UI",
        )
        self.body_font = self._pick_font(
            [
                "Maple Mono NF CN",
                "Maple Mono",
                "Maple Mono NF",
                "Microsoft YaHei UI",
                "Segoe UI",
            ],
            "Microsoft YaHei UI",
        )

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        # Font family may contain spaces (e.g. "Maple Mono NF CN"), so brace it for Tcl.
        self.root.option_add("*Font", f"{{{self.body_font}}} 10")
        self.style.configure(".", font=(self.body_font, 10))

        self.style.configure("Dark.TFrame", background=self.colors["bg"])
        self.style.configure("Card.TFrame", background=self.colors["panel"])
        self.style.configure("TNotebook", background=self.colors["panel"], borderwidth=0)
        self.style.configure(
            "TNotebook.Tab",
            padding=(24, 10),
            font=(self.body_font, 10, "bold"),
        )
        self.style.map(
            "TNotebook.Tab",
            padding=[("selected", (24, 10)), ("!selected", (24, 10))],
            expand=[("selected", [0, 0, 0, 0]), ("!selected", [0, 0, 0, 0])],
        )
        self.style.configure(
            "Card.TLabelframe",
            background=self.colors["panel"],
            bordercolor=self.colors["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "Card.TLabelframe.Label",
            background=self.colors["panel"],
            foreground=self.colors["title"],
            font=(self.title_font, 12, "bold"),
        )
        self.style.configure(
            "Hero.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["title"],
            font=(self.title_font, 16, "bold"),
        )
        self.style.configure(
            "Body.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["text"],
            font=(self.body_font, 11),
        )
        self.style.configure(
            "Muted.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["muted"],
            font=(self.body_font, 10),
        )
        self.style.configure(
            "Warn.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["warn"],
            font=(self.body_font, 10),
        )
        self.style.configure(
            "Info.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["title"],
            font=(self.body_font, 11, "bold"),
        )
        self.style.configure(
            "PrimaryStart.TButton",
            background=self.colors["accent"],
            foreground=self.colors["title"],
            borderwidth=1,
            font=(self.body_font, 12, "bold"),
            padding=(18, 12),
        )
        self.style.map(
            "PrimaryStart.TButton",
            background=[
                ("pressed", self.colors["accent_press"]),
                ("active", self.colors["accent_hover"]),
                ("disabled", "#4a3431"),
            ],
            foreground=[("disabled", "#a48e82")],
        )
        self.style.configure(
            "PrimaryStop.TButton",
            background="#3f3533",
            foreground=self.colors["title"],
            borderwidth=1,
            font=(self.body_font, 12, "bold"),
            padding=(18, 12),
        )
        self.style.map(
            "PrimaryStop.TButton",
            background=[
                ("pressed", "#2e2625"),
                ("active", "#5a4c48"),
                ("disabled", "#2d2626"),
            ],
            foreground=[("disabled", "#8b7a72")],
        )
        self.style.configure(
            "Important.TButton",
            background=self.colors["gold"],
            foreground=self.colors["title"],
            borderwidth=1,
            font=(self.body_font, 11, "bold"),
            padding=(14, 9),
        )
        self.style.map(
            "Important.TButton",
            background=[
                ("pressed", self.colors["gold_press"]),
                ("active", self.colors["gold_hover"]),
                ("disabled", "#4c3e30"),
            ],
            foreground=[("disabled", "#a08973")],
        )
        self.style.configure(
            "Subtle.TButton",
            background=self.colors["panel_alt"],
            foreground=self.colors["text"],
            borderwidth=1,
            font=(self.body_font, 10),
            padding=(12, 7),
        )
        self.style.map(
            "Subtle.TButton",
            background=[("pressed", "#201717"), ("active", "#352828"), ("disabled", "#2a2020")],
            foreground=[("disabled", "#86766c")],
        )
        self.style.configure(
            "Dark.TCombobox",
            fieldbackground=self.colors["field"],
            background=self.colors["field"],
            foreground=self.colors["text"],
            arrowcolor=self.colors["title"],
            bordercolor=self.colors["border"],
            lightcolor=self.colors["border"],
            darkcolor=self.colors["border"],
            padding=4,
        )
        self.style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", self.colors["field"]), ("disabled", "#2a2020")],
            foreground=[("readonly", self.colors["text"]), ("disabled", "#87796f")],
            background=[("readonly", self.colors["field"]), ("active", "#261d1d")],
            selectbackground=[("readonly", self.colors["accent"])],
            selectforeground=[("readonly", self.colors["title"])],
        )
        self.style.configure(
            "Dark.Treeview",
            background=self.colors["field"],
            fieldbackground=self.colors["field"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
            rowheight=30,
            font=(self.body_font, 10),
        )
        self.style.map(
            "Dark.Treeview",
            background=[("selected", self.colors["accent"])],
            foreground=[("selected", self.colors["title"])],
        )
        self.style.configure(
            "Dark.Treeview.Heading",
            background=self.colors["panel_alt"],
            foreground=self.colors["title"],
            relief="flat",
            font=(self.body_font, 10, "bold"),
        )
        self.style.map(
            "Dark.Treeview.Heading",
            background=[("active", "#3a2d2d")],
            foreground=[("active", self.colors["title"])],
        )
        self.style.configure("Dark.TNotebook", background=self.colors["panel"], borderwidth=0)
        self.style.configure(
            "Dark.TNotebook.Tab",
            background="#2c2323",
            foreground="#9e9186",
            font=(self.body_font, 10, "bold"),
            padding=(24, 10),
            width=18,
        )
        self.style.map(
            "Dark.TNotebook.Tab",
            background=[("selected", self.colors["panel"]), ("active", "#362a2a")],
            foreground=[("selected", self.colors["title"]), ("active", self.colors["text"])],
            padding=[("selected", (24, 10)), ("!selected", (24, 10))],
            expand=[("selected", [0, 0, 0, 0]), ("!selected", [0, 0, 0, 0])],
        )
        self.style.configure(
            "Dark.Vertical.TScrollbar",
            background="#3a2d2d",
            troughcolor="#161111",
            bordercolor=self.colors["border"],
            arrowcolor=self.colors["title"],
        )
        self.style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", "#594444"), ("pressed", "#2d2222")],
        )

    def _load_visual_assets(self) -> None:
        sign_path = self.assets_root / "sign.png"
        if sign_path.exists():
            try:
                self._icon_photo = tk.PhotoImage(file=str(sign_path))
                self.root.iconphoto(True, self._icon_photo)
                self._icon_badge = self._scale_photo(self._icon_photo, max_width=82, max_height=82)
            except tk.TclError:
                self._icon_photo = None
                self._icon_badge = None
        self._load_random_girl_art()

    def _scale_photo(self, photo: tk.PhotoImage, max_width: int, max_height: int) -> tk.PhotoImage:
        width = photo.width()
        height = photo.height()
        factor = max(
            1,
            (width + max_width - 1) // max_width,
            (height + max_height - 1) // max_height,
        )
        if factor <= 1:
            return photo
        return photo.subsample(factor, factor)

    def _load_random_girl_art(self) -> None:
        girls_dir = self.assets_root / "girls"
        images = sorted(girls_dir.glob("*.png")) if girls_dir.exists() else []
        if not images:
            self._girl_photo_raw = None
            self._girl_photo = None
            self.status_info.set("未找到 images/girls 画像资源")
            return

        chosen = random.choice(images)
        try:
            if Image is not None and ImageTk is not None:
                with Image.open(chosen) as image:
                    width, height = image.size
                    ratio = min(560 / width, 820 / height, 1.0)
                    target = (max(1, int(width * ratio)), max(1, int(height * ratio)))
                    if target != (width, height):
                        resampling = getattr(Image, "Resampling", Image).LANCZOS
                        image = image.resize(target, resample=resampling)
                    self._girl_photo = ImageTk.PhotoImage(image)
                self._girl_photo_raw = None
            else:
                self._girl_photo_raw = tk.PhotoImage(file=str(chosen))
                self._girl_photo = self._scale_photo(self._girl_photo_raw, max_width=560, max_height=820)
        except Exception:
            self._girl_photo_raw = None
            self._girl_photo = None
            self.status_info.set("驻营画像加载失败")

    def _apply_girl_art_widget(self) -> None:
        if not hasattr(self, "girl_art_label"):
            return
        if self._girl_photo is not None:
            self.girl_art_label.configure(image=self._girl_photo, text="")
        else:
            self.girl_art_label.configure(image="", text="无可用画像")

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, style="Dark.TFrame", padding=14)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=0, minsize=430)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=0, minsize=560)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main, style="Dark.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)

        hero = ttk.LabelFrame(left, text="营地面板", style="Card.TLabelframe", padding=12)
        hero.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        hero.columnconfigure(1, weight=1)
        if self._icon_badge is not None:
            ttk.Label(hero, image=self._icon_badge, style="Body.TLabel").grid(
                row=0, column=0, rowspan=2, sticky="nw", padx=(0, 10)
            )
        ttk.Label(hero, text="暗黑地牢回档助手", style="Hero.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(
            hero,
            textvariable=self.status_info,
            style="Info.TLabel",
            justify="left",
            wraplength=260,
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))

        core = ttk.LabelFrame(left, text="核心操作", style="Card.TLabelframe", padding=12)
        core.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        core.columnconfigure(0, weight=1)
        core.columnconfigure(1, weight=1)
        self.btn_start = ttk.Button(core, text="开始监控", command=self.on_start, style="PrimaryStart.TButton")
        self.btn_stop = ttk.Button(core, text="停止监控", command=self.on_stop, style="PrimaryStop.TButton")
        self.btn_manual = ttk.Button(
            core,
            text="手动存档（仅关游戏后）",
            command=self.on_manual_save,
            style="Important.TButton",
        )
        self.btn_restore = ttk.Button(core, text="回档所选", command=self.on_restore, style="Important.TButton")

        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=(0, 8))
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=(0, 8))
        self.btn_restore.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.btn_manual.grid(row=2, column=0, columnspan=2, sticky="ew")

        status = ttk.LabelFrame(left, text="运行状态", style="Card.TLabelframe", padding=10)
        status.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(status, textvariable=self.status_profile, style="Body.TLabel").pack(anchor="w")
        ttk.Label(status, textvariable=self.status_game, style="Body.TLabel").pack(anchor="w")
        ttk.Label(status, textvariable=self.status_inraid, style="Body.TLabel").pack(anchor="w")
        ttk.Label(status, textvariable=self.status_cloud, style="Muted.TLabel", wraplength=340, justify="left").pack(
            anchor="w"
        )
        ttk.Label(status, textvariable=self.status_save_root, style="Muted.TLabel", wraplength=340, justify="left").pack(
            anchor="w", pady=(2, 0)
        )
        ttk.Label(status, textvariable=self.status_error, style="Warn.TLabel", wraplength=340, justify="left").pack(
            anchor="w", pady=(2, 0)
        )

        self.btn_toggle_advanced = ttk.Button(
            left,
            text="更多功能 ▸",
            style="Subtle.TButton",
            command=self._toggle_advanced_panel,
        )
        self.btn_toggle_advanced.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        self.advanced_panel = ttk.LabelFrame(left, text="次要功能", style="Card.TLabelframe", padding=10)
        self.advanced_panel.grid(row=4, column=0, sticky="ew")
        self.advanced_panel.columnconfigure(1, weight=1)

        ttk.Label(self.advanced_panel, text="当前档位", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.profile_combo = ttk.Combobox(
            self.advanced_panel,
            textvariable=self.profile_var,
            width=8,
            state="readonly",
            style="Dark.TCombobox",
        )
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_change)
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 8))

        self.btn_settings = ttk.Button(
            self.advanced_panel,
            text="设置",
            command=self.on_open_settings,
            style="Subtle.TButton",
        )
        self.btn_profile_refresh = ttk.Button(
            self.advanced_panel,
            text="刷新档位",
            command=self.refresh_profiles,
            style="Subtle.TButton",
        )
        self.btn_f5 = ttk.Button(
            self.advanced_panel,
            text="立即F5存档（按钮替代）",
            command=self.on_f5_button,
            style="Subtle.TButton",
        )
        self.btn_refresh = ttk.Button(
            self.advanced_panel,
            text="刷新列表",
            command=self.refresh_snapshots,
            style="Subtle.TButton",
        )

        self.btn_settings.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        self.btn_profile_refresh.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(0, 6))
        self.btn_f5.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.btn_refresh.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.advanced_panel.grid_remove()

        center = ttk.LabelFrame(main, text="存档记录（选择后可回档）", style="Card.TLabelframe", padding=8)
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)

        self.snapshot_tabs = ttk.Notebook(center, style="Dark.TNotebook")
        self.snapshot_tabs.grid(row=0, column=0, sticky="nsew")

        self.trees: Dict[str, ttk.Treeview] = {}
        for bucket in GUI_BUCKETS:
            tab = ttk.Frame(self.snapshot_tabs, style="Card.TFrame", padding=(6, 6, 6, 4))
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(0, weight=1)
            self.snapshot_tabs.add(tab, text=BUCKET_TITLES[bucket])

            spec = BUCKET_COLUMN_SPECS[bucket]
            col_names = tuple(item[0] for item in spec)
            tree = ttk.Treeview(
                tab,
                columns=col_names,
                show="headings",
                selectmode="browse",
                style="Dark.Treeview",
            )
            self._tree_columns[bucket] = list(col_names)
            for name, title, width, anchor in spec:
                tree.heading(name, text=title)
                tree.column(name, width=width, anchor=anchor)
            tree.grid(row=0, column=0, sticky="nsew")

            scrollbar = ttk.Scrollbar(
                tab,
                orient=tk.VERTICAL,
                command=tree.yview,
                style="Dark.Vertical.TScrollbar",
            )
            tree.configure(yscroll=scrollbar.set)
            scrollbar.grid(row=0, column=1, sticky="ns")

            tree.bind("<<TreeviewSelect>>", self._on_tree_select)
            self.trees[bucket] = tree

        right = ttk.Frame(main, style="Dark.TFrame")
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        art = ttk.LabelFrame(right, text="驻营画像", style="Card.TLabelframe", padding=10)
        art.grid(row=0, column=0, sticky="nsew")
        art.columnconfigure(0, weight=1)

        self.girl_art_label = tk.Label(
            art,
            text="加载中",
            bg=self.colors["field"],
            fg=self.colors["muted"],
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            compound="center",
        )
        self.girl_art_label.grid(row=0, column=0, sticky="nsew")
        art.rowconfigure(0, weight=1)
        self._apply_girl_art_widget()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _toggle_advanced_panel(self) -> None:
        if self._advanced_visible:
            self.advanced_panel.grid_remove()
            self.btn_toggle_advanced.config(text="更多功能 ▸")
            self._advanced_visible = False
            return

        self.advanced_panel.grid()
        self.btn_toggle_advanced.config(text="更多功能 ▾")
        self._advanced_visible = True

    def _on_tree_select(self, event: Any) -> None:
        src = event.widget
        for tree in self.trees.values():
            if tree is src:
                continue
            for item in tree.selection():
                tree.selection_remove(item)

    def _drain_events(self) -> None:
        try:
            while True:
                self.engine.events.get_nowait()
        except queue.Empty:
            return

    def _set_save_root_text(self) -> None:
        current = self.engine.config.save_root.strip()
        display = current if current else "未设置"
        self.save_root_var.set(display)
        self.status_save_root.set(f"游戏存档位置：{display}")

    def _ensure_save_root(self, interactive: bool) -> bool:
        current = self.engine.config.save_root.strip()
        if current and Path(current).exists():
            self._set_save_root_text()
            return True

        detected = detect_save_root()
        if detected is not None:
            self._apply_save_root(detected, source="自动搜索", show_popup=False)
            self.status_info.set(f"自动搜索到存档目录：{detected}")
            return True

        self._set_save_root_text()
        self.status_info.set("未找到游戏存档目录，请手动选择")
        if interactive:
            return self._prompt_choose_save_root()
        return False

    def _prompt_choose_save_root(self) -> bool:
        messagebox.showinfo(
            "需要配置",
            "未自动找到暗黑地牢存档目录。\n请点击“设置”并选择存档目录。",
        )
        self.on_open_settings()
        return False

    def _apply_save_root(self, selected: Path, source: str, show_popup: bool = False) -> bool:
        try:
            if self.engine.running:
                self.engine.stop()
                self.status_info.set("已停止监控，准备切换存档目录")

            root = set_save_root(self.engine.config, selected)
            save_config(self.engine.config, self.config_path)

            if not save_root_looks_valid(root):
                self.status_info.set("已设置目录，但该目录看起来不像有效存档目录")
            else:
                self.status_info.set(f"{source}成功：{root}")

            self.engine.on_save_root_changed()
            self._drain_events()
            self._set_save_root_text()
            self.refresh_profiles()
            self.refresh_snapshots()
            self._refresh_state_labels()

            if show_popup:
                messagebox.showinfo("存档目录已更新", str(root))
            return True
        except Exception as exc:
            messagebox.showerror("设置失败", str(exc))
            return False

    def on_auto_detect_save_root(self) -> None:
        detected = detect_save_root()
        if detected is None:
            messagebox.showwarning("未找到", "自动搜索未找到存档目录，请手动选择。")
            return
        self._apply_save_root(detected, source="自动搜索", show_popup=True)

    def on_choose_save_root(self) -> bool:
        current = self.engine.config.save_root.strip()
        initial_dir = current if current and Path(current).exists() else str(Path.home())
        selected = filedialog.askdirectory(title="选择暗黑地牢存档目录", initialdir=initial_dir)
        if not selected:
            return False
        return self._apply_save_root(Path(selected), source="手动选择", show_popup=False)

    def _create_settings_vars(self) -> None:
        cfg = self.engine.config
        self._settings_vars = {
            "save_root": tk.StringVar(value=cfg.save_root),
            "jar_path": tk.StringVar(value=cfg.jar_path),
            "state_poll_interval_ms": tk.StringVar(value=str(cfg.state_poll_interval_ms)),
            "inraid_state_poll_interval_ms": tk.StringVar(value=str(cfg.inraid_state_poll_interval_ms)),
            "runtime_snapshot_interval_ms": tk.StringVar(value=str(cfg.runtime_snapshot_interval_ms)),
            "retention_per_bucket": tk.StringVar(value=str(cfg.retention_per_bucket)),
            "integrity_retry": tk.StringVar(value=str(cfg.integrity_retry)),
            "quiet_window_ms": tk.StringVar(value=str(cfg.quiet_window_ms)),
        }

    def _close_settings(self) -> None:
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.destroy()
        self._settings_win = None
        self._settings_vars = {}

    def _pick_settings_save_root(self) -> None:
        current = self._settings_vars["save_root"].get().strip()
        initial_dir = current if current and Path(current).exists() else str(Path.home())
        selected = filedialog.askdirectory(
            parent=self._settings_win,
            title="选择暗黑地牢存档目录",
            initialdir=initial_dir,
        )
        if selected:
            self._settings_vars["save_root"].set(selected)

    def _detect_settings_save_root(self) -> None:
        detected = detect_save_root()
        if detected is None:
            messagebox.showwarning("未找到", "自动搜索未找到存档目录，请手动选择。", parent=self._settings_win)
            return
        self._settings_vars["save_root"].set(str(detected))

    def _pick_settings_jar_file(self) -> None:
        current = self._settings_vars["jar_path"].get().strip()
        initial_dir = str(Path(current).parent) if current else str(Path.cwd())
        selected = filedialog.askopenfilename(
            parent=self._settings_win,
            title="选择 DDSaveEditor.jar",
            initialdir=initial_dir,
            filetypes=[("Jar 文件", "*.jar"), ("所有文件", "*.*")],
        )
        if selected:
            self._settings_vars["jar_path"].set(selected)

    def _parse_int_setting(self, key: str, label: str, min_value: int = 1) -> int:
        raw = self._settings_vars[key].get().strip()
        if not raw:
            raise ValueError(f"{label}不能为空")
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{label}必须是整数") from exc
        if value < min_value:
            raise ValueError(f"{label}不能小于 {min_value}")
        return value

    def on_open_settings(self) -> None:
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.focus_set()
            return

        self._create_settings_vars()
        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("设置")
        win.geometry("860x420")
        win.transient(self.root)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._close_settings)

        body = ttk.Frame(win, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(body, text="游戏存档目录").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(body, textvariable=self._settings_vars["save_root"]).grid(
            row=row, column=1, sticky="ew", padx=(8, 6), pady=5
        )
        save_btns = ttk.Frame(body)
        save_btns.grid(row=row, column=2, sticky="e")
        ttk.Button(save_btns, text="自动搜索", command=self._detect_settings_save_root).pack(side=tk.LEFT, padx=2)
        ttk.Button(save_btns, text="选择目录", command=self._pick_settings_save_root).pack(side=tk.LEFT, padx=2)

        row += 1
        ttk.Label(body, text="解码器Jar路径").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(body, textvariable=self._settings_vars["jar_path"]).grid(
            row=row, column=1, sticky="ew", padx=(8, 6), pady=5
        )
        ttk.Button(body, text="选择文件", command=self._pick_settings_jar_file).grid(row=row, column=2, sticky="e")

        form_items = [
            ("state_poll_interval_ms", "状态轮询间隔(ms)"),
            ("inraid_state_poll_interval_ms", "副本中轮询间隔(ms)"),
            ("runtime_snapshot_interval_ms", "运行时快照间隔(ms)"),
            ("retention_per_bucket", "每类快照保留上限"),
            ("integrity_retry", "完整性重试次数"),
            ("quiet_window_ms", "快照静默窗口(ms)"),
        ]
        for key, label in form_items:
            row += 1
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(body, textvariable=self._settings_vars[key]).grid(
                row=row, column=1, sticky="ew", padx=(8, 6), pady=5
            )

        row += 1
        ttk.Label(
            body,
            text="说明：副本中轮询间隔建议大于状态轮询间隔，用于降低副本内性能占用。",
            foreground="gray",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 6))

        actions = ttk.Frame(body)
        actions.grid(row=row + 1, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(actions, text="保存并应用", command=self.on_save_settings).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="关闭", command=self._close_settings).pack(side=tk.LEFT, padx=4)

    def on_save_settings(self) -> None:
        if not self._settings_vars:
            return
        try:
            save_root_raw = self._settings_vars["save_root"].get().strip()
            jar_path = self._settings_vars["jar_path"].get().strip()
            if not save_root_raw:
                raise ValueError("游戏存档目录不能为空")
            if not jar_path:
                raise ValueError("解码器Jar路径不能为空")

            state_poll_interval_ms = self._parse_int_setting("state_poll_interval_ms", "状态轮询间隔(ms)", 50)
            inraid_state_poll_interval_ms = self._parse_int_setting(
                "inraid_state_poll_interval_ms", "副本中轮询间隔(ms)", 1000
            )
            runtime_snapshot_interval_ms = self._parse_int_setting(
                "runtime_snapshot_interval_ms", "运行时快照间隔(ms)", 1000
            )
            retention_per_bucket = self._parse_int_setting("retention_per_bucket", "每类快照保留上限", 1)
            integrity_retry = self._parse_int_setting("integrity_retry", "完整性重试次数", 1)
            quiet_window_ms = self._parse_int_setting("quiet_window_ms", "快照静默窗口(ms)", 0)

            if inraid_state_poll_interval_ms < state_poll_interval_ms:
                raise ValueError("副本中轮询间隔(ms)不能小于状态轮询间隔(ms)")
        except ValueError as exc:
            messagebox.showerror("设置有误", str(exc), parent=self._settings_win)
            return

        try:
            if self.engine.running:
                self.engine.stop()
                self.status_info.set("已停止监控，准备应用设置")

            root = set_save_root(self.engine.config, Path(save_root_raw))
            self.engine.config.jar_path = jar_path
            self.engine.config.state_poll_interval_ms = state_poll_interval_ms
            self.engine.config.inraid_state_poll_interval_ms = inraid_state_poll_interval_ms
            self.engine.config.runtime_snapshot_interval_ms = runtime_snapshot_interval_ms
            self.engine.config.retention_per_bucket = retention_per_bucket
            self.engine.config.integrity_retry = integrity_retry
            self.engine.config.quiet_window_ms = quiet_window_ms

            save_config(self.engine.config, self.config_path)
            self.engine.decoder.jar_path = self.engine.config.jar_file
            self.engine.config.snapshots_root.mkdir(parents=True, exist_ok=True)
            self.engine.config.logs_root.mkdir(parents=True, exist_ok=True)

            if not save_root_looks_valid(root):
                self.status_info.set("设置已保存，但存档目录看起来可能无效")
            else:
                self.status_info.set("设置已保存并应用")

            self.engine.on_save_root_changed()
            self._drain_events()
            self._set_save_root_text()
            self.refresh_profiles()
            self.refresh_snapshots()
            self._refresh_state_labels()
            self._close_settings()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self._settings_win)

    def refresh_profiles(self) -> None:
        save_root = self.engine.config.save_root.strip()
        values: List[str] = []
        if save_root and Path(save_root).exists():
            values = [str(x) for x in discover_profiles(Path(save_root))]
        self._profile_values = values
        self.profile_combo["values"] = values
        current = str(self.engine.config.profile)
        if current in values:
            self.profile_var.set(current)
        elif values:
            self.profile_var.set(values[0])
            self.engine.config.profile = int(values[0])
            self.engine.on_profile_changed()
            save_config(self.engine.config, self.config_path)
        else:
            self.profile_var.set(current)

    def on_profile_change(self, _event: Any = None) -> None:
        selected = self.profile_var.get().strip()
        if not selected:
            return
        try:
            new_profile = int(selected)
        except ValueError:
            return
        if new_profile == self.engine.config.profile:
            return
        if self.engine.running:
            self.engine.stop()
            self.status_info.set("已停止监控，准备切换档位")
        self.engine.config.profile = new_profile
        save_config(self.engine.config, self.config_path)
        self.engine.on_profile_changed()
        self._drain_events()
        self.refresh_snapshots()
        self._refresh_state_labels()

    def _refresh_state_labels(self) -> None:
        running = "是" if self.engine.game_running else "否"
        monitoring = "监控中" if self.engine.running else "已停止"
        inraid = "未知" if self.engine.inraid is None else ("在副本中" if self.engine.inraid else "不在副本中")
        cloud = "未知"
        if self.engine.cloud_enabled is True:
            cloud = "已开启（风险：云同步可能覆盖回档）"
        elif self.engine.cloud_enabled is False:
            cloud = "已关闭"

        self._set_save_root_text()
        self.status_profile.set(f"当前档位：profile_{self.engine.config.profile}")
        self.status_game.set(f"游戏运行中：{running} | 监控状态：{monitoring}")
        self.status_inraid.set(f"副本状态：{inraid}")
        self.status_cloud.set(f"Steam云同步：{cloud}")
        self.status_error.set(f"最近错误：{self.engine.last_error or '无'}")
        self.btn_manual.config(state=("disabled" if self.engine.game_running else "normal"))
        self.btn_start.config(state=("disabled" if self.engine.running else "normal"))
        self.btn_stop.config(state=("normal" if self.engine.running else "disabled"))

    def _poll_events(self) -> None:
        changed = False
        try:
            while True:
                event = self.engine.events.get_nowait()
                et = event.get("type")
                if et in {"snapshot_created", "restore_done", "anchor_set"}:
                    changed = True
                if et == "error":
                    self.status_info.set(f"错误：{event.get('message')}")
                elif et == "info":
                    self.status_info.set(event.get("message", ""))
                elif et == "hotkey":
                    reg = event.get("registered")
                    if reg:
                        self.status_info.set("全局F5热键已注册")
                    else:
                        self.status_info.set("全局F5热键注册失败")
                elif et == "state":
                    self._refresh_state_labels()
        except queue.Empty:
            pass

        if changed:
            self.refresh_snapshots()
        self._refresh_state_labels()
        self.root.after(200, self._poll_events)

    def refresh_snapshots(self) -> None:
        self._item_index.clear()
        for bucket, tree in self.trees.items():
            for child in tree.get_children():
                tree.delete(child)
            snaps = self.manager.list_snapshots(bucket=bucket, include_invalid=False)
            for snap in snaps:
                iid = f"{bucket}:{snap.snapshot_id}"
                self._item_index[iid] = snap
                base_values = {
                    "created": self._format_local_time(snap.created_at),
                    "reason": reason_label(snap.reason),
                    "ok": "正常" if snap.integrity_ok else "损坏",
                }
                columns = self._tree_columns.get(bucket, [])
                tree.insert(
                    "",
                    tk.END,
                    iid=iid,
                    values=tuple(base_values.get(col, "") for col in columns),
                )

    def _selected_snapshot(self) -> Optional[SnapshotInfo]:
        for tree in self.trees.values():
            sel = tree.selection()
            if not sel:
                continue
            return self._item_index.get(sel[0])
        return None

    def _format_local_time(self, value: str) -> str:
        text = value.strip()
        if not text:
            return value
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return text.replace("T", " ")[:19]

    def _run_async(self, fn: Any, done_message: str, on_success: Optional[Any] = None) -> None:
        def worker() -> None:
            try:
                fn()
                self.root.after(0, lambda: self.status_info.set(done_message))
                if on_success is not None:
                    self.root.after(0, on_success)
            except Exception as exc:
                self.logger.error(f"Async action failed: {exc}")
                self.root.after(0, lambda: self.status_info.set(f"错误：{exc}"))
            finally:
                self.root.after(0, self.refresh_snapshots)
                self.root.after(0, self._refresh_state_labels)

        threading.Thread(target=worker, daemon=True).start()

    def on_start(self) -> None:
        if not self._ensure_save_root(interactive=True):
            return
        try:
            self.engine.start(with_hotkey=True)
            self.status_info.set("监控已启动")
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    def on_stop(self) -> None:
        self.engine.stop()
        self.status_info.set("监控已停止")

    def on_manual_save(self) -> None:
        if not self._ensure_save_root(interactive=True):
            return
        if self.engine.game_running:
            messagebox.showwarning("操作被阻止", "游戏仍在运行，请先关闭游戏再进行手动存档。")
            return
        self._run_async(self.engine.trigger_manual_closed_snapshot, "手动存档完成")

    def on_f5_button(self) -> None:
        if not self._ensure_save_root(interactive=True):
            return
        self._run_async(lambda: self.engine.trigger_f5_snapshot(source_reason="hotkey_f5"), "F5存档完成")

    def on_restore(self) -> None:
        if not self._ensure_save_root(interactive=True):
            return
        snap = self._selected_snapshot()
        if not snap:
            messagebox.showwarning("未选择存档", "请先选择一个存档再回档。")
            return
        if self.engine.game_running:
            messagebox.showwarning("操作被阻止", "游戏仍在运行，请先关闭游戏再回档。")
            return
        warn_cloud = ""
        if self.engine.cloud_enabled is True:
            warn_cloud = "\n\n警告：Steam 云同步已开启，可能会覆盖回档结果。"
        ok = messagebox.askyesno(
            "确认回档",
            (
                f"确定要回档吗？\n"
                f"分类：{bucket_label(snap.bucket)}\n"
                f"ID：{snap.snapshot_id}\n"
                f"原因：{reason_label(snap.reason)}\n\n"
                "回档前会先自动备份当前档（回档前自动备份）。"
                f"{warn_cloud}"
            ),
        )
        if not ok:
            return
        self._run_async(
            lambda: self.engine.restore(snap),
            "回档完成",
            on_success=lambda: messagebox.showinfo("回档完成", "回档成功，已自动备份回档前存档。"),
        )

    def on_close(self) -> None:
        try:
            self.engine.stop()
        finally:
            self.root.destroy()


def _enable_high_dpi() -> None:
    if os.environ.get("DD_GUI_SAFE_MODE") == "1":
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        return


def run_gui(engine: MonitorEngine, manager: SnapshotManager, logger: ActionLogger, config_path: Path) -> int:
    _enable_high_dpi()
    root = tk.Tk()
    try:
        dpi = float(root.winfo_fpixels("1i"))
        if dpi > 0:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
    GUIApp(root, engine, manager, logger, config_path)
    root.mainloop()
    return 0
