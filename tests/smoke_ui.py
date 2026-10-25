"""Build actual widgets without loading the GPU model. Run separately on Windows."""
import sys
import time
from unittest.mock import patch, Mock
from types import SimpleNamespace
import tempfile
from pathlib import Path
import customtkinter as ctk
from reader_app import App
from settings import Preferences
from reader_ui import PANEL

stdout, stderr = sys.stdout, sys.stderr
with patch.object(ctk.CTk, "mainloop"), patch("reader_app.clear_legacy_artifacts"), patch("reader_app.Preferences.load", return_value=Preferences()):
    hidden = "--hidden" in sys.argv
    app = App(preview=True, start_hidden=hidden)
    app.root.update()
    if hidden:
        assert not app.root.winfo_viewable(), "Tray startup revealed the main window"
    else:
        app.show()
        app.root.update()
        stdout.write(f"Window: {app.root.winfo_width()}x{app.root.winfo_height()}; editor: {app.text.winfo_width()}\n")
        assert app.text.winfo_width() > 300
        assert len(app.key_entries) == 3
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
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "persist"), patch("reader_app.filedialog.asksaveasfilename", return_value=str(Path(tmp)/"test.mp3")):
            app.text.insert("1.0", "这是窗口导出流程的测试文字。")
            app.reader = SimpleNamespace(ready=True, number=1, submit=Mock(return_value=1))
            app.export_mp3()
            assert app.exporting and app.export_button.cget("state") == "disabled"
            app.events.put(("export_progress", (1, 50, 100, 5)))
            app.poll()
            assert "50%" in app.status.get()
            app.events.put(("export_done", (1, str(Path(tmp)/"test.mp3"))))
            app.poll()
            assert not app.exporting and not app.text.get("1.0", "end").strip()
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
    app.root.destroy()
sys.stdout, sys.stderr = stdout, stderr
print("UI_BUILD_CHECK_PASSED")
