"""Small Windows-only boundary: hotkeys, tray, single instance, clipboard."""
import ctypes as C
from ctypes import wintypes as W
import threading
import time
import queue
from hotkeys import DEFAULT_HOTKEYS, ACTION_NAMES, parse_hotkey, validate_bindings
from priority_hotkeys import PriorityHotkeys
from version import APP_NAME

U = C.WinDLL("user32", use_last_error=True)
K = C.WinDLL("kernel32", use_last_error=True)
S = C.WinDLL("shell32", use_last_error=True)
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", C.c_int),
                ("cbWndExtra", C.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", C.c_byte * 8)]


class NOTIFYICONDATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", W.HICON)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", W.DWORD), ("data", INPUTUNION)]


def api(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype = result
    fn.argtypes = list(args)
    return fn


api(K, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
api(K, "CreateMutexW", W.HANDLE, C.c_void_p, W.BOOL, W.LPCWSTR)
api(K, "CloseHandle", W.BOOL, W.HANDLE)
api(K, "GlobalLock", C.c_void_p, W.HGLOBAL)
api(K, "GlobalUnlock", W.BOOL, W.HGLOBAL)
api(U, "RegisterClassW", W.ATOM, C.POINTER(WNDCLASS))
api(U, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
    C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
api(U, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
api(U, "RegisterHotKey", W.BOOL, W.HWND, C.c_int, W.UINT, W.UINT)
api(U, "UnregisterHotKey", W.BOOL, W.HWND, C.c_int)
api(U, "GetMessageW", W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
api(U, "TranslateMessage", W.BOOL, C.POINTER(W.MSG))
api(U, "DispatchMessageW", LRESULT, C.POINTER(W.MSG))
api(U, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
api(U, "DestroyWindow", W.BOOL, W.HWND)
api(U, "PostQuitMessage", None, C.c_int)
api(U, "FindWindowW", W.HWND, W.LPCWSTR, W.LPCWSTR)
api(U, "LoadIconW", W.HICON, W.HINSTANCE, C.c_void_p)
api(U, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)
api(U, "CreatePopupMenu", W.HMENU)
api(U, "AppendMenuW", W.BOOL, W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR)
api(U, "TrackPopupMenu", W.UINT, W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p)
api(U, "DestroyMenu", W.BOOL, W.HMENU)
api(U, "GetCursorPos", W.BOOL, C.POINTER(W.POINT))
api(U, "SetForegroundWindow", W.BOOL, W.HWND)
api(U, "GetForegroundWindow", W.HWND)
api(U, "OpenClipboard", W.BOOL, W.HWND)
api(U, "CloseClipboard", W.BOOL)
api(U, "EmptyClipboard", W.BOOL)
api(U, "GetClipboardData", W.HANDLE, W.UINT)
api(U, "GetClipboardSequenceNumber", W.DWORD)
api(U, "IsClipboardFormatAvailable", W.BOOL, W.UINT)
api(U, "GetAsyncKeyState", W.SHORT, C.c_int)
api(U, "SendInput", W.UINT, W.UINT, C.POINTER(INPUT), C.c_int)
api(S, "Shell_NotifyIconW", W.BOOL, W.DWORD, C.POINTER(NOTIFYICONDATA))
api(U, "LoadImageW", W.HANDLE, W.HINSTANCE, W.LPCWSTR, W.UINT, C.c_int, C.c_int, W.UINT)
api(U, "DestroyIcon", W.BOOL, W.HICON)

CLASS_NAME = "QwenOfflineReaderLocalWindow_v1"
COPY_SETTLE_SECONDS = .3


class CopyCancelled(ValueError):
    pass


def clipboard_text(clear_after=False, *, expected_sequence=None, active=None):
    deadline = time.monotonic() + .6
    while not U.OpenClipboard(None):
        if active is not None and not active():
            raise CopyCancelled()
        if time.monotonic() >= deadline:
            raise ValueError("剪贴板暂时被其他程序占用，请重试。")
        time.sleep(.015)
    try:
        if active is not None and not active():
            raise CopyCancelled()
        if expected_sequence is not None and U.GetClipboardSequenceNumber() != expected_sequence:
            raise ValueError("剪贴板在读取前被其他程序更新，请重新选字。")
        if not U.IsClipboardFormatAvailable(13):
            raise ValueError("剪贴板里没有文字。")
        handle = U.GetClipboardData(13)
        pointer = K.GlobalLock(handle) if handle else None
        if not pointer:
            raise C.WinError(C.get_last_error())
        try:
            text = C.wstring_at(pointer)
        finally:
            K.GlobalUnlock(handle)
        if active is not None and not active():
            raise CopyCancelled()
        if clear_after and not U.EmptyClipboard():
            raise ValueError("未能清空本次剪贴板，请稍后重试。")
        return text
    finally:
        U.CloseClipboard()


def copy_selection(hotkey="F4", *, active=None):
    # Never read the previous clipboard if the target app did not complete Ctrl+C.
    target = U.GetForegroundWindow()
    if not target:
        raise ValueError("当前窗口无法自动复制，请手动复制后使用“朗读剪贴板”。")
    def guard():
        if active is not None and not active():
            raise CopyCancelled()
        if U.GetForegroundWindow() != target:
            raise ValueError("复制期间切换了窗口，请重新选中文字后朗读。")
    def settle(seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            guard()
            time.sleep(.01)
    deadline = time.monotonic() + 1.2
    release_keys = [parse_hotkey(hotkey).vk, 0x10, 0x11, 0x12, 0x5B, 0x5C]
    while any(U.GetAsyncKeyState(key) & 0x8000 for key in release_keys):
        guard()
        if time.monotonic() >= deadline:
            raise ValueError(f"请轻按一下 {hotkey}，并松开组合键。")
        time.sleep(.01)
    # Let mouse selection, key-up and the target application's UI settle first.
    settle(COPY_SETTLE_SECONDS)
    inputs = (INPUT * 4)()
    for item, (vk, flags) in zip(inputs, [(0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2)]):
        item.type = 1
        item.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    for attempt in range(2):
        guard()
        # Snapshot only immediately before Ctrl+C, not before the settling delay.
        before = U.GetClipboardSequenceNumber()
        if U.SendInput(4, inputs, C.sizeof(INPUT)) != 4:
            raise ValueError("无法复制当前程序的文字。请手动复制后点击“朗读剪贴板”。")
        deadline = time.monotonic() + 1.5
        changed = False
        while time.monotonic() < deadline:
            guard()
            sequence = U.GetClipboardSequenceNumber()
            if sequence != before:
                changed = True
                if U.IsClipboardFormatAvailable(13):
                    return clipboard_text(clear_after=True, expected_sequence=sequence, active=active)
            time.sleep(.015)
        if changed:
            raise ValueError("本次复制没有可朗读的文字，请确认选中的是可复制文本。")
        # One retry only if no new copy was observed and the source remains focused.
        if not attempt:
            settle(COPY_SETTLE_SECONDS)
    raise ValueError("未复制到新文字。请先选中可复制的文字再按朗读快捷键；也可手动复制后使用“朗读剪贴板”。")


class Native:
    def __init__(self, events, bindings=None, *, priority=False):
        self.events = events
        self.hwnd = None
        self.registered = set()
        self.bindings = {}
        self.priority = False
        self.priority_hook = None
        self.initial_priority = priority
        self.initial_bindings = validate_bindings(bindings or DEFAULT_HOTKEYS)
        self.commands = queue.Queue()
        self.icon = None
        self.mutex = K.CreateMutexW(None, False, "Local\\QwenOfflineReader_Desktop_1")
        self.existing = C.get_last_error() == 183
        if self.existing:
            hwnd = U.FindWindowW(CLASS_NAME, None)
            if hwnd:
                U.PostMessageW(hwnd, 0x8002, 0, 0)
            K.CloseHandle(self.mutex)
            return
        self.thread = threading.Thread(target=self._run, name="windows-hotkeys", daemon=True)
        self.thread.start()

    def _tray(self, action):
        data = NOTIFYICONDATA()
        data.cbSize = C.sizeof(data)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = 1 | 2 | 4
        data.uCallbackMessage = 0x8001
        data.hIcon = self.icon or U.LoadIconW(None, C.c_void_p(32516))
        data.szTip = APP_NAME + " · " + " / ".join(self.bindings.values())
        return S.Shell_NotifyIconW(action, C.byref(data))

    def _register(self, bindings):
        failures = []
        for identifier, action in enumerate(DEFAULT_HOTKEYS, 1):
            if action not in bindings or not bindings[action]:
                continue
            key = parse_hotkey(bindings[action])
            if U.RegisterHotKey(self.hwnd, identifier, 0x4000 | key.modifiers, key.vk):
                self.registered.add(identifier)
                self.bindings[action] = key.label
            else:
                failures.append(f"{ACTION_NAMES[action]} {key.label}")
        return failures

    def _unregister(self):
        if self.priority_hook:
            self.priority_hook.close()
            self.priority_hook = None
        for key in self.registered:
            U.UnregisterHotKey(self.hwnd, key)
        self.registered.clear()
        self.bindings = {}
        self.priority = False

    def _activate(self, values, priority):
        if not priority:
            return self._register(values)
        if any(values.values()):
            hook = PriorityHotkeys(self.events, values)
            hook.install()
            self.priority_hook = hook
        self.bindings = {key: value for key, value in values.items() if value}
        self.priority = True
        return []

    def _keys(self, values, priority=None):
        previous = {action: self.bindings.get(action, "") for action in DEFAULT_HOTKEYS}
        previous_priority = self.priority
        priority = previous_priority if priority is None else priority
        try:
            values = validate_bindings(values)
            if not isinstance(priority, bool):
                raise ValueError("快捷键优先接管设置无效。")
        except ValueError as exc:
            self.events.put(("hotkeys", {"success": False, "bindings": previous,
                                         "priority": previous_priority, "error": str(exc)}))
            return
        try:
            self._unregister()
        except OSError:
            self.events.put(("hotkeys", {"success": False, "bindings": previous,
                "priority": previous_priority, "error": "原快捷键接管未能解除，请退出并重新打开程序。"}))
            return
        try:
            failures = self._activate(values, priority)
        except OSError as exc:
            failures = [f"优先接管启动失败（Windows 错误 {exc.winerror}）"]
        error = ""
        if failures:
            self._unregister()
            try:
                restore_failures = self._activate(previous, previous_priority)
            except OSError:
                restore_failures = ["原快捷键接管恢复失败"]
            error = ("、".join(failures) + "。" if priority else
                     "以下快捷键被其他程序占用：" + "、".join(failures) + "。可启用优先接管或更换组合。")
            if restore_failures:
                error += "部分原快捷键也未能恢复，请重新设置。"
        self._tray(1)
        effective = {action: self.bindings.get(action, "") for action in DEFAULT_HOTKEYS}
        self.events.put(("hotkeys", {"success": not failures, "bindings": effective,
                                     "priority": self.priority, "error": error}))

    def retry(self, bindings=None, *, priority=None):
        if self.hwnd:
            self.commands.put((dict(bindings or self.initial_bindings), priority))
            U.PostMessageW(self.hwnd, 0x8003, 0, 0)

    def close(self):
        if self.hwnd:
            U.PostMessageW(self.hwnd, 0x10, 0, 0)

    def _run(self):
        self.explorer = U.RegisterWindowMessageW("TaskbarCreated")
        self.proc = WNDPROC(self._wndproc)
        instance = K.GetModuleHandleW(None)
        wc = WNDCLASS(lpfnWndProc=self.proc, hInstance=instance, lpszClassName=CLASS_NAME)
        if not U.RegisterClassW(C.byref(wc)):
            self.events.put(("error", f"无法注册快捷键窗口：{C.get_last_error()}"))
            return
        self.hwnd = U.CreateWindowExW(0x80, CLASS_NAME, APP_NAME, 0,
                                      0, 0, 0, 0, None, None, instance, None)
        if not self.hwnd:
            self.events.put(("error", f"无法创建快捷键窗口：{C.get_last_error()}"))
            return
        from pathlib import Path
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
        self.icon = U.LoadImageW(None, str(icon_path), 1, 32, 32, 0x10)
        self._keys(self.initial_bindings, self.initial_priority)
        if not self._tray(0):
            self.events.put(("tray_error", None))
        message = W.MSG()
        while U.GetMessageW(C.byref(message), None, 0, 0) > 0:
            U.TranslateMessage(C.byref(message))
            U.DispatchMessageW(C.byref(message))
        self._unregister()
        K.CloseHandle(self.mutex)
        if self.icon:
            U.DestroyIcon(self.icon)

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == 0x312:
            action = {1: "read_selection", 2: "stop", 3: "pause"}.get(wp)
            if action:
                self.events.put((action, time.perf_counter()))
        elif msg == 0x8002:
            self.events.put(("show", None))
        elif msg == 0x8003:
            try:
                self._keys(*self.commands.get_nowait())
            except queue.Empty:
                pass
        elif msg == 0x8004:
            self.events.put(("exit", None))
        elif msg == self.explorer:
            self._tray(0)
        elif msg == 0x8001:
            if lp == 0x203:
                self.events.put(("show", None))
            elif lp == 0x205:
                menu = U.CreatePopupMenu()
                for number, text in [(1, "显示朗读窗口"), (4, "暂停 / 继续"), (2, "停止朗读"), (3, "退出并释放显存")]:
                    U.AppendMenuW(menu, 0, number, text)
                point = W.POINT()
                U.GetCursorPos(C.byref(point))
                U.SetForegroundWindow(hwnd)
                choice = U.TrackPopupMenu(menu, 0x100 | 2, point.x, point.y, 0, hwnd, None)
                U.DestroyMenu(menu)
                U.PostMessageW(hwnd, 0, 0, 0)
                if choice:
                    self.events.put(({1: "show", 2: "stop", 3: "exit", 4: "pause"}[choice], None))
        elif msg == 0x10:
            self._tray(2)
            self._unregister()
            U.DestroyWindow(hwnd)
        elif msg == 2:
            U.PostQuitMessage(0)
        else:
            return U.DefWindowProcW(hwnd, msg, wp, lp)
        return 0
