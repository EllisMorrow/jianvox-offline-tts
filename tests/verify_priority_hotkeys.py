"""Optional Windows desktop check: a separate process owns the test shortcuts.

Uses only F23/F24 and Ctrl+F24, no clipboard, user text, model, audio, or files.
The original owner receives its shortcut again after interception is closed.
"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from win_native import U, K, INPUT, KEYBDINPUT, Native
from priority_hotkeys import PriorityHotkeys

U.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
U.PeekMessageW.restype = W.BOOL
U.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
U.PostThreadMessageW.restype = W.BOOL
K.GetCurrentThreadId.argtypes = []
K.GetCurrentThreadId.restype = W.DWORD


def owner():
    identifiers = []
    try:
        for identifier, modifiers, vk in [(1, 0, 0x87), (2, 2, 0x87), (3, 0, 0x86)]:
            if not U.RegisterHotKey(None, identifier, modifiers | 0x4000, vk):
                raise RuntimeError("Test shortcuts are already occupied; no other program was modified")
            identifiers.append(identifier)
        current = K.GetCurrentThreadId()
        def quit_on_input():
            sys.stdin.readline()
            U.PostThreadMessageW(current, 0x12, 0, 0)
        threading.Thread(target=quit_on_input, daemon=True).start()
        print("READY", flush=True)
        message = W.MSG()
        while U.GetMessageW(C.byref(message), None, 0, 0) > 0:
            if message.message == 0x312:
                print(int(message.wParam), flush=True)
    finally:
        for identifier in identifiers:
            U.UnregisterHotKey(None, identifier)


def send(*keys):
    inputs = (INPUT * len(keys))()
    for item, (vk, up) in zip(inputs, keys):
        item.type = 1
        item.ki = KEYBDINPUT(vk, 0, 2 if up else 0, 0, 0)
    assert U.SendInput(len(inputs), inputs, C.sizeof(INPUT)) == len(inputs)


def verify():
    assert not any(U.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)), "Release modifiers before this optional check"
    process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--owner"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    owner_events = queue.Queue()
    def collect():
        for line in process.stdout:
            owner_events.put(line.strip())
    threading.Thread(target=collect, daemon=True).start()
    events = queue.Queue()
    ready = queue.Queue()
    thread = None
    hook_thread_id = None
    def intercept():
        hook = PriorityHotkeys(events, {"read": "F24", "stop": "Ctrl+F24", "pause": ""})
        try:
            message = W.MSG()
            U.PeekMessageW(C.byref(message), None, 0, 0, 0)
            hook.install()
            ready.put(K.GetCurrentThreadId())
            while U.GetMessageW(C.byref(message), None, 0, 0) > 0:
                U.TranslateMessage(C.byref(message))
                U.DispatchMessageW(C.byref(message))
        except Exception as exc:
            ready.put(exc)
        finally:
            hook.close()
    try:
        assert owner_events.get(timeout=10) == "READY"
        send((0x87, False), (0x87, True))
        assert owner_events.get(timeout=3) == "1", "The separate process did not receive its registered hotkey"
        thread = threading.Thread(target=intercept, daemon=True)
        thread.start()
        hook_thread_id = ready.get(timeout=5)
        if isinstance(hook_thread_id, Exception):
            raise hook_thread_id
        send((0x87, False), (0x87, False), (0x87, True))
        assert events.get(timeout=3)[0] == "read_selection"
        time.sleep(.15)
        assert events.empty() and owner_events.empty(), "Repeated or duplicate activation"
        send((0x11, False), (0x87, False), (0x87, True), (0x11, True))
        assert events.get(timeout=3)[0] == "stop"
        send((0x86, False), (0x86, True))
        assert owner_events.get(timeout=3) == "3", "An unrelated shortcut was intercepted"
        assert U.PostThreadMessageW(hook_thread_id, 0x12, 0, 0)
        thread.join(timeout=3)
        assert not thread.is_alive()
        send((0x87, False), (0x87, True))
        assert owner_events.get(timeout=3) == "1", "Original owner did not resume after closing interception"
        original_mutex = K.CreateMutexW
        def isolated_mutex(security, initial, _name):
            return original_mutex(security, initial, "Local\\JianVox_Priority_Integration_Check")
        bindings = {"read": "F24", "stop": "Ctrl+F24", "pause": ""}
        native = None
        def native_event(kind):
            end = time.monotonic() + 5
            while time.monotonic() < end:
                event, data = events.get(timeout=max(.1, end-time.monotonic()))
                if event == "error":
                    raise RuntimeError(data)
                if event == kind:
                    return data
            raise TimeoutError(kind)
        with patch("win_native.CLASS_NAME", "JianVoxPriorityIntegrationCheckWindow"), \
             patch("win_native.K.CreateMutexW", side_effect=isolated_mutex), \
             patch("win_native.S.Shell_NotifyIconW", return_value=True):
            try:
                native = Native(events, bindings, priority=True)
                assert not native.existing
                assert native_event("hotkeys")["success"]
                send((0x87, False), (0x87, True))
                native_event("read_selection")
                native.retry(bindings, priority=False)
                result = native_event("hotkeys")
                assert not result["success"] and result["priority"], "Failed mode switch did not restore interception"
                send((0x87, False), (0x87, True))
                native_event("read_selection")
                native.retry({action: "" for action in bindings}, priority=False)
                assert native_event("hotkeys")["success"]
                send((0x87, False), (0x87, True))
                assert owner_events.get(timeout=3) == "1"
            finally:
                if native and not native.existing:
                    native.close()
                    native.thread.join(timeout=3)
                    assert not native.thread.is_alive(), "Native hotkey thread did not close"
        print("OCCUPIED_SHORTCUT_PRIORITY_REPEAT_PASSTHROUGH_AND_RELEASE_PASSED", flush=True)
        print("NATIVE_MODE_SWITCH_ROLLBACK_AND_SHUTDOWN_PASSED", flush=True)
    finally:
        if thread and thread.is_alive() and isinstance(hook_thread_id, int):
            U.PostThreadMessageW(hook_thread_id, 0x12, 0, 0)
            thread.join(timeout=3)
        if process.poll() is None:
            process.stdin.write("exit\n")
            process.stdin.flush()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


if __name__ == "__main__":
    owner() if "--owner" in sys.argv else verify()
