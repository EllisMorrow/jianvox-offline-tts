"""Closing the window follows the saved preference without changing explicit actions."""
from unittest.mock import Mock, patch
import unittest

from reader_app import App
from settings import Preferences


class WindowCloseTests(unittest.TestCase):
    def app(self, *, close_to_tray=True, demo=False):
        app = App.__new__(App)
        app.demo = demo
        app.preferences = Preferences(close_to_tray=close_to_tray)
        app.close_to_tray = Mock()
        app.root = Mock()
        app.native, app.tray_ok = Mock(), True
        app.exit = Mock()
        return app

    def test_default_close_hides_without_exiting(self):
        app = self.app()
        app.on_window_close()
        app.root.withdraw.assert_called_once()
        app.exit.assert_not_called()

    def test_disabled_close_exits_without_hiding(self):
        app = self.app(close_to_tray=False)
        app.on_window_close()
        app.exit.assert_called_once()
        app.root.withdraw.assert_not_called()

    def test_changed_setting_applies_immediately_in_both_directions(self):
        app = self.app()
        with patch.object(app, "persist") as persist:
            app.close_to_tray.get.return_value = False
            app.change_close_to_tray()
            self.assertFalse(app.preferences.close_to_tray)
            app.on_window_close()
            app.exit.assert_called_once()
            app.close_to_tray.get.return_value = True
            app.change_close_to_tray()
            self.assertTrue(app.preferences.close_to_tray)
            app.on_window_close()
            app.root.withdraw.assert_called_once()
            self.assertEqual(persist.call_count, 2)

    def test_missing_tray_falls_back_to_taskbar_instead_of_disappearing(self):
        app = self.app()
        app.tray_ok = False
        app.on_window_close()
        app.root.iconify.assert_called_once()
        app.root.withdraw.assert_not_called()
        app.exit.assert_not_called()

    def test_explicit_hide_still_works_when_close_means_exit(self):
        app = self.app(close_to_tray=False)
        app.hide()
        app.root.withdraw.assert_called_once()
        app.exit.assert_not_called()

    def test_demo_window_always_exits(self):
        app = self.app(demo=True)
        app.on_window_close()
        app.exit.assert_called_once()
        app.root.withdraw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
