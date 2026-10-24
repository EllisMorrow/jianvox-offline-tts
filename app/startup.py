"""Only the application's own per-user startup value is managed."""
from pathlib import Path
import subprocess
import sys
import winreg

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "QwenOfflineReader"


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, _ = winreg.QueryValueEx(key, NAME)
            return bool(value)
    except FileNotFoundError:
        return False


def set_enabled(enabled):
    if enabled:
        executable = Path(sys.executable).with_name("pythonw.exe")
        app = Path(__file__).resolve().parent / "reader_app.py"
        if not executable.is_file() or not app.is_file():
            raise ValueError("找不到启动文件，请重新打开程序后再设置。")
        command = subprocess.list2cmdline([str(executable), str(app), "--tray"])
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
                existing, _ = winreg.QueryValueEx(key, NAME)
                if existing == command:
                    return
        except FileNotFoundError:
            pass
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, NAME)
        except FileNotFoundError:
            pass
