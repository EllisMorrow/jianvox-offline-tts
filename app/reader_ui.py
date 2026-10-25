"""View construction only. Task state and actions remain in reader_app.App."""
import ctypes as C
from ctypes import wintypes as W
import customtkinter as ctk
from hotkeys import ACTION_NAMES
from reader_core import VOICES
from version import VERSION
from ui_tooltip import Tooltip

BG, PANEL = ("#F7F8FB", "#171923"), ("#FFFFFF", "#222531")
INK, MUTED = ("#202338", "#ECECF4"), ("#646B80", "#AFB5C8")
ACCENT, HOVER = ("#6256D9", "#A69BFF"), ("#5145C6", "#B9B0FF")
BORDER = ("#E5E7EF", "#383D50")
SIDEBAR, SELECTED = ("#EFF1F7", "#1D202C"), ("#E4E1F7", "#373150")
FIELD, CONTROL = ("#FAFBFD", "#1B1E29"), ("#F1F2F7", "#2E3343")
CONTROL_BUTTON, CONTROL_HOVER = ("#E8EAF3", "#3A4053"), ("#DDE0EB", "#484F65")
QUIET_HOVER, SECONDARY_HOVER = ("#E9EBF3", "#303547"), ("#F1F2F8", "#303547")
DISABLED, TRACK = ("#949AAD", "#7D859B"), ("#DADDE8", "#41475B")
SCROLLBAR, SCROLLBAR_HOVER = ("#CFD3DF", "#4C5369"), ("#B9BFCE", "#626C87")
PRIMARY_FILL, PRIMARY_HOVER = ("#6256D9", "#7160D2"), ("#5145C6", "#7865D6")
SUCCESS = ("#26775E", "#7FD5B1")


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
        self.sidebar_collapsed = False
        self._header_mode = None
        self._toolbar_mode = None

    def font(self, size=13, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = ctk.CTkFont(family="Microsoft YaHei UI", size=size, weight="bold" if bold else "normal")
        return self.fonts[key]

    def label(self, parent, text=None, size=13, bold=False, color=INK, **kwargs):
        return ctk.CTkLabel(parent, text=text, height=kwargs.pop("height", size+8),
                            font=self.font(size, bold), text_color=color, anchor="w", **kwargs)

    def button(self, parent, text, command, role="secondary", **kwargs):
        colors = {"primary": (PRIMARY_FILL, PRIMARY_HOVER, ("#FFFFFF", "#FFFFFF"), 0),
                  "secondary": (PANEL, SECONDARY_HOVER, INK, 1),
                  "quiet": ("transparent", QUIET_HOVER, MUTED, 0)}
        fill, hover, ink, border = colors[role]
        return ctk.CTkButton(parent, text=text, command=command, height=kwargs.pop("height", 38),
                            corner_radius=7, font=self.font(), fg_color=fill, hover_color=hover,
                            text_color=ink, text_color_disabled=DISABLED,
                            border_width=border, border_color=BORDER, **kwargs)

    def card(self, parent, **kwargs):
        return ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10, border_width=1, border_color=BORDER, **kwargs)

    def build(self):
        app, root = self.app, self.app.root
        root.configure(fg_color=BG)
        width, height, minimum_w, minimum_h = window_dimensions(root)
        root.geometry(f"{width}x{height}")
        root.minsize(minimum_w, minimum_h)
        root.grid_columnconfigure(2, weight=1)
        root.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(root, width=128, fg_color=SIDEBAR, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(4, weight=1)
        self.brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.brand.grid(row=0, column=0, padx=16, pady=(18, 12), sticky="ew")
        self.brand_title = self.label(self.brand, "聆笺", 21, True)
        self.brand_title.pack(anchor="w")
        self.brand_name = self.label(self.brand, "JianVox", 12, color=MUTED)
        self.brand_name.pack(anchor="w", pady=(2, 0))
        for row, name in enumerate(["朗读", "快捷键", "设置"], 1):
            button = self.button(self.sidebar, name, lambda n=name: self.show_page(n), role="quiet", anchor="w", width=108, height=38)
            button.grid(row=row, column=0, sticky="ew", padx=10, pady=2)
            self.nav[name] = button
        # Keep the handle outside the navigation so hiding the entire sidebar
        # returns its full width to the editor and cannot overlap a menu item.
        self.sidebar_handle = ctk.CTkFrame(root, width=20, fg_color=BG, corner_radius=0)
        self.sidebar_handle.grid(row=0, column=1, sticky="nsew")
        self.sidebar_handle.grid_propagate(False)
        self.sidebar_toggle = self.button(self.sidebar_handle, "‹", self.toggle_sidebar, role="quiet", width=20, height=48)
        self.sidebar_toggle.place(relx=.5, rely=.5, anchor="center")
        self.sidebar_tooltip = Tooltip(self.sidebar_toggle, "收起或展开左侧导航。")
        app.hide_button = self.button(self.sidebar, "隐藏到托盘", app.hide, role="quiet", width=108, height=34)
        app.hide_button.grid(row=6, column=0, padx=10, pady=3, sticky="ew")
        app.exit_button = self.button(self.sidebar, "退出程序", app.exit, role="quiet", width=108, height=34)
        app.exit_button.grid(row=7, column=0, padx=10, pady=(3, 16), sticky="ew")

        self.workspace = ctk.CTkFrame(root, fg_color="transparent", corner_radius=0)
        self.workspace.grid(row=0, column=2, sticky="nsew", padx=(12, 24), pady=(18, 12))
        self.workspace.grid_columnconfigure(0, weight=1)
        self.workspace.grid_rowconfigure(1, weight=1)
        self.header = ctk.CTkFrame(self.workspace, fg_color="transparent")
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.header.grid_columnconfigure(0, weight=1)
        self.title = self.label(self.header, "朗读", 22, True)
        self.title.grid(row=0, column=0, sticky="nw")
        self.hint_label = self.label(self.header, textvariable=app.hint, size=11, color=MUTED)
        self.hint_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.build_voice_controls(self.header)
        self.header.bind("<Configure>", self.layout_header)
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
        app.export_progress = ctk.CTkProgressBar(self.status_area, height=4, fg_color=TRACK, progress_color=ACCENT)
        app.export_progress.set(0)
        app.dot = self.label(self.status_area, "●", 12, color=ACCENT)
        app.dot.grid(row=1, column=0, sticky="nw", padx=(0, 8))
        self.status_label = self.label(self.status_area, textvariable=app.status, size=11, color=MUTED, wraplength=560)
        self.status_label.grid(row=1, column=1, sticky="ew")
        self.offline_label = self.label(self.status_area, f"● 完全离线  ·  v{VERSION}", 11, color=SUCCESS)
        self.offline_label.grid(row=1, column=2, sticky="ne", padx=(12, 0))
        self.status_area.bind("<Configure>", self.layout_status)
        self.show_page("朗读")

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        collapsed = self.sidebar_collapsed
        if collapsed:
            self.sidebar.grid_remove()
        else:
            self.sidebar.grid()
        self.sidebar_toggle.configure(text="›" if collapsed else "‹")

    def layout_status(self, _event=None):
        available = self.status_area._reverse_widget_scaling(self.status_area.winfo_width())
        offline_width = self.offline_label._reverse_widget_scaling(self.offline_label.winfo_reqwidth())
        self.status_label.configure(wraplength=max(100, available-offline_width-34))

    def layout_header(self, _event=None):
        width = self.header._reverse_widget_scaling(self.header.winfo_width())
        self.hint_label.configure(wraplength=max(100, width))
        if self.current_page != "朗读":
            self.voice_controls.grid_remove()
            self._header_mode = None
            return
        mode = "wide" if width >= 640 else "stacked"
        if mode == self._header_mode:
            return
        self._header_mode = mode
        self.voice_controls.grid(row=0 if mode == "wide" else 1, column=1 if mode == "wide" else 0,
                                 columnspan=1 if mode == "wide" else 2, sticky="e" if mode == "wide" else "ew",
                                 padx=(12, 0) if mode == "wide" else 0, pady=0 if mode == "wide" else (6, 0))

    def show_page(self, name):
        for label, page in self.pages.items():
            page.grid_forget()
            self.nav[label].configure(fg_color=SELECTED if label == name else "transparent",
                                      text_color=ACCENT if label == name else MUTED)
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        self.current_page = name
        self.title.configure(text={"朗读":"文字朗读", "快捷键":"键盘快捷键", "设置":"偏好设置"}[name])
        self.layout_header()

    def show_export_progress(self):
        self.app.export_progress.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

    def hide_export_progress(self):
        self.app.export_progress.grid_remove()

    def build_reader(self, parent):
        app = self.app
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        editor = self.card(parent)
        editor.grid(row=0, column=0, sticky="nsew")
        self.editor = editor
        editor.grid_columnconfigure(0, weight=1)
        editor.grid_rowconfigure(1, weight=1)
        tools = ctk.CTkFrame(editor, fg_color="transparent")
        tools.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 4))
        self.label(tools, "朗读文字", bold=True).pack(side="left")
        app.clear_button = self.button(tools, "清空", app.clear_text, role="quiet", width=50, height=28)
        app.clear_button.pack(side="right")
        app.counter = self.label(tools, "0 字", 11, color=MUTED)
        app.counter.pack(side="right", padx=(8, 4))
        app.text = ctk.CTkTextbox(editor, fg_color=PANEL, text_color=INK, corner_radius=0, border_width=0,
                                 wrap="word", undo=True, font=self.font(16), spacing1=3, spacing3=5,
                                 scrollbar_button_color=SCROLLBAR, scrollbar_button_hover_color=SCROLLBAR_HOVER)
        app.text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self.label(editor, "在此粘贴文字，或使用选字朗读快捷键。", 11, color=MUTED).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 6))
        def changed(_):
            if app.text.edit_modified():
                app.update_counter()
                app.text.edit_modified(False)
        app.text.bind("<<Modified>>", changed)
        app.update_counter()

        app.action_bar = ctk.CTkFrame(parent, fg_color="transparent")
        app.action_bar.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        app.action_bar.grid_columnconfigure(0, weight=1)
        self.playback_actions = ctk.CTkFrame(app.action_bar, fg_color="transparent")
        self.export_actions = ctk.CTkFrame(app.action_bar, fg_color="transparent")
        app.read_button = self.button(self.playback_actions, "朗读文字", lambda: app.read(app.text.get("1.0", "end")), "primary", width=106)
        app.read_button.pack(side="left", padx=(0, 8))
        app.pause_button = self.button(self.playback_actions, "暂停", app.pause, width=56)
        app.pause_button.pack(side="left", padx=(0, 8))
        app.stop_button = self.button(self.playback_actions, "停止", app.stop, width=56)
        app.stop_button.pack(side="left", padx=(0, 8))
        app.clipboard_button = self.button(self.playback_actions, "朗读剪贴板", app.read_clipboard, width=106)
        app.clipboard_button.pack(side="left")
        app.export_button = self.button(self.export_actions, "导出 MP3", app.export_mp3, width=100)
        app.export_button.pack(side="left", padx=(0, 8))
        app.estimate_button = self.button(self.export_actions, "导出MP3时间预估", app.estimate_export_time, width=150)
        app.estimate_button.pack(side="left")
        self.export_tooltip = Tooltip(app.export_button,
            "普通朗读最多 20000 字。\nMP3 导出最多 100 万字，自动分段生成。\n长任务耗时较长，可先预估导出时间；点击停止可取消。")
        app.action_bar.bind("<Configure>", self.layout_toolbar)
        self.layout_toolbar()

    def layout_toolbar(self, _event=None):
        width = self.app.action_bar._reverse_widget_scaling(self.app.action_bar.winfo_width())
        mode = "wide" if width >= 660 else "stacked"
        if mode == self._toolbar_mode:
            return
        self._toolbar_mode = mode
        self.playback_actions.grid(row=0, column=0, sticky="w")
        self.export_actions.grid(row=0 if mode == "wide" else 1, column=1 if mode == "wide" else 0,
                                 sticky="e" if mode == "wide" else "w",
                                 padx=(12, 0) if mode == "wide" else 0, pady=0 if mode == "wide" else (8, 0))

    def build_voice_controls(self, parent):
        app = self.app
        self.voice_controls = ctk.CTkFrame(parent, fg_color="transparent")
        self.voice_controls.grid_columnconfigure(0, weight=1)
        voice = ctk.CTkFrame(self.voice_controls, fg_color="transparent")
        voice.grid(row=0, column=0, sticky="ew", padx=(0, 16))
        self.label(voice, "声音", 11, color=MUTED, height=17).pack(anchor="w", pady=(0, 3))
        app.voice_menu = ctk.CTkOptionMenu(voice, variable=app.voice, values=list(VOICES), width=184, height=30,
            fg_color=CONTROL, button_color=CONTROL_BUTTON, button_hover_color=CONTROL_HOVER, text_color=INK,
            text_color_disabled=DISABLED, dropdown_fg_color=PANEL, dropdown_text_color=INK, dropdown_hover_color=SELECTED,
            font=self.font(11), dropdown_font=self.font(12), command=lambda _: app.defer_save())
        app.voice_menu.pack(fill="x")
        for column, (title, variable, value, minimum, maximum, steps, attribute) in enumerate([
            ("语速", app.speed, app.speed_text, .75, 1.5, 15, "speed_slider"),
            ("音量", app.volume, app.volume_text, 0, 1, 100, "volume_slider")], 1):
            group = ctk.CTkFrame(self.voice_controls, fg_color="transparent", width=140 if column == 1 else 124)
            group.grid(row=0, column=column, sticky="ew", padx=(0, 16) if column == 1 else 0)
            row = ctk.CTkFrame(group, fg_color="transparent")
            row.pack(fill="x", pady=(0, 6))
            self.label(row, title, 11, color=MUTED).pack(side="left")
            if column == 1:
                self.reset_speed_button = self.button(row, "复位", app.reset_speed, role="quiet", height=20, width=32)
                self.reset_speed_button.pack(side="right", padx=(4, 0))
                self.speed_tooltip = Tooltip(self.reset_speed_button, "恢复标准语速（1.00×）。")
            self.label(row, textvariable=value, size=11, bold=True, color=ACCENT).pack(side="right")
            slider = ctk.CTkSlider(group, width=140 if column == 1 else 124, height=20, from_=minimum, to=maximum, number_of_steps=steps, variable=variable,
                fg_color=TRACK, progress_color=ACCENT, button_color=ACCENT, button_hover_color=HOVER,
                command=lambda _: app.playback_changed())
            slider.pack(fill="x", pady=(1, 3))
            setattr(app, attribute, slider)
        self.voice_tooltip = Tooltip(app.voice_menu, "声音与语速在下次朗读生效；音量可即时调整。")

    def build_hotkeys(self, parent):
        app = self.app
        card = self.card(parent)
        card.grid(row=0, column=0, sticky="new")
        card.grid_columnconfigure(1, weight=1)
        self.label(card, "为常用操作指定快捷键", 14, True).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(14, 6))
        self.label(card, "例如 F4、Ctrl+Alt+R；留空即可禁用。", 12, color=MUTED).grid(row=1, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 12))
        app.key_entries = {}
        for row, (action, title) in enumerate(ACTION_NAMES.items(), 2):
            self.label(card, title, width=115).grid(row=row, column=0, sticky="w", padx=(20, 14), pady=5)
            entry = ctk.CTkEntry(card, height=36, font=self.font(), fg_color=FIELD, border_color=BORDER,
                                 text_color=INK, placeholder_text_color=DISABLED)
            entry.insert(0, app.preferences.hotkeys[action])
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            self.button(card, "不设置", lambda e=entry: e.delete(0, "end"), "quiet", width=76, height=34).grid(row=row, column=2, padx=(10, 16), pady=5)
            app.key_entries[action] = entry
        app.key_status = self.label(card, "", 11, color=MUTED, wraplength=480)
        app.key_status.grid(row=5, column=0, columnspan=3, sticky="w", padx=20, pady=(5, 5))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", padx=20, pady=(4, 14))
        app.key_save = self.button(actions, "保存快捷键", app.apply_hotkeys, "primary", width=124)
        app.key_save.pack(side="left")
        self.button(actions, "填入默认值", app.default_keys, "quiet", width=124).pack(side="left", padx=10)
        self.label(parent, "若快捷键被其他程序占用，会提示并恢复原绑定。", 11, color=MUTED).grid(row=1, column=0, sticky="nw", pady=14)

    def build_settings(self, parent):
        app = self.app
        parent.grid_rowconfigure(0, weight=1)
        panel = ctk.CTkScrollableFrame(parent, fg_color="transparent", corner_radius=0,
                                      scrollbar_button_color=SCROLLBAR, scrollbar_button_hover_color=SCROLLBAR_HOVER)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        self.settings_panel = panel
        appearance = self.card(panel)
        appearance.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        appearance.grid_columnconfigure(0, weight=1)
        self.label(appearance, "外观主题", 14, True).grid(row=0, column=0, sticky="w", padx=18, pady=(15, 3))
        self.label(appearance, "跟随系统会自动匹配 Windows 外观。", 11, color=MUTED).grid(
            row=1, column=0, sticky="w", padx=18, pady=(0, 15))
        app.theme_menu = ctk.CTkOptionMenu(appearance, variable=app.theme,
            values=["白天", "夜晚", "跟随系统"], command=app.change_theme, width=132, height=32,
            fg_color=CONTROL, button_color=CONTROL_BUTTON, button_hover_color=CONTROL_HOVER,
            text_color=INK, text_color_disabled=DISABLED, dropdown_fg_color=PANEL,
            dropdown_text_color=INK, dropdown_hover_color=SELECTED,
            font=self.font(12), dropdown_font=self.font(12))
        app.theme_menu.grid(row=0, column=1, rowspan=2, sticky="e", padx=18, pady=14)
        style = self.card(panel)
        style.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self.label(style, "朗读语气", 14, True).pack(anchor="w", padx=18, pady=(16, 5))
        self.label(style, "描述节奏与情绪，保存后在下次朗读生效。", 11, color=MUTED).pack(anchor="w", padx=18, pady=(0, 10))
        app.style_box = ctk.CTkTextbox(style, height=78, fg_color=FIELD, text_color=INK, border_width=1,
                                      border_color=BORDER, font=self.font(), wrap="word", corner_radius=7,
                                      scrollbar_button_color=SCROLLBAR, scrollbar_button_hover_color=SCROLLBAR_HOVER)
        app.style_box.pack(fill="x", padx=18)
        app.style_box.insert("1.0", app.preferences.style)
        self.button(style, "保存语气", app.save_style, width=104, height=34).pack(anchor="w", padx=18, pady=13)
        general = self.card(panel)
        general.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self.label(general, "保存与启动", 14, True).pack(anchor="w", padx=18, pady=(16, 12))
        self.label(general, "MP3 默认保存目录", 12).pack(anchor="w", padx=18, pady=(0, 6))
        row = ctk.CTkFrame(general, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkEntry(row, textvariable=app.export_directory, state="readonly", height=36, font=self.font(12),
                     fg_color=FIELD, border_color=BORDER, text_color=INK,
                     placeholder_text_color=DISABLED).pack(side="left", fill="x", expand=True)
        self.button(row, "选择目录", app.choose_directory, width=100, height=36).pack(side="right", padx=(10, 0))
        ctk.CTkSwitch(general, text="开机启动后进入托盘", variable=app.startup_enabled,
                      command=app.change_startup, fg_color=TRACK, progress_color=PRIMARY_FILL,
                      button_color=("#FFFFFF", "#ECECF4"), button_hover_color=("#F5F5FB", "#FFFFFF"),
                      text_color=INK, text_color_disabled=DISABLED, font=self.font()).pack(anchor="w", padx=18, pady=(0, 17))
        privacy = self.card(panel)
        privacy.grid(row=3, column=0, sticky="ew", pady=(0, 5))
        self.label(privacy, "隐私与设置文件", 14, True).pack(anchor="w", padx=18, pady=(16, 8))
        self.label(privacy, "仅保存必要偏好，不保留正文、朗读历史或使用统计。\nWindows 剪贴板历史及其他软件记录不在清理范围内。",
                   11, color=MUTED, justify="left", wraplength=520).pack(anchor="w", padx=18, pady=(0, 12))
        self.button(privacy, "打开设置文件夹", app.open_settings_folder, width=150, height=34).pack(anchor="w", padx=18, pady=(0, 16))
