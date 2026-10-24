"""Real widget-boundary checks at multiple logical window sizes and DPI scales."""
import ctypes as C
from ctypes import wintypes as W
import sys
from unittest.mock import patch
import customtkinter as ctk
from reader_app import App
from settings import Preferences


def contains(widget, parent, label):
    assert widget.winfo_ismapped(), f"{label} is hidden"
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    assert x >= px-2 and y >= py-2, f"{label} exceeds top/left boundary"
    assert x + widget.winfo_width() <= px + parent.winfo_width()+2, f"{label} exceeds right boundary"
    assert y + widget.winfo_height() <= py + parent.winfo_height()+2, f"{label} exceeds bottom boundary"


stdout, stderr = sys.stdout, sys.stderr
app = None
try:
    with patch.object(ctk.CTk, "mainloop"), patch("reader_app.clear_legacy_artifacts"), patch("reader_app.Preferences.load", return_value=Preferences()):
        app = App(preview=True)
        app.show()
        user = C.WinDLL("user32")
        user.GetDpiForWindow.argtypes, user.GetDpiForWindow.restype = [W.HWND], W.UINT
        system_scale = user.GetDpiForWindow(app.root.winfo_id()) / 96
        cases = 0
        for scale in [1, 1.25, 1.5, 2]:
            ctk.set_widget_scaling(scale / system_scale)
            ctk.set_window_scaling(scale / system_scale)
            for width, height in [(800, 480), (860, 570), (1040, 700), (1280, 800)]:
                app.ui.show_page("朗读")
                app.show()
                # CTk temporarily locks min/max while changing scale; release the test window for resize.
                app.root.minsize(800, 480)
                app.root.maxsize(10000, 10000)
                app.root.geometry(f"{width}x{height}")
                app.root.update()
                assert abs(app.root.winfo_width()-width*scale) <= 3, (scale, width, app.root.winfo_width())
                assert abs(app.root.winfo_height()-height*scale) <= 3, (scale, height, app.root.winfo_height())
                for name in ["read_button", "pause_button", "stop_button", "clipboard_button", "export_button", "hide_button", "exit_button"]:
                    contains(getattr(app, name), app.root, name)
                contains(app.action_bar, app.ui.pages["朗读"], "playback toolbar")
                contains(app.text, app.ui.pages["朗读"], "editor")
                contains(app.voice_menu, app.ui.pages["朗读"], "voice selector")
                contains(app.volume_slider, app.ui.pages["朗读"], "volume slider")
                contains(app.ui.status_area, app.root, "status footer")
                app.ui.show_export_progress()
                app.root.update()
                contains(app.action_bar, app.ui.pages["朗读"], "toolbar with export progress")
                contains(app.export_progress, app.root, "export progress")
                app.ui.hide_export_progress()
                app.ui.show_page("快捷键")
                app.root.update()
                for action, entry in app.key_entries.items():
                    contains(entry, app.ui.pages["快捷键"], f"{action} hotkey entry")
                contains(app.key_save, app.ui.pages["快捷键"], "save hotkeys")
                app.ui.show_page("设置")
                app.root.update()
                contains(app.style_box, app.ui.pages["设置"], "style editor")
                cases += 1
        stdout.write(f"LAYOUT_CHECK_PASSED: {cases} window/scale combinations, three pages and export progress\n")
finally:
    if app:
        app.root.destroy()
    sys.stdout, sys.stderr = stdout, stderr
