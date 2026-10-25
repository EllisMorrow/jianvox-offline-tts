"""Focused Windows desktop reader; text stays in memory only."""
import logging
import os
import queue
import sys
sys.dont_write_bytecode = True
import threading
import time
import math
import platform
from collections import OrderedDict
import tkinter as tk
from tkinter import messagebox, filedialog
from pathlib import Path
import customtkinter as ctk
from settings import PROJECT, USER_SETTINGS, Preferences
from privacy import clear_legacy_artifacts, quiet_runtime
import startup
from hotkeys import ACTION_NAMES, DEFAULT_HOTKEYS, validate_bindings
from reader_core import Reader, VOICES, split_text
from export_estimate import analyze_text, select_sample, Calibration, estimate_export
from reader_ui import ReaderUI, BG, ACCENT
from runtime_errors import RuntimeIssue
from win_native import Native, CopyCancelled, clipboard_text, copy_selection, clear_clipboard_if_unchanged, U

from version import APP_NAME, VERSION

THEME_CHOICES = {"白天": "light", "夜晚": "dark", "跟随系统": "system"}
THEME_LABELS = {value: label for label, value in THEME_CHOICES.items()}
ERROR_COLOR = ("#B24B55", "#F397A1")
SUCCESS_COLOR = ("#329678", "#69CBA9")


class App:
    def __init__(self, *, preview=False, start_hidden=False, demo=False):
        self.diagnostics = quiet_runtime()
        self.events = queue.Queue()
        self.demo = demo
        preview = preview or demo
        self.preferences = Preferences() if demo else Preferences.load()
        self.preview = preview
        self.native = None if preview else Native(self.events, self.preferences.hotkeys,
                                                  priority=self.preferences.priority_hotkeys)
        if self.native and self.native.existing:
            return
        if not demo:
            clear_legacy_artifacts()
        ctk.set_appearance_mode(self.preferences.theme)
        self.root = ctk.CTk(fg_color=BG)
        self.background_start = start_hidden
        if start_hidden:
            self.root.withdraw()
        self.root.iconbitmap(str(PROJECT / "assets" / "app.ico"))
        self.root.title(f"{APP_NAME} v{VERSION}" + (" · 界面演示" if demo else ""))
        self.root.protocol("WM_DELETE_WINDOW", self.exit if demo else self.hide)
        self.closed, self.tray_ok = False, True
        self.copy_lock = threading.Lock()
        self.hotkeys, self.metrics = {}, {}
        self.save_timer = None
        self.editor_request = None
        self.exporting = False
        self.export_id = None
        self.export_summary = "正在估算剩余时间"
        self.export_chapters_saved = 0
        self.export_chaptered = False
        self.export_error = None
        self.last_issue = None
        self.estimating = False
        self.estimate_id = self.estimate_token = self.estimate_job = None
        self.calibrations = OrderedDict()  # Session-only numerical measurements; never store source text.
        self.startup_enabled = tk.BooleanVar(value=False if demo else startup.is_enabled())
        if self.startup_enabled.get() and not preview:
            try:
                startup.set_enabled(True)  # Refresh an already authorized startup entry after a move/update.
            except OSError:
                self.events.put(("error", "开机启动路径未能更新，请重新切换自启开关。"))
        self.export_directory = tk.StringVar(value=self.preferences.export_directory)
        self.theme = tk.StringVar(value=THEME_LABELS[self.preferences.theme])
        self.priority_hotkeys = tk.BooleanVar(value=self.preferences.priority_hotkeys)
        self.export_mode = tk.StringVar(value="分章 MP3" if self.preferences.chaptered_export else "单个 MP3")
        self.diagnostic_text = tk.StringVar(value="本次运行暂无错误记录；诊断仅在内存中保留。")
        self.status = tk.StringVar(value="正在准备声音…" if not preview else "界面演示 · 未加载模型")
        self.hint = tk.StringVar()
        self.voice = tk.StringVar(value=next(k for k, v in VOICES.items() if v == self.preferences.voice))
        self.speed = tk.DoubleVar(value=self.preferences.speed)
        self.volume = tk.DoubleVar(value=self.preferences.volume)
        self.speed_text = tk.StringVar()
        self.volume_text = tk.StringVar()
        self.build()
        self.sync_labels()
        if demo:
            self.text.insert("1.0", "让文字，自然地成为声音。\n\n选中一段文字，按下快捷键，即可开始离线朗读。\n\n你也可以将文章粘贴在这里，选择喜欢的声音，调整语速，然后导出为 MP3。所有示例内容仅用于展示界面。")
            self.update_counter()
        self.reader = None if preview else Reader(self.events)
        if self.reader:
            self.reader.audio.set_volume(self.preferences.volume)
        self.root.after(60, self.poll)
        if not start_hidden:
            self.root.after(100, self.show)
        self.root.mainloop()

    def build(self):
        self.ui = ReaderUI(self)
        self.ui.build()

    def sync_labels(self):
        bindings = self.hotkeys or self.preferences.hotkeys
        enabled = [f"{key} {ACTION_NAMES[action]}" for action, key in bindings.items() if key]
        self.hint.set(" · ".join(enabled) if enabled else "快捷键已停用，可使用窗口按钮朗读。")
        self.speed_text.set(f"{self.speed.get():.2f}×")
        self.volume_text.set(f"{self.volume.get():.0%}")

    def update_counter(self):
        self.counter.configure(text=f"{len(self.text.get('1.0', 'end').strip())} 字")

    def clear_text(self):
        self.text.delete("1.0", "end")
        self.text.edit_reset()
        self.editor_request = None
        self.update_counter()

    def clear_completed_text(self, number):
        if self.editor_request and self.editor_request[0] == number:
            if self.text.get("1.0", "end").strip() == self.editor_request[1]:
                self.clear_text()
            self.editor_request = None

    def choose_directory(self):
        value = filedialog.askdirectory(parent=self.root, title="选择 MP3 保存目录",
                                        initialdir=self.preferences.export_directory or str(Path.home()))
        if value:
            self.preferences.export_directory = value
            self.export_directory.set(value)
            self.persist()

    def open_settings_folder(self):
        USER_SETTINGS.mkdir(parents=True, exist_ok=True)
        os.startfile(str(USER_SETTINGS))

    def change_startup(self):
        if self.demo:
            self.startup_enabled.set(False)
            return
        try:
            startup.set_enabled(self.startup_enabled.get())
            self.status.set("已开启开机启动，下次登录后将进入系统托盘。" if self.startup_enabled.get() else "已关闭开机启动。")
        except (OSError, ValueError) as exc:
            self.startup_enabled.set(startup.is_enabled())
            self.show_error(f"开机启动设置失败：{exc}")

    def export_mp3(self):
        if self.exporting or self.estimating:
            self.show_error("请先停止当前导出或预估，再开始新的导出。")
            return
        if not self.reader or not self.reader.ready:
            self.show_error("请等待声音准备完成。")
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            self.show_error("请先粘贴需要生成音频的文字。")
            return
        target = filedialog.asksaveasfilename(parent=self.root, title="导出 MP3（不会覆盖已有文件）",
            initialdir=self.preferences.export_directory or str(Path.home() / "Music"), initialfile="朗读音频.mp3",
            defaultextension=".mp3", filetypes=[("MP3 音频", "*.mp3")], confirmoverwrite=False)
        if not target:
            return
        if Path(target).exists():
            self.show_error("文件已存在。为保护原文件，请换一个文件名。")
            return
        try:
            self.save_playback()
            number = self.reader.submit(text, self.preferences.voice, self.preferences.style,
                                       speed=self.preferences.speed, output=target,
                                       chaptered=self.export_mode.get() == "分章 MP3")
            self.export_id = number
            self.export_summary = "正在估算剩余时间"
            self.exporting = True
            self.export_chapters_saved = 0
            self.export_chaptered = self.export_mode.get() == "分章 MP3"
            self.export_error = None
            self.editor_request = (number, text)
            self.preferences.export_directory = str(Path(target).parent)
            self.export_directory.set(self.preferences.export_directory)
            self.persist()
            self.export_button.configure(state="disabled")
            self.estimate_button.configure(state="disabled")
            self.pause_button.configure(state="disabled")
            self.export_mode_menu.configure(state="disabled")
            self.ui.show_export_progress()
            self.export_progress.set(0)
            self.status.set("正在导出 MP3，准备第一段… 可点击停止取消。")
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))

    def estimate_export_time(self):
        if not self.reader or not self.reader.ready:
            self.show_error("请等待声音准备完成。")
            return
        audio = getattr(self.reader, "audio", None)
        if (self.exporting or self.estimating or getattr(audio, "active", False)
                or getattr(self.reader, "running_number", None) is not None
                or getattr(self.reader, "pending", None) is not None):
            self.show_error("请先停止当前朗读或导出，再预估时间。")
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            self.show_error("请先粘贴需要预估的文字。")
            return
        self.save_playback()
        self.estimating = True
        self.estimate_token = token = object()
        self.export_button.configure(state="disabled")
        self.estimate_button.configure(state="disabled")
        self.pause_button.configure(state="disabled")
        self.status.set("正在分析文字… 可点击停止取消。")
        voice, style, speed = self.preferences.voice, self.preferences.style, self.preferences.speed
        def prepare():
            try:
                parts = split_text(text, max_chars=1_000_000)
                analysis = analyze_text(text, parts=parts)
                sample = select_sample(text, parts=parts)
                self.events.put(("estimate_prepared", (token, analysis, sample, voice, style, speed)))
            except ValueError as exc:
                self.events.put(("estimate_error", (token, str(exc))))
        threading.Thread(target=prepare, name="export-analysis", daemon=True).start()

    def finish_estimate(self):
        self.estimating = False
        self.estimate_id = self.estimate_token = self.estimate_job = None
        self.estimate_button.configure(state="normal")
        self.export_button.configure(state="disabled" if self.exporting else "normal")
        self.pause_button.configure(state="disabled" if self.exporting else "normal")

    @staticmethod
    def time_range(value):
        def display(seconds):
            seconds = max(1, math.ceil(seconds))
            if seconds >= 3600:
                return f"{seconds // 3600} 小时 {(seconds % 3600) // 60} 分"
            if seconds >= 60:
                return f"{seconds // 60} 分 {seconds % 60} 秒"
            return f"{seconds} 秒"
        return f"{display(value.lower)} – {display(value.upper)}"

    def show_estimate(self, analysis, calibration, speed):
        result = estimate_export(analysis, calibration=calibration, speed=speed)
        self.finish_estimate()
        self.status.set(f"预计导出用时 {self.time_range(result.total_seconds)}（参考范围）。")
        messagebox.showinfo("MP3 导出时间预估", (
            f"文字：{analysis.characters:,} 字 · {analysis.segments:,} 段\n"
            f"语速：{speed:.2f}×（按点击时的声音与语气估算）\n"
            f"预计导出用时：{self.time_range(result.total_seconds)}\n"
            f"预计音频时长：{self.time_range(result.audio_seconds)}\n"
            f"建议磁盘可用空间：约 {max(1, math.ceil(result.disk_bytes_upper / 1048576)):,} MB\n\n"
            "基于本机短样本推算，不包含模型启动和本次探测时间。"
            "正文类型、停顿、显卡负载及长时间运行都会影响实际耗时，范围仅供参考。\n\n"
            "样本不播放、不保存；测量数据仅保留到程序退出。"
        ), parent=self.root)

    def defer_save(self):
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
        self.save_timer = self.root.after(450, self.save_playback)

    def save_playback(self):
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
        self.save_timer = None
        self.preferences.voice = VOICES[self.voice.get()]
        self.preferences.speed = round(self.speed.get(), 2)
        self.preferences.volume = round(self.volume.get(), 2)
        self.preferences.chaptered_export = self.export_mode.get() == "分章 MP3"
        self.persist()

    def change_export_mode(self, _choice):
        self.save_playback()

    def reload_model(self):
        if not self.reader:
            self.show_error("界面预览模式不加载模型。")
            return
        try:
            self.reader.restart()
            self.status.set("正在重新加载本地声音，请稍候…")
        except ValueError as exc:
            self.show_error(str(exc))

    def report_issue(self, issue):
        self.last_issue = issue.code
        self.diagnostic_text.set(f"本次最近错误：{issue.code}（不含正文、音频或文件路径）。")
        if self.exporting and issue.code != "audio_unavailable":
            self.export_error = issue.message
        self.show_error(issue.message)

    def copy_diagnostics(self):
        # User explicitly copies this fixed-field summary; no traceback, paths,
        # source text, voice instructions or generation history are included.
        ready = bool(self.reader and self.reader.ready)
        self.root.clipboard_clear()
        self.root.clipboard_append(
            f"JianVox {VERSION}\nPython {platform.python_version()}\n"
            f"Platform: {sys.platform}\nModel ready: {ready}\n"
            f"Last error: {self.last_issue or 'none'}")
        self.status.set("已复制诊断摘要；不含正文、路径、语气设置或生成记录。")

    def persist(self):
        if self.demo:
            return
        try:
            self.preferences.save()
        except (OSError, ValueError) as exc:
            logging.exception("Preference save failed")
            self.show_error(f"设置未能保存：{exc}")

    def change_theme(self, choice):
        theme = THEME_CHOICES.get(choice)
        if theme is None:
            raise ValueError("请选择有效的界面主题。")
        ctk.set_appearance_mode(theme)
        self.theme.set(choice)
        self.preferences.theme = theme
        self.persist()

    def playback_changed(self):
        self.sync_labels()
        if self.reader:
            self.reader.audio.set_volume(self.volume.get())
        self.defer_save()

    def reset_speed(self):
        self.speed.set(1.0)
        self.playback_changed()

    def save_style(self):
        value = self.style_box.get("1.0", "end").strip()
        if len(value) > 500:
            self.show_error("语气说明最多 500 字。")
            return
        self.preferences.style = value
        self.persist()
        self.status.set("语气已保存，下次朗读生效。")

    def default_keys(self):
        for action, entry in self.key_entries.items():
            entry.delete(0, "end")
            entry.insert(0, DEFAULT_HOTKEYS[action])

    def apply_hotkeys(self):
        try:
            bindings = validate_bindings({k: v.get() for k, v in self.key_entries.items()})
        except ValueError as exc:
            self.key_status.configure(text=str(exc), text_color=ERROR_COLOR)
            return
        if self.native:
            self.key_save.configure(state="disabled")
            self.native.retry(bindings, priority=self.priority_hotkeys.get())
        else:
            self.key_status.configure(text="预览模式不注册全局快捷键。")

    def read(self, text, started=None):
        if not self.reader:
            return
        if self.estimating:
            self.show_error("请先停止时间预估，再开始朗读。")
            return
        try:
            self.save_playback()
            number = self.reader.submit(text, self.preferences.voice, self.preferences.style,
                                        started, self.preferences.speed)
            self.metrics = {"request": number, "characters": len(text.strip()), "speed": self.preferences.speed}
            if text.strip() == self.text.get("1.0", "end").strip():
                self.editor_request = (number, text.strip())
            self.pause_button.configure(text="暂停")
            self.status.set("正在生成声音…")
            return number
        except ValueError as exc:
            self.show_error(str(exc))

    def accept_clipboard(self, text, sequence, started=None):
        number = self.read(text, started)
        if number is not None:
            try:
                clear_clipboard_if_unchanged(sequence)
            except (ValueError, OSError):
                self.show_error("朗读已接受，但剪贴板暂时无法清空；请自行清理需要保密的内容。")

    def read_clipboard(self):
        if self.exporting or self.estimating:
            self.show_error("请先停止当前导出或预估，再朗读剪贴板。")
            return
        if not self.reader or not self.reader.ready:
            self.show_error("声音仍在准备中，请等待“已就绪”。")
            return
        try:
            text, sequence = clipboard_text(with_sequence=True)
            self.accept_clipboard(text, sequence)
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))

    def selection(self, started):
        if not self.reader or not self.reader.ready:
            self.show_error("声音仍在准备中，请等待“已就绪”。")
            return
        if self.exporting or self.estimating:
            self.show_error("请先停止当前导出或预估，再朗读选中文字。")
            return
        if not self.copy_lock.acquire(blocking=False):
            return
        self.reader.stop()
        token = self.reader.number
        key = self.hotkeys.get("read", self.preferences.hotkeys["read"])
        def task():
            try:
                text, sequence = copy_selection(key, active=lambda: not self.closed and self.reader.number == token,
                                                defer_clear=True)
                self.events.put(("selection_copied", (text, started, token, sequence)))
            except CopyCancelled:
                pass
            except (ValueError, OSError) as exc:
                self.events.put(("copy_error", (str(exc), token)))
            finally:
                self.copy_lock.release()
        threading.Thread(target=task, daemon=True).start()

    def stop(self):
        if self.estimating:
            self.finish_estimate()  # Ignore a late analysis result without retaining its text.
        if self.reader:
            self.reader.stop()

    def pause(self):
        if self.exporting:
            self.status.set("导出音频可用“停止”取消；暂停键仅用于朗读播放。")
        elif self.reader and self.reader.toggle_pause() is None:
            self.status.set("当前没有正在朗读的内容。")

    def show_error(self, text):
        # Hotkey and worker errors must not change visibility or steal focus.
        self.status.set(text)
        self.dot.configure(text_color=ERROR_COLOR)

    def show(self):
        self.background_start = False
        self.root.deiconify()
        self.root.lift()
        U.SetForegroundWindow(self.root.winfo_id())

    def hide(self):
        if self.tray_ok and self.native:
            self.root.withdraw()
        else:
            self.root.iconify()

    def poll(self):
        changed = False
        try:
            # Yield to native paint/input even if many worker events arrive together.
            for _ in range(100):
                kind, data = self.events.get_nowait()
                changed = True
                if kind == "read_selection":
                    self.selection(data)
                elif kind == "selection_copied" and data[2] == self.reader.number:
                    self.accept_clipboard(data[0], data[3], data[1])
                elif kind == "copy_error" and data[1] == self.reader.number:
                    self.show_error(data[0])
                elif kind == "stop":
                    self.stop()
                elif kind == "pause":
                    self.pause()
                elif kind == "show":
                    self.show()
                elif kind == "exit":
                    self.exit()
                    return
                elif kind == "hotkeys":
                    self.hotkeys = data["bindings"]
                    priority = data.get("priority", self.preferences.priority_hotkeys)
                    self.priority_hotkeys.set(priority)
                    self.key_save.configure(state="normal")
                    self.sync_labels()
                    if data["success"]:
                        self.preferences.hotkeys = dict(self.hotkeys)
                        self.preferences.priority_hotkeys = priority
                        self.persist()
                        self.sync_labels()
                        self.key_status.configure(text="快捷键已生效（优先接管）。" if priority else "快捷键已生效（普通模式）。",
                                                  text_color=SUCCESS_COLOR)
                        for action, entry in self.key_entries.items():
                            entry.delete(0, "end")
                            entry.insert(0, self.hotkeys[action])
                    else:
                        self.key_status.configure(text=data["error"], text_color=ERROR_COLOR)
                        self.show_error(data["error"])
                elif kind == "tray_error":
                    self.tray_ok = False
                    self.show()
                elif kind == "status":
                    self.status.set(data)
                elif kind == "ready":
                    output = getattr(self.reader, "output", None)
                    self.status.set("已就绪，随时可以开始朗读。" if output is None or output.available else
                                    "声音已就绪，正在连接播放设备；MP3 导出可用。")
                    self.dot.configure(text_color=SUCCESS_COLOR)
                elif kind == "engine_loading":
                    self.status.set("正在准备本地声音，请稍候…")
                elif kind == "audio_unavailable":
                    self.report_issue(RuntimeIssue("audio_unavailable", data))
                elif kind == "audio_reconnected":
                    if self.reader and self.reader.ready and not self.exporting and not self.estimating:
                        self.status.set("播放设备已连接，可继续朗读。")
                        self.dot.configure(text_color=SUCCESS_COLOR)
                elif kind == "issue":
                    self.report_issue(data)
                elif kind == "estimate_error" and data[0] is self.estimate_token:
                    self.finish_estimate()
                    self.show_error(data[1])
                elif kind == "estimate_prepared" and data[0] is self.estimate_token:
                    token, analysis, sample, voice, style, speed = data
                    # An English article can open with a Chinese introduction.
                    # Cache by the language actually measured, not the whole article.
                    key = (voice, style, sample.analysis.language_category)
                    calibration = self.calibrations.get(key)
                    if calibration is not None:
                        self.calibrations.move_to_end(key)
                        self.show_estimate(analysis, calibration, speed)
                    else:
                        try:
                            self.estimate_job = (analysis, sample.analysis, key, speed)
                            self.estimate_id = self.reader.probe(sample.text, voice, style)
                            self.status.set("正在生成短样本估时，不播放、不保存… 可点击停止取消。")
                        except ValueError as exc:
                            self.finish_estimate()
                            self.show_error(str(exc))
                elif kind == "probe_done" and data[0] == self.estimate_id and data[0] == self.reader.number:
                    analysis, sample_analysis, key, speed = self.estimate_job
                    try:
                        calibration = Calibration(sample_analysis, data[1], data[2])
                        self.calibrations[key] = calibration
                        self.calibrations.move_to_end(key)
                        while len(self.calibrations) > 8:
                            self.calibrations.popitem(last=False)
                        self.show_estimate(analysis, calibration, speed)
                    except ValueError as exc:
                        self.finish_estimate()
                        self.show_error(str(exc))
                elif kind == "probe_cancelled" and data == self.estimate_id:
                    self.finish_estimate()
                    if self.status.get().startswith("正在"):
                        self.status.set("时间预估已取消。")
                elif kind == "error":
                    self.show_error(data)
                elif kind == "stopped":
                    self.status.set("正在停止导出并清理临时音频…" if self.exporting else
                                    "正在停止时间预估…" if self.estimating else "已停止，可以开始新的朗读。")
                    self.pause_button.configure(text="暂停")
                elif kind == "paused" and data[0] == self.reader.number:
                    self.status.set("已暂停，再按暂停键或点击继续即可接着朗读。" if data[1] else "正在继续朗读…")
                    self.pause_button.configure(text="继续" if data[1] else "暂停")
                elif kind == "first_audio" and data[0] == self.reader.number:
                    self.metrics["first_output_seconds"] = round(data[1], 3)
                    self.status.set(f"正在朗读 · {self.metrics.get('characters', '')} 字 · 约 {data[1]:.2f} 秒开始输出")
                    self.dot.configure(text_color=ACCENT)
                elif kind == "played" and data[0] == self.reader.number:
                    self.metrics["buffer_gaps"] = data[1]
                    self.status.set("朗读完成，随时可以继续。")
                    self.dot.configure(text_color=SUCCESS_COLOR)
                    self.pause_button.configure(text="暂停")
                    self.clear_completed_text(data[0])
                elif kind == "export_progress" and data[0] == self.export_id:
                    _, done, total, eta = data
                    self.export_progress.set(done / total)
                    self.export_summary = f"{done / total:.0%} · 预计剩余 {int(eta)//60} 分 {int(eta)%60} 秒"
                    self.status.set(f"正在导出 MP3 · {self.export_summary} · 可停止取消")
                elif kind == "export_activity" and data[0] == self.export_id:
                    self.status.set(f"正在导出 MP3 · {self.export_summary} · 可停止取消")
                elif kind == "chapter_saved" and data[0] == self.export_id:
                    self.export_chapters_saved = data[1]
                    self.status.set(f"已保存 {data[1]}/{data[2]} 个分章文件，正在继续…")
                elif kind in {"export_done", "export_cancelled"}:
                    number = data[0] if kind == "export_done" else data
                    if number == self.export_id:
                        self.exporting = False
                        self.export_id = None
                        self.export_button.configure(state="normal")
                        self.estimate_button.configure(state="normal")
                        self.pause_button.configure(state="normal", text="暂停")
                        self.export_mode_menu.configure(state="normal")
                        self.ui.hide_export_progress()
                        if kind == "export_done":
                            self.status.set(f"已保存 {self.export_chapters_saved} 个分章 MP3。" if self.export_chaptered else
                                            "MP3 已保存到你选择的位置。")
                            self.clear_completed_text(number)
                        elif self.export_error:
                            self.show_error(self.export_error)
                        else:
                            self.status.set(f"导出已取消，已完成的 {self.export_chapters_saved} 个分章文件已保留。" if self.export_chapters_saved else
                                            "导出已取消，临时音频已清理。")
                        self.editor_request = None
                elif kind == "audio_warning":
                    logging.warning(data)
        except queue.Empty:
            pass
        if not self.closed:
            self.root.after(60, self.poll)

    def exit(self):
        self.closed = True
        self.save_playback()
        if self.reader:
            self.reader.close()
        if self.native:
            self.native.close()
        self.clear_text()
        self.calibrations.clear()
        self.estimate_token = self.estimate_job = None
        self.root.withdraw()
        self.exit_started = time.monotonic()
        self.finish_exit()

    def finish_exit(self):
        if self.reader and self.reader.thread.is_alive() and time.monotonic() - self.exit_started < 10:
            self.root.after(50, self.finish_exit)
            return
        self.root.destroy()


if __name__ == "__main__":
    try:
        App(preview="--preview" in sys.argv, start_hidden="--tray" in sys.argv, demo="--demo" in sys.argv)
    except Exception:
        logging.exception("Desktop application failed")
        messagebox.showerror(APP_NAME, "程序未能启动。请检查运行环境与模型文件；程序未保存错误日志。")
