"""Real widget-boundary checks at multiple logical window sizes and DPI scales."""
import ctypes as C
from ctypes import wintypes as W
import sys
import time
from unittest.mock import patch
import customtkinter as ctk
from reader_app import App
from settings import Preferences


def contains(widget, parent, label):
    # Window/DPI changes and remapping pages are asynchronous on Windows.
    # Wait only for mapping; the actual bounds assertions below remain strict.
    deadline = time.monotonic() + 1
    while not widget.winfo_ismapped() and time.monotonic() < deadline:
        widget.update()
        time.sleep(.01)
    assert widget.winfo_ismapped(), f"{label} is hidden"
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    assert x >= px-2 and y >= py-2, f"{label} exceeds top/left boundary"
    assert x + widget.winfo_width() <= px + parent.winfo_width()+2, f"{label} exceeds right boundary"
    assert y + widget.winfo_height() <= py + parent.winfo_height()+2, f"{label} exceeds bottom boundary"


def separated(first, second, label):
    ax, ay = first.winfo_rootx(), first.winfo_rooty()
    bx, by = second.winfo_rootx(), second.winfo_rooty()
    assert (ax+first.winfo_width() <= bx+2 or bx+second.winfo_width() <= ax+2
            or ay+first.winfo_height() <= by+2 or by+second.winfo_height() <= ay+2), f"{label} overlap"


stdout, stderr = sys.stdout, sys.stderr
app = None
try:
    with patch.object(ctk.CTk, "mainloop"), patch("reader_app.clear_legacy_artifacts"), patch("reader_app.Preferences.load", return_value=Preferences()), patch.object(App, "estimate_export_time", create=True) as estimate:
        app = App(preview=True)
        app.show()
        user = C.WinDLL("user32")
        user.GetDpiForWindow.argtypes, user.GetDpiForWindow.restype = [W.HWND], W.UINT
        system_scale = user.GetDpiForWindow(app.root.winfo_id()) / 96
        cases = 0
        for theme, expected in [("light", "Light"), ("dark", "Dark"), ("system", "Light")]:
            with patch("darkdetect.theme", return_value=expected):
                ctk.set_appearance_mode(theme)
                ctk.AppearanceModeTracker.init_appearance_mode()
                app.root.update()
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
                        expanded_workspace = None
                        for collapsed in [False, True]:
                            if app.ui.sidebar_collapsed != collapsed:
                                app.ui.sidebar_toggle.invoke()
                            app.ui.show_page("朗读")
                            app.status.set("正在导出 MP3，正在生成片段并估算剩余时间。" * 5)
                            app.root.update()
                            for name in ["read_button", "pause_button", "stop_button", "clipboard_button", "export_mode_menu", "export_button", "estimate_button"]:
                                contains(getattr(app, name), app.root, name)
                            contains(app.ui.sidebar_toggle, app.ui.sidebar_handle, "sidebar toggle")
                            if collapsed:
                                assert not app.ui.sidebar.winfo_ismapped(), "collapsed sidebar still occupies the window"
                                assert app.ui.workspace.winfo_width() >= expanded_workspace + 110 * scale, "sidebar space was not reclaimed"
                                for widget in [*app.ui.nav.values(), app.hide_button, app.exit_button]:
                                    assert not widget.winfo_ismapped(), "sidebar content remains visible when collapsed"
                            else:
                                expanded_workspace = app.ui.workspace.winfo_width()
                                assert app.ui.sidebar.winfo_width() <= 130 * scale, "expanded sidebar is too wide"
                                for name, button in app.ui.nav.items():
                                    contains(button, app.ui.sidebar, f"{name} navigation")
                                    separated(button, app.ui.sidebar_toggle, f"{name} and sidebar toggle")
                                contains(app.hide_button, app.ui.sidebar, "tray button")
                                contains(app.exit_button, app.ui.sidebar, "exit button")
                            contains(app.action_bar, app.ui.pages["朗读"], "playback toolbar")
                            separated(app.ui.playback_actions, app.ui.export_actions, "playback and export actions")
                            separated(app.ui.title, app.ui.voice_controls, "title and voice controls")
                            contains(app.text, app.ui.pages["朗读"], "editor")
                            assert app.text.winfo_height() >= 60 * scale, (scale, width, height, collapsed, app.text.winfo_height(), app.ui.header.winfo_height(), app.ui.status_area.winfo_height(), app.action_bar.winfo_height())
                            assert abs(app.ui.editor.winfo_width()-app.ui.pages["朗读"].winfo_width()) <= 3, "editor does not fill the page width"
                            for name in ["voice_menu", "speed_slider", "volume_slider"]:
                                contains(getattr(app, name), app.ui.header, name)
                            assert app.estimate_button.winfo_rootx() > app.export_button.winfo_rootx(), "estimate must sit to the right of export"
                            contains(app.ui.status_area, app.root, "status footer")
                            contains(app.ui.offline_label, app.ui.status_area, "offline/version footer")
                            assert app.ui.status_label.winfo_rootx()+app.ui.status_label.winfo_width() <= app.ui.offline_label.winfo_rootx()+2, "status overlaps offline/version"
                            app.estimate_button.invoke()
                            assert estimate.call_count == cases + 1
                            app.ui.show_export_progress()
                            app.root.update()
                            contains(app.action_bar, app.ui.pages["朗读"], "toolbar with export progress")
                            contains(app.export_progress, app.root, "export progress")
                            app.ui.hide_export_progress()
                            app.ui.show_page("快捷键")
                            app.root.update()
                            assert not app.ui.voice_controls.winfo_ismapped(), "reader controls remain on the hotkey page"
                            for action, entry in app.key_entries.items():
                                contains(entry, app.ui.pages["快捷键"], f"{action} hotkey entry")
                            contains(app.key_save, app.ui.pages["快捷键"], "save hotkeys")
                            contains(app.priority_checkbox, app.ui.pages["快捷键"], "priority shortcut option")
                            app.ui.show_page("设置")
                            app.ui.settings_panel._parent_canvas.yview_moveto(0)
                            app.root.update()
                            contains(app.theme_menu, app.ui.pages["设置"],
                                     f"theme selection {(theme, scale, width, height, collapsed, cases)}")
                            app.ui.settings_panel._parent_canvas.yview_moveto(1)
                            app.root.update()
                            contains(app.recovery_button, app.ui.pages["设置"], "model recovery button")
                            contains(app.diagnostic_button, app.ui.pages["设置"], "safe diagnostics button")
                            cases += 1
        app.ui.show_page("朗读")
        app.root.update()
        app.ui.export_tooltip.show()
        app.root.update()
        assert app.ui.export_tooltip.window.winfo_ismapped(), "export help is hidden"
        app.ui.export_tooltip.hide()
        assert app.ui.export_tooltip.window is None
        stdout.write(f"LAYOUT_CHECK_PASSED: {cases} theme/window/scale/sidebar combinations, three pages, full sidebar hiding, theme selection and export progress\n")
finally:
    if app:
        app.root.destroy()
    sys.stdout, sys.stderr = stdout, stderr
