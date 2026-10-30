"""Read genuine UIA text selections in a disposable, time-limited process.

No clipboard, document/value fallback, UI mutations, third-party imports or files.
The parent checks cancellation and foreground identity while draining an anonymous
pipe; a blocked accessibility provider is killed together with its worker.
"""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


class SelectionUnavailable(ValueError):
    """A fixed category, never provider exception details or selected text."""

    def __init__(self, code="unavailable"):
        self.code = code
        messages = {
            "timeout": "辅助功能读取选中文字超时，请重新选择后重试。",
            "too_long": "选中文字超过朗读字数上限。",
        }
        super().__init__(messages.get(code, "无法通过辅助功能读取选中文字，请重新选择后重试。"))


class SelectionCancelled(ValueError):
    def __init__(self):
        super().__init__("选字读取已取消或前台窗口已切换。")


def _foreground_window():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    return user32.GetForegroundWindow() or 0


def _check_request(target_hwnd, active):
    # A caller's guard may raise its own cancellation exception. Preserve it.
    if active is not None and not active():
        raise SelectionCancelled()
    if _foreground_window() != target_hwnd:
        raise SelectionCancelled()


def _start_worker(target_hwnd, max_chars):
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return subprocess.Popen(
        [str(executable), "-I", "-S", "-B", str(Path(__file__).resolve()),
         "--worker", str(target_hwnd), str(max_chars)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True, bufsize=0,
    )


def _available_bytes(pipe):
    import msvcrt
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_ulong, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    kernel32.PeekNamedPipe.restype = ctypes.c_int
    available = ctypes.c_ulong()
    if not kernel32.PeekNamedPipe(msvcrt.get_osfhandle(pipe.fileno()), None,
                                 0, None, ctypes.byref(available), None):
        if ctypes.get_last_error() in (109, 232):  # Pipe closed by worker.
            return 0
        raise SelectionUnavailable()
    return available.value


def _wait_result(process, target_hwnd, active, deadline, max_chars):
    payload = bytearray()
    while True:
        _check_request(target_hwnd, active)
        if time.monotonic() >= deadline:
            raise SelectionUnavailable("timeout")
        available = _available_bytes(process.stdout)
        if available:
            if len(payload) + available > max_chars * 4 + 64:
                raise SelectionUnavailable("too_long")
            payload.extend(os.read(process.stdout.fileno(), available))
            continue
        if process.poll() is not None:
            # The worker may have written between PeekNamedPipe and poll().
            if _available_bytes(process.stdout):
                continue
            break
        time.sleep(min(.01, max(0, deadline - time.monotonic())))
    _check_request(target_hwnd, active)
    if process.returncode != 0 or not payload:
        raise SelectionUnavailable()
    if payload[:1] == b"E":
        code = bytes(payload[1:])
        if code == b"cancelled":
            raise SelectionCancelled()
        category = code.decode("ascii") if code in (b"too_long", b"timeout") else "unavailable"
        raise SelectionUnavailable(category)
    if payload[:1] != b"O":
        raise SelectionUnavailable()
    try:
        text = payload[1:].decode("utf-8", errors="strict")
    except UnicodeError:
        raise SelectionUnavailable() from None
    if len(text) > max_chars:
        raise SelectionUnavailable("too_long")
    if not text.strip():
        raise SelectionUnavailable()
    return text


def get_selected_text(target_hwnd: int, *, active=None, timeout=2.0,
                      max_chars=20000) -> str:
    """Return only current selected text, or raise a fixed, non-sensitive error.

    ``active`` returns false to cancel and may raise a caller-defined exception.
    The timeout includes process startup. All exits reap the short-lived worker.
    """
    if (not isinstance(target_hwnd, int) or isinstance(target_hwnd, bool)
            or target_hwnd <= 0 or not isinstance(max_chars, int)
            or isinstance(max_chars, bool) or not 0 < max_chars < 2 ** 30
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("Invalid selection read limits or target window.")
    _check_request(target_hwnd, active)
    deadline = time.monotonic() + timeout
    process = None
    try:
        try:
            process = _start_worker(target_hwnd, max_chars)
        except OSError:
            raise SelectionUnavailable() from None
        return _wait_result(process, target_hwnd, active, deadline, max_chars)
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait()
            finally:
                process.stdout.close()


# Native interface layout/signatures verified against Microsoft's SDK header:
# https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/
# RecompiledIdlHeaders/um/UIAutomationClient.h
# Slots include IUnknown (QueryInterface/AddRef/Release at 0/1/2).
_P = ctypes.c_void_p
_I = ctypes.c_int
_PP = ctypes.POINTER(_P)


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_ulong), ("data2", ctypes.c_ushort),
                ("data3", ctypes.c_ushort), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _Automation:
    """Only the minimal read-only COM surface, used exclusively by the child."""

    def __init__(self):
        self.pointers = []
        self.ole32 = ctypes.WinDLL("ole32")
        self.oleaut32 = ctypes.WinDLL("oleaut32")
        self.ole32.CoInitializeEx.argtypes = [_P, ctypes.c_ulong]
        self.ole32.CoInitializeEx.restype = ctypes.c_long
        self.ole32.CoUninitialize.argtypes = []
        self.ole32.CoUninitialize.restype = None
        self.ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), _P,
                                               ctypes.c_ulong, ctypes.POINTER(_GUID), _PP]
        self.ole32.CoCreateInstance.restype = ctypes.c_long
        self.oleaut32.SysStringLen.argtypes = [_P]
        self.oleaut32.SysStringLen.restype = ctypes.c_uint
        self.oleaut32.SysFreeString.argtypes = [_P]
        self.oleaut32.SysFreeString.restype = None
        if self.ole32.CoInitializeEx(None, 0) < 0:  # COINIT_MULTITHREADED
            raise SelectionUnavailable()
        try:
            clsid = _GUID.parse("ff48dba4-60ef-4201-aa87-54103eef594e")
            iid = _GUID.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")
            self.root = self._receive(lambda out: self.ole32.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid), out))
            self.walker = self.pointer(self.root, 16)  # RawViewWalker
        except Exception:
            self.close()
            raise

    @staticmethod
    def call(pointer, slot, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(_P))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, _P, *types)(table[slot])
        result = method(pointer, *args)
        if result & 0xffffffff == 0x80131505:  # UIA_E_TIMEOUT
            raise SelectionUnavailable("timeout")
        return result

    def _receive(self, function, *, optional=False):
        pointer = _P()
        result = function(ctypes.byref(pointer))
        if pointer:
            self.pointers.append(pointer)
        if result < 0 or not pointer:
            if optional and (result >= 0 or result & 0xffffffff in
                             (0x80004002, 0x80040204)):  # Unsupported pattern.
                return None
            raise SelectionUnavailable()
        return pointer

    def pointer(self, pointer, slot, types=(), args=(), *, optional=False):
        return self._receive(lambda out: self.call(pointer, slot, (*types, _PP),
                                                  *args, out), optional=optional)

    def integer(self, pointer, slot):
        result = _I()
        if self.call(pointer, slot, (ctypes.POINTER(_I),), ctypes.byref(result)) < 0:
            raise SelectionUnavailable()
        return result.value

    def focused(self):
        return self.pointer(self.root, 8)

    def from_handle(self, hwnd):
        return self.pointer(self.root, 6, (_P,), (hwnd,))

    def parent(self, element):
        return self.pointer(self.walker, 3, (_P,), (element,), optional=True)

    def same(self, first, second):
        result = _I()
        if self.call(self.root, 3, (_P, _P, ctypes.POINTER(_I)),
                     first, second, ctypes.byref(result)) < 0:
            raise SelectionUnavailable()
        return bool(result.value)

    def selection(self, element, max_chars):
        iid = _GUID.parse("32eba289-3583-42c9-9c59-3b6d9a1e9b6a")
        pattern = self.pointer(element, 14, (_I, ctypes.POINTER(_GUID)),
                               (10014, ctypes.byref(iid)), optional=True)
        if pattern is None:
            return None
        ranges = self.pointer(pattern, 5)  # TextPattern.GetSelection only.
        count = self.integer(ranges, 3)
        if not 0 < count <= 64:
            raise SelectionUnavailable()
        parts, length = [], 0
        for index in range(count):
            selected = self.pointer(ranges, 4, (_I,), (index,))
            # A caret is a degenerate range; it never authorizes document text.
            comparison = _I()
            if self.call(selected, 5, (_I, _P, _I, ctypes.POINTER(_I)),
                         0, selected, 1, ctypes.byref(comparison)) < 0:
                raise SelectionUnavailable()
            if comparison.value == 0:
                continue
            text_pointer = _P()
            try:
                # UIA counts UTF-16 units. This bound can detect a Python-char
                # overflow even when all selected characters are surrogate pairs.
                if self.call(selected, 12, (_I, _PP), max_chars * 2 + 1,
                             ctypes.byref(text_pointer)) < 0:
                    raise SelectionUnavailable()
                size = self.oleaut32.SysStringLen(text_pointer) if text_pointer else 0
                if size > max_chars * 2:
                    raise SelectionUnavailable("too_long")
                text = ctypes.wstring_at(text_pointer, size) if size else ""
            finally:
                if text_pointer:
                    self.oleaut32.SysFreeString(text_pointer)
            if text:
                length += len(text) + bool(parts)
                if length > max_chars:
                    raise SelectionUnavailable("too_long")
                parts.append(text)
        text = "\n".join(parts)
        if not text.strip():
            raise SelectionUnavailable()
        return text

    def close(self):
        try:
            for pointer in reversed(self.pointers):
                self.call(pointer, 2, ())
            self.pointers.clear()
        finally:
            self.ole32.CoUninitialize()


def _read_selection(api, target_hwnd, max_chars):
    _check_request(target_hwnd, None)
    target, focus = api.from_handle(target_hwnd), api.focused()
    if (not api.integer(focus, 26) or api.integer(focus, 35)
            or not api.integer(focus, 28)):
        raise SelectionUnavailable()
    # Prove ancestry before reading ANY text. Walk only this focus's ancestors,
    # never siblings/descendants/desktop; only its first 3 parents are candidates.
    candidates, current = [], focus
    for depth in range(17):
        if api.integer(current, 35):
            raise SelectionUnavailable()
        if depth <= 3:
            candidates.append(current)
        if api.same(current, target):
            break
        current = api.parent(current)
        if current is None:
            raise SelectionUnavailable()
    else:
        raise SelectionUnavailable()
    for index, element in enumerate(candidates):
        _check_request(target_hwnd, None)
        # Parents must be a document/edit text container, not arbitrary windows.
        if index and api.integer(element, 21) not in (50004, 50030):
            continue
        if not api.integer(element, 28):
            raise SelectionUnavailable()
        text = api.selection(element, max_chars)
        if text is not None:
            _check_request(target_hwnd, None)
            if not api.same(focus, api.focused()):
                raise SelectionCancelled()
            return text
    raise SelectionUnavailable()


def _worker_main(target_hwnd, max_chars):
    api = None
    try:
        api = _Automation()
        text = _read_selection(api, target_hwnd, max_chars)
        payload = b"O" + text.encode("utf-8", errors="strict")
    except SelectionCancelled:
        payload = b"Ecancelled"
    except SelectionUnavailable as error:
        code = error.code if error.code in {"too_long", "timeout"} else "unavailable"
        payload = b"E" + code.encode("ascii")
    except Exception:
        payload = b"Eunavailable"
    finally:
        if api is not None:
            api.close()
    # Only this anonymous pipe carries the text; stderr is discarded by parent.
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        _worker_main(int(sys.argv[2]), int(sys.argv[3]))
