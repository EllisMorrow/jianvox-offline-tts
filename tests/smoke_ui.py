"""Build actual widgets without loading the GPU model. Run separately on Windows."""
import sys
from unittest.mock import patch, Mock
from types import SimpleNamespace
import tempfile
from pathlib import Path
import customtkinter as ctk
from reader_app import App
from settings import Preferences

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
            app.reader = None
    app.root.destroy()
sys.stdout, sys.stderr = stdout, stderr
print("UI_BUILD_CHECK_PASSED")
