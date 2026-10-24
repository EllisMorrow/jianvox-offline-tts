"""View construction only. Task state and actions remain in reader_app.App."""
import ctypes as C
from ctypes import wintypes as W
import customtkinter as ctk
from hotkeys import ACTION_NAMES
from reader_core import VOICES
from version import VERSION

BG, PANEL, INK, MUTED = "#F7F8FB", "#FFFFFF", "#202338", "#646B80"
ACCENT, HOVER, BORDER = "#6256D9", "#5145C6", "#E5E7EF"


def window_dimensions(root):
    """Fit logical dimensions to the current monitor's usable area, including DPI."""
    class MonitorInfo(C.Structure):
        _fields_ = [("cbSize", W.DWORD), ("monitor", W.RECT), ("work", W.RECT), ("flags", W.DWORD)]
    user = C.WinDLL("user32")
    user.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
    user.MonitorFromWindow.restype = W.HANDLE
    user.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
    user.GetMonitorInfoW.restype = W.BOOL
    user.GetDpiForWindow.argtypes = [W.HWND]
    user.GetDpiForWindow.restype = W.UINT
    hwnd = root.winfo_id()
    info = MonitorInfo(cbSize=C.sizeof(MonitorInfo))
    monitor = user.MonitorFromWindow(hwnd, 2)
    if not user.GetMonitorInfoW(monitor, C.byref(info)):
        raise C.WinError()
    scale = (user.GetDpiForWindow(hwnd) or 96) / 96
    available_w = int((info.work.right - info.work.left - 64) / scale)
    available_h = int((info.work.bottom - info.work.top - 80) / scale)
    return min(1040, available_w), min(700, available_h), min(800, available_w), min(480, available_h)


class ReaderUI:
    def __init__(self, app):
        self.app = app
        self.fonts = {}
        self.pages = {}
        self.nav = {}
        self.current_page = "朗读"

    def font(self, size=13, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = ctk.CTkFont(family="Microsoft YaHei UI", size=size, weight="bold" if bold else "normal")
        return self.fonts[key]

    def label(self, parent, text=None, size=13, bold=False, color=INK, **kwargs):
        return ctk.CTkLabel(parent, text=text, height=kwargs.pop("height", size+8),
                            font=self.font(size, bold), text_color=color, anchor="w", **kwargs)

    def button(self, parent, text, command, role="secondary", **kwargs):
        colors = {"primary": (ACCENT, HOVER, "white", 0),
                  "secondary": (PANEL, "#F1F2F8", INK, 1),
                  "quiet": ("transparent", "#E9EBF3", MUTED, 0)}
        fill, hover, ink, border = colors[role]
        return ctk.CTkButton(parent, text=text, command=command, height=kwargs.pop("height", 38),
                            corner_radius=7, font=self.font(), fg_color=fill, hover_color=hover,
                            text_color=ink, border_width=border, border_color=BORDER, **kwargs)

    def card(self, parent, **kwargs):
        return ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10, border_width=1, border_color=BORDER, **kwargs)

    def build(self):
        app, root = self.app, self.app.root
        root.configure(fg_color=BG)
        width, height, minimum_w, minimum_h = window_dimensions(root)
        root.geometry(f"{width}x{height}")
        root.minsize(minimum_w, minimum_h)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(root, width=152, fg_color="#EFF1F7", corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(4, weight=1)
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=20, pady=(24, 23), sticky="ew")
        self.label(brand, "聆笺", 21, True).pack(anchor="w")
        self.label(brand, "JianVox", 12, color=MUTED).pack(anchor="w", pady=(2, 0))
        for row, name in enumerate(["朗读", "快捷键", "设置"], 1):
            button = self.button(self.sidebar, name, lambda n=name: self.show_page(n), role="quiet", anchor="w", width=120, height=40)
            button.grid(row=row, column=0, sticky="ew", padx=12, pady=3)
            self.nav[name] = button
        self.label(self.sidebar, f"●  完全离线\nv{VERSION}", 11, color="#26775E", justify="left").grid(row=5, column=0, padx=20, pady=(12, 12), sticky="w")
        app.hide_button = self.button(self.sidebar, "隐藏到托盘", app.hide, role="quiet", width=120, height=34)
        app.hide_button.grid(row=6, column=0, padx=12, pady=3, sticky="ew")
        app.exit_button = self.button(self.sidebar, "退出程序", app.exit, role="quiet", width=120, height=34)
        app.exit_button.grid(row=7, column=0, padx=12, pady=(3, 16), sticky="ew")

        self.workspace = ctk.CTkFrame(root, fg_color="transparent", corner_radius=0)
        self.workspace.grid(row=0, column=1, sticky="nsew", padx=24, pady=(22, 14))
        self.workspace.grid_columnconfigure(0, weight=1)
        self.workspace.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self.workspace, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self.title = self.label(header, "朗读", 22, True)
        self.title.pack(anchor="w")
        self.label(header, textvariable=app.hint, size=11, color=MUTED).pack(anchor="w", pady=(5, 0))
        self.page_host = ctk.CTkFrame(self.workspace, fg_color="transparent")
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)
        for name in self.nav:
            page = ctk.CTkFrame(self.page_host, fg_color="transparent")
            page.grid_columnconfigure(0, weight=1)
            self.pages[name] = page
        self.build_reader(self.pages["朗读"])
        self.build_hotkeys(self.pages["快捷键"])
        self.build_settings(self.pages["设置"])

        self.status_area = ctk.CTkFrame(self.workspace, fg_color="transparent")
        self.status_area.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.status_area.grid_columnconfigure(1, weight=1)
        app.export_progress = ctk.CTkProgressBar(self.status_area, height=4, progress_color=ACCENT)
        app.export_progress.set(0)
        app.dot = self.label(self.status_area, "●", 12, color=ACCENT)
        app.dot.grid(row=1, column=0, sticky="nw", padx=(0, 8))
        self.status_label = self.label(self.status_area, textvariable=app.status, size=11, color=MUTED, wraplength=560)
        self.status_label.grid(row=1, column=1, sticky="ew")
        self.status_area.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(240, e.width-30)))
        self.show_page("朗读")

    def show_page(self, name):
        for label, page in self.pages.items():
            page.grid_forget()
            self.nav[label].configure(fg_color="#E4E1F7" if label == name else "transparent",
                                      text_color=ACCENT if label == name else MUTED)
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        self.current_page = name
        self.title.configure(text={"朗读":"文字朗读", "快捷键":"键盘快捷键", "设置":"偏好设置"}[name])

    def show_export_progress(self):
        self.app.export_progress.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    def hide_export_progress(self):
        self.app.export_progress.grid_remove()

    def build_reader(self, parent):
        app = self.app
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=0, minsize=224)
        parent.grid_rowconfigure(0, weight=1)
        editor = self.card(parent)
        editor.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        editor.grid_columnconfigure(0, weight=1)
        editor.grid_rowconfigure(1, weight=1)
        tools = ctk.CTkFrame(editor, fg_color="transparent")
        tools.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        self.label(tools, "朗读文字", bold=True).pack(side="left")
        app.clear_button = self.button(tools, "清空", app.clear_text, role="quiet", width=50, height=28)
        app.clear_button.pack(side="right")
        app.counter = self.label(tools, "0 字", 11, color=MUTED)
        app.counter.pack(side="right", padx=(8, 4))
        app.text = ctk.CTkTextbox(editor, fg_color=PANEL, text_color=INK, corner_radius=0, border_width=0,
                                 wrap="word", undo=True, font=self.font(16), spacing1=3, spacing3=5)
        app.text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self.label(editor, "在此粘贴文字，或使用选字朗读快捷键。", 11, color=MUTED).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 13))
        def changed(_):
            if app.text.edit_modified():
                app.update_counter()
                app.text.edit_modified(False)
        app.text.bind("<<Modified>>", changed)
        app.update_counter()

        controls_shell = self.card(parent, width=224)
        controls_shell.grid(row=0, column=1, sticky="nsew")
        controls = ctk.CTkScrollableFrame(controls_shell, width=176, fg_color="transparent", corner_radius=0,
                                         scrollbar_button_color="#CFD3DF", scrollbar_button_hover_color="#B9BFCE")
        controls.pack(fill="both", expand=True, padx=1, pady=1)
        self.label(controls, "声音", bold=True).pack(anchor="w", padx=16, pady=(16, 8))
        app.voice_menu = ctk.CTkOptionMenu(controls, variable=app.voice, values=list(VOICES), width=192, height=36,
            fg_color="#F1F2F7", button_color="#E8EAF3", button_hover_color="#DDE0EB", text_color=INK,
            dropdown_fg_color=PANEL, dropdown_text_color=INK, dropdown_hover_color="#EEECFA",
            font=self.font(11), dropdown_font=self.font(12), command=lambda _: app.defer_save())
        app.voice_menu.pack(fill="x", padx=16)
        for title, variable, value, minimum, maximum, steps, attribute in [
            ("语速", app.speed, app.speed_text, .75, 1.5, 15, "speed_slider"),
            ("音量", app.volume, app.volume_text, 0, 1, 100, "volume_slider")]:
            row = ctk.CTkFrame(controls, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(23, 6))
            self.label(row, title, 12).pack(side="left")
            self.label(row, textvariable=value, size=12, bold=True, color=ACCENT).pack(side="right")
            slider = ctk.CTkSlider(controls, from_=minimum, to=maximum, number_of_steps=steps, variable=variable,
                progress_color=ACCENT, button_color=ACCENT, button_hover_color=HOVER, command=lambda _: app.playback_changed())
            slider.pack(fill="x", padx=16)
            setattr(app, attribute, slider)
        self.button(controls, "恢复标准语速", app.reset_speed, role="quiet", height=30, width=150).pack(fill="x", padx=16, pady=(17, 4))
        self.label(controls, "声音与语速在下次朗读生效。\n音量可即时调整。", 11, color=MUTED, justify="left").pack(anchor="w", padx=16, pady=(0, 15))

        app.action_bar = ctk.CTkFrame(parent, fg_color="transparent")
        app.action_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        app.action_bar.grid_columnconfigure(3, weight=1)
        app.read_button = self.button(app.action_bar, "朗读文字", lambda: app.read(app.text.get("1.0", "end")), "primary", width=116)
        app.read_button.grid(row=0, column=0, padx=(0, 8))
        app.pause_button = self.button(app.action_bar, "暂停", app.pause, width=64)
        app.pause_button.grid(row=0, column=1, padx=(0, 8))
        app.stop_button = self.button(app.action_bar, "停止", app.stop, width=64)
        app.stop_button.grid(row=0, column=2)
        app.clipboard_button = self.button(app.action_bar, "朗读剪贴板", app.read_clipboard, width=112)
        app.clipboard_button.grid(row=0, column=4, padx=(8, 8))
        app.export_button = self.button(app.action_bar, "导出 MP3", app.export_mp3, width=104)
        app.export_button.grid(row=0, column=5)

    def build_hotkeys(self, parent):
        app = self.app
        card = self.card(parent)
        card.grid(row=0, column=0, sticky="new")
        card.grid_columnconfigure(1, weight=1)
        self.label(card, "为常用操作指定快捷键", 14, True).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(19, 6))
        self.label(card, "例如 F4、Ctrl+Alt+R；留空即可禁用。", 12, color=MUTED).grid(row=1, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 18))
        app.key_entries = {}
        for row, (action, title) in enumerate(ACTION_NAMES.items(), 2):
            self.label(card, title, width=115).grid(row=row, column=0, sticky="w", padx=(20, 14), pady=7)
            entry = ctk.CTkEntry(card, height=36, font=self.font(), fg_color="#FAFBFD", border_color=BORDER, text_color=INK)
            entry.insert(0, app.preferences.hotkeys[action])
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            self.button(card, "不设置", lambda e=entry: e.delete(0, "end"), "quiet", width=76, height=34).grid(row=row, column=2, padx=(10, 16), pady=7)
            app.key_entries[action] = entry
        app.key_status = self.label(card, "", 11, color=MUTED, wraplength=480)
        app.key_status.grid(row=5, column=0, columnspan=3, sticky="w", padx=20, pady=(10, 5))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", padx=20, pady=(4, 20))
        app.key_save = self.button(actions, "保存快捷键", app.apply_hotkeys, "primary", width=124)
        app.key_save.pack(side="left")
        self.button(actions, "填入默认值", app.default_keys, "quiet", width=124).pack(side="left", padx=10)
        self.label(parent, "若快捷键被其他程序占用，会提示并恢复原绑定。", 11, color=MUTED).grid(row=1, column=0, sticky="nw", pady=14)

    def build_settings(self, parent):
        app = self.app
        parent.grid_rowconfigure(0, weight=1)
        panel = ctk.CTkScrollableFrame(parent, fg_color="transparent", corner_radius=0,
                                      scrollbar_button_color="#CFD3DF", scrollbar_button_hover_color="#B9BFCE")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        style = self.card(panel)
        style.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.label(style, "朗读语气", 14, True).pack(anchor="w", padx=18, pady=(16, 5))
        self.label(style, "描述节奏与情绪，保存后在下次朗读生效。", 11, color=MUTED).pack(anchor="w", padx=18, pady=(0, 10))
        app.style_box = ctk.CTkTextbox(style, height=78, fg_color="#FAFBFD", text_color=INK, border_width=1,
                                      border_color=BORDER, font=self.font(), wrap="word", corner_radius=7)
        app.style_box.pack(fill="x", padx=18)
        app.style_box.insert("1.0", app.preferences.style)
        self.button(style, "保存语气", app.save_style, width=104, height=34).pack(anchor="w", padx=18, pady=13)
        general = self.card(panel)
        general.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self.label(general, "保存与启动", 14, True).pack(anchor="w", padx=18, pady=(16, 12))
        self.label(general, "MP3 默认保存目录", 12).pack(anchor="w", padx=18, pady=(0, 6))
        row = ctk.CTkFrame(general, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkEntry(row, textvariable=app.export_directory, state="readonly", height=36, font=self.font(12),
                     fg_color="#FAFBFD", border_color=BORDER, text_color=INK).pack(side="left", fill="x", expand=True)
        self.button(row, "选择目录", app.choose_directory, width=100, height=36).pack(side="right", padx=(10, 0))
        ctk.CTkSwitch(general, text="开机启动后进入托盘", variable=app.startup_enabled,
                      command=app.change_startup, progress_color=ACCENT, font=self.font()).pack(anchor="w", padx=18, pady=(0, 17))
        privacy = self.card(panel)
        privacy.grid(row=2, column=0, sticky="ew", pady=(0, 5))
        self.label(privacy, "隐私与设置文件", 14, True).pack(anchor="w", padx=18, pady=(16, 8))
        self.label(privacy, "仅保存必要偏好，不保留正文、朗读历史或使用统计。\nWindows 剪贴板历史及其他软件记录不在清理范围内。",
                   11, color=MUTED, justify="left", wraplength=520).pack(anchor="w", padx=18, pady=(0, 12))
        self.button(privacy, "打开设置文件夹", app.open_settings_folder, width=150, height=34).pack(anchor="w", padx=18, pady=(0, 16))
