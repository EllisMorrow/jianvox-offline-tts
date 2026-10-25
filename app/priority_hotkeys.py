"""Optional, thread-owned Windows shortcut interception. No text or key logging."""
import ctypes as C
from ctypes import wintypes as W
import time
from hotkeys import HotkeyMatcher

U = C.WinDLL("user32", use_last_error=True)
K = C.WinDLL("kernel32", use_last_error=True)
LRESULT = C.c_ssize_t
HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


U.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
U.SetWindowsHookExW.restype = W.HANDLE
U.CallNextHookEx.argtypes = [W.HANDLE, C.c_int, W.WPARAM, W.LPARAM]
U.CallNextHookEx.restype = LRESULT
U.UnhookWindowsHookEx.argtypes = [W.HANDLE]
U.UnhookWindowsHookEx.restype = W.BOOL
U.GetAsyncKeyState.argtypes = [C.c_int]
U.GetAsyncKeyState.restype = W.SHORT
K.GetModuleHandleW.argtypes = [W.LPCWSTR]
K.GetModuleHandleW.restype = W.HMODULE


class PriorityHotkeys:
    """Install/close on the native message-loop thread; callback only matches and queues."""
    def __init__(self, events, bindings):
        self.events = events
        # LL callbacks precede the OS async-state update: track modifier transitions
        # ourselves, using async state only to seed keys held before installation.
        held = [key for key in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)
                if U.GetAsyncKeyState(key) & 0x8000]
        self.matcher = HotkeyMatcher(bindings, held)
        self.handle = None
        self.enabled = True
        self.callback = HOOKPROC(self._callback)  # Keep alive until unhooked.

    def install(self):
        if self.handle:
            raise RuntimeError("快捷键接管已经启动。")
        self.handle = U.SetWindowsHookExW(13, self.callback, K.GetModuleHandleW(None), 0)
        if not self.handle:
            raise C.WinError(C.get_last_error())

    def close(self):
        if self.handle:
            if not U.UnhookWindowsHookEx(self.handle) and C.get_last_error() != 1404:
                raise C.WinError(C.get_last_error())
            self.handle = None
        self.enabled = False
        self.matcher.intercepted.clear()

    def _callback(self, code, message, pointer):
        if code == 0 and self.enabled and message in (0x100, 0x101, 0x104, 0x105):
            try:
                key = C.cast(pointer, C.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                consume, action = self.matcher.process(key, message in (0x100, 0x104))
                if action:
                    event = {"read": "read_selection", "stop": "stop", "pause": "pause"}[action]
                    self.events.put((event, time.perf_counter()))
                if consume:
                    return 1
            except Exception:
                # A ctypes callback must never return an undefined value or block
                # keyboard input after a Python exception. Report once and pass through.
                self.enabled = False
                self.matcher.intercepted.clear()
                self.events.put(("error", "快捷键优先接管已失效，请关闭并重新开启该选项。"))
        return U.CallNextHookEx(self.handle, code, message, pointer)
