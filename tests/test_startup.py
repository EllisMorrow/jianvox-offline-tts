import unittest
from unittest.mock import patch, MagicMock
import startup


class StartupTests(unittest.TestCase):
    def test_enable_uses_per_user_key_and_tray_argument(self):
        with patch.object(startup.winreg, "OpenKey", side_effect=FileNotFoundError), patch.object(startup.winreg, "CreateKey") as create, patch.object(startup.winreg, "SetValueEx") as write:
            startup.set_enabled(True)
            create.assert_called_once_with(startup.winreg.HKEY_CURRENT_USER, startup.KEY)
            args = write.call_args.args
            self.assertEqual(args[1], startup.NAME)
            self.assertIn("--tray", args[4])
            self.assertIn("pythonw.exe", args[4])

    def test_disable_deletes_only_own_startup_value(self):
        with patch.object(startup.winreg, "OpenKey"), patch.object(startup.winreg, "DeleteValue") as delete:
            startup.set_enabled(False)
            self.assertEqual(delete.call_args.args[1], startup.NAME)

    def test_missing_startup_is_disabled(self):
        with patch.object(startup.winreg, "OpenKey", side_effect=FileNotFoundError):
            self.assertFalse(startup.is_enabled())
            startup.set_enabled(False)
