from unittest.mock import Mock, patch
import unittest

import customtkinter as ctk
from reader_app import App
from settings import Preferences


class ThemeTests(unittest.TestCase):
    def test_saved_appearance_mode_is_applied_before_root_creation(self):
        order = []
        root = Mock()
        preferences = Preferences(theme="system")

        def set_mode(mode):
            order.append(("mode", mode))

        def create_root(*args, **kwargs):
            order.append(("root", None))
            return root

        with patch("reader_app.Preferences.load", return_value=preferences), \
             patch("reader_app.quiet_runtime"), \
             patch("reader_app.clear_legacy_artifacts"), \
             patch("reader_app.startup.is_enabled", return_value=False), \
             patch("reader_app.ctk.set_appearance_mode", side_effect=set_mode), \
             patch("reader_app.ctk.CTk", side_effect=create_root), \
             patch("reader_app.tk.BooleanVar"), \
             patch("reader_app.tk.StringVar"), \
             patch("reader_app.tk.DoubleVar"), \
             patch.object(App, "build"), \
             patch.object(App, "sync_labels"), \
             patch.object(App, "show"), \
             patch.object(ctk.CTk, "mainloop"):
            App(preview=True)

        self.assertEqual(order[:2], [("mode", "system"), ("root", None)])

    def test_theme_change_only_updates_theme_and_does_not_save_demo_preferences(self):
        app = App.__new__(App)
        app.demo = True
        app.preferences = Preferences()
        app.theme = Mock()
        app.text = Mock()
        app.text.get.return_value = "示例正文"

        with patch("reader_app.ctk.set_appearance_mode") as set_mode, \
             patch.object(Preferences, "save") as save:
            app.change_theme("夜晚")

        set_mode.assert_called_once_with("dark")
        app.theme.set.assert_called_once_with("夜晚")
        self.assertEqual(app.preferences.theme, "dark")
        self.assertEqual(app.text.get(), "示例正文")
        save.assert_not_called()
        self.assertFalse(hasattr(app, "reader"))
        self.assertFalse(hasattr(app, "native"))

    def test_unknown_theme_is_rejected_without_applying_it(self):
        app = App.__new__(App)
        app.preferences = Preferences()
        app.theme = Mock()
        with patch("reader_app.ctk.set_appearance_mode") as set_mode:
            with self.assertRaises(ValueError):
                app.change_theme("自动")
        set_mode.assert_not_called()
        self.assertEqual(app.preferences.theme, "system")


if __name__ == "__main__":
    unittest.main()
