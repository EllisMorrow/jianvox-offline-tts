"""Build actual widgets without loading the GPU model. Run separately on Windows."""
import sys
import time
import queue
from unittest.mock import patch, Mock
from types import SimpleNamespace
import tempfile
from pathlib import Path
import customtkinter as ctk
from reader_app import App
from settings import Preferences
from reader_ui import PANEL
from reader_core import AudioBuffer

stdout, stderr = sys.stdout, sys.stderr
with patch.object(ctk.CTk, "mainloop"), patch("reader_app.clear_legacy_artifacts"), patch("reader_app.Preferences.load", return_value=Preferences()):
    hidden = "--hidden" in sys.argv
    app = App(preview=True, start_hidden=hidden)
    app.root.update()
    if hidden:
        assert not app.root.winfo_viewable(), "Tray startup revealed the main window"
        app.background_start = False  # Previously opened, then hidden by the user.
        app.reader = SimpleNamespace(ready=False, number=3, stop=Mock(), toggle_pause=Mock(return_value=None))
        for event in [("read_selection", time.perf_counter()), ("copy_error", ("模拟复制失败", 3)),
                      ("pause", None), ("stop", None), ("error", "模拟后台错误")]:
            app.events.put(event)
            app.poll()
            app.root.update()
            assert not app.root.winfo_viewable(), f"Background shortcut revealed the main window: {event[0]}"
        app.events.put(("show", None))
        app.poll()
        app.root.update()
        assert app.root.winfo_viewable(), "Explicit tray show action no longer works"
        app.reader = None
    else:
        app.show()
        app.root.update()
        stdout.write(f"Window: {app.root.winfo_width()}x{app.root.winfo_height()}; editor: {app.text.winfo_width()}\n")
        assert app.text.winfo_width() > 300
        assert len(app.key_entries) == 3
        app.ui.show_page("设置")
        app.root.update()
        assert app.close_to_tray_switch.winfo_ismapped(), "Close-to-tray setting is missing"
        close_handler = app.root.protocol("WM_DELETE_WINDOW")
        with patch.object(app, "persist"), patch.object(app, "hide") as hide, patch.object(app, "exit") as exit_app:
            assert app.close_to_tray.get()
            app.root.tk.call(close_handler)
            hide.assert_called_once()
            exit_app.assert_not_called()
            app.close_to_tray_switch.toggle()
            assert not app.preferences.close_to_tray
            app.root.tk.call(close_handler)
            exit_app.assert_called_once()
            app.close_to_tray_switch.toggle()
            assert app.preferences.close_to_tray
        app.ui.show_page("朗读")
        with patch.object(app, "persist"):
            assert app.volume_slider.cget("to") == 1.5
            app.reader = SimpleNamespace(audio=AudioBuffer(queue.Queue()))
            # Exercise the real slider callback rather than manually invoking
            # playback_changed: live gain must work before preferences save.
            app.volume_slider._clicked(SimpleNamespace(
                x=app.volume_slider._apply_widget_scaling(app.volume_slider._current_width), y=1))
            assert app.reader.audio.get_volume() == 1.5
            assert app.preferences.volume == .85
            app.save_playback()
            assert app.volume.get() == 1.5 and app.volume_text.get() == "150%"
            assert app.preferences.volume == 1.5
            app.volume_slider.set(.85)
            app.playback_changed()
            app.save_playback()
            app.reader = None
        app.default_keys()
        assert app.key_entries["read"].get() == "F4"
        original_editor = app.text
        app.text.insert("1.0", "切换主题时保留这段公开测试文字。")
        app.reader = Mock()
        original_reader = app.reader
        app.exporting = True
        with patch.object(app, "persist"):
            for choice, appearance, mode in [("白天", "Light", 0), ("夜晚", "Dark", 1),
                                             ("跟随系统", "Light", 0)]:
                with patch("darkdetect.theme", return_value=appearance):
                    app.theme_menu._dropdown_callback(choice)
                    ctk.AppearanceModeTracker.init_appearance_mode()
                    app.root.update()
                    assert ctk.get_appearance_mode() == appearance
                    assert app.text._textbox.cget("background").lower() == PANEL[mode].lower()
                    assert app.text is original_editor and app.reader is original_reader
                    assert app.exporting and app.text.get("1.0", "end").strip()
            with patch("darkdetect.theme", return_value="Dark"):
                ctk.AppearanceModeTracker.init_appearance_mode()
                app.root.update()
                assert ctk.get_appearance_mode() == "Dark"
                assert app.text._textbox.cget("background").lower() == PANEL[1].lower()
                assert app.preferences.theme == "system"
        app.reader = None
        app.exporting = False
        app.clear_text()
        with patch.object(app, "persist"), patch.object(app, "show_error"):
            app.native = Mock()
            app.priority_hotkeys.set(True)
            app.apply_hotkeys()
            expected = {action: entry.get() for action, entry in app.key_entries.items()}
            app.native.retry.assert_called_once_with(expected, priority=True)
            app.events.put(("hotkeys", {"success": True, "bindings": expected, "priority": True, "error": ""}))
            app.poll()
            assert app.preferences.priority_hotkeys
            assert "优先接管" in app.key_status.cget("text")
            app.priority_hotkeys.set(False)
            app.events.put(("hotkeys", {"success": False, "bindings": expected, "priority": True, "error": "测试冲突"}))
            app.poll()
            assert app.priority_hotkeys.get(), "Failed change did not restore the effective mode"
            app.native = None
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "persist"), patch("reader_app.filedialog.asksaveasfilename", return_value=str(Path(tmp)/"test.mp3")):
            source = "  这是窗口朗读与导出流程的测试文字。\nKeep the text.\n\n"
            app.text.insert("1.0", source)
            app.reader = SimpleNamespace(ready=True, number=1, submit=Mock(return_value=1))
            app.read_button.invoke()
            assert app.status.get().startswith("窗口文字"), "Editor read claimed a copy channel"
            app.events.put(("played", (1, 0)))
            app.poll()
            assert app.status.get().startswith("窗口文字"), "Completion lost the text source"
            assert app.text.get("1.0", "end-1c") == source, "Reading cleared or changed editor text"
            app.native = Mock()
            app.hide()
            app.root.update()
            assert app.text.get("1.0", "end-1c") == source, "Hiding to tray cleared editor text"
            app.native = None
            app.show()
            app.root.update()
            app.export_mp3()
            assert app.exporting and app.export_button.cget("state") == "disabled"
            app.events.put(("export_progress", (1, 50, 100, 5)))
            app.poll()
            assert "50%" in app.status.get()
            app.events.put(("export_done", (1, str(Path(tmp)/"test.mp3"))))
            app.poll()
            assert not app.exporting and app.text.get("1.0", "end-1c") == source, "Export cleared or changed editor text"
            app.clear_button.invoke()
            assert not app.text.get("1.0", "end").strip(), "Explicit clear did not empty the editor"
            app.text.insert("1.0", "这是公开的预估流程测试。" * 10)
            app.reader = SimpleNamespace(ready=True, number=10, running_number=None, pending=None,
                audio=SimpleNamespace(active=False), probe=Mock(return_value=10))
            with patch("reader_app.messagebox.showinfo") as result:
                app.estimate_export_time()
                deadline = time.monotonic() + 5
                while app.estimate_id is None and time.monotonic() < deadline:
                    app.root.update()
                    app.poll()
                    time.sleep(.01)
                assert app.estimate_id == 10
                app.events.put(("probe_done", (10, 3.0, 5.0)))
                app.poll()
                assert not app.estimating and result.call_count == 1
                assert app.text.get("1.0", "end").strip(), "Estimating cleared the input"
                assert len(app.calibrations) == 1
                app.estimate_export_time()
                deadline = time.monotonic() + 5
                while app.estimating and time.monotonic() < deadline:
                    app.root.update()
                    app.poll()
                    time.sleep(.01)
                assert not app.estimating and result.call_count == 2
                app.reader.probe.assert_called_once()
            app.reader = None
        with patch.object(app, "persist"), patch.object(app, "finish_exit"):
            app.exit()
            assert not app.text.get("1.0", "end").strip(), "Actual exit did not clear editor text"
    app.root.destroy()
sys.stdout, sys.stderr = stdout, stderr
print("UI_BUILD_CHECK_PASSED")
