"""Selection supervision uses real isolated pipes, never the user's UI/clipboard."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import selection_uia as uia


@unittest.skipUnless(os.name == "nt", "Windows anonymous-pipe supervision")
class SelectionProcessTests(unittest.TestCase):
    def setUp(self):
        self.foreground = patch.object(uia, "_foreground_window", return_value=123)
        self.foreground.start()
        self.addCleanup(self.foreground.stop)
        self.processes = []

    def worker(self, script):
        def start(*args):
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, close_fds=True, bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.processes.append(process)
            return process
        return patch.object(uia, "_start_worker", side_effect=start)

    def assert_reaped(self):
        self.assertTrue(self.processes)
        for process in self.processes:
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdout.closed)

    def test_success_drains_large_unicode_payload_and_reaps_child(self):
        text = "公开样本😀" * 3000
        with self.worker("import sys; sys.stdout.buffer.write(b'O' + "
                         "('公开样本😀' * 3000).encode('utf-8'))"):
            self.assertEqual(uia.get_selected_text(123, timeout=3), text)
        self.assert_reaped()

    def test_hung_provider_is_killed_on_timeout(self):
        started = time.monotonic()
        with self.worker("import time; time.sleep(30)"):
            with self.assertRaises(uia.SelectionUnavailable) as caught:
                uia.get_selected_text(123, timeout=.15)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertLess(time.monotonic() - started, 2)
        self.assert_reaped()

    def test_partial_response_cannot_bypass_timeout(self):
        with self.worker("import sys,time; sys.stdout.buffer.write(b'Opending'); "
                         "sys.stdout.buffer.flush(); time.sleep(30)"):
            with self.assertRaises(uia.SelectionUnavailable) as caught:
                uia.get_selected_text(123, timeout=.15)
        self.assertEqual(caught.exception.code, "timeout")
        self.assert_reaped()

    def test_cancellation_kills_and_reaps_running_child(self):
        deadline = time.monotonic() + .15
        with self.worker("import time; time.sleep(30)"):
            with self.assertRaises(uia.SelectionCancelled):
                uia.get_selected_text(123, active=lambda: time.monotonic() < deadline)
        self.assert_reaped()

    def test_caller_guard_exception_is_preserved_and_worker_reaped(self):
        class CallerCancellation(ValueError):
            pass
        deadline = time.monotonic() + .15

        def guard():
            if time.monotonic() >= deadline:
                raise CallerCancellation("public test cancellation")
            return True

        with self.worker("import time; time.sleep(30)"):
            with self.assertRaises(CallerCancellation):
                uia.get_selected_text(123, active=guard)
        self.assert_reaped()

    def test_foreground_change_during_read_cancels_and_reaps(self):
        deadline = time.monotonic() + .15
        with patch.object(uia, "_foreground_window", side_effect=lambda:
                          123 if time.monotonic() < deadline else 456):
            with self.worker("import time; time.sleep(30)"):
                with self.assertRaises(uia.SelectionCancelled):
                    uia.get_selected_text(123)
        self.assert_reaped()

    def test_inactive_or_wrong_target_does_not_start_process(self):
        with patch.object(uia, "_start_worker") as start:
            with self.assertRaises(uia.SelectionCancelled):
                uia.get_selected_text(123, active=lambda: False)
            with self.assertRaises(uia.SelectionCancelled):
                uia.get_selected_text(456)
        start.assert_not_called()

    def test_crash_and_malformed_payloads_never_expose_details(self):
        for script in (
            "import os; os._exit(9)",
            "import sys; sys.stdout.buffer.write(b'Xprivate provider detail')",
            "import sys; sys.stdout.buffer.write(b'Eprivate provider detail')",
            "import sys; sys.stdout.buffer.write(b'O\\xff')",
            "import sys; sys.stdout.buffer.write(b'O  \\r\\n')",
        ):
            with self.subTest(script=script), self.worker(script):
                with self.assertRaises(uia.SelectionUnavailable) as caught:
                    uia.get_selected_text(123, timeout=3)
                self.assertNotIn("private", str(caught.exception))
        self.assert_reaped()

    def test_character_limit_is_not_silent_truncation(self):
        for script in (
            "import sys; sys.stdout.buffer.write(b'O12345')",
            "import sys; sys.stdout.buffer.write(b'O' + b'x' * 10000)",
            "import sys; sys.stdout.buffer.write(b'Etoo_long')",
        ):
            with self.subTest(script=script), self.worker(script):
                with self.assertRaises(uia.SelectionUnavailable) as caught:
                    uia.get_selected_text(123, max_chars=4, timeout=3)
                self.assertEqual(caught.exception.code, "too_long")
        self.assert_reaped()

    def test_worker_reported_cancellation_is_distinct(self):
        with self.worker("import sys; sys.stdout.buffer.write(b'Ecancelled')"):
            with self.assertRaises(uia.SelectionCancelled):
                uia.get_selected_text(123, timeout=3)
        self.assert_reaped()

    def test_provider_timeout_uses_fixed_timeout_category(self):
        with self.worker("import sys; sys.stdout.buffer.write(b'Etimeout')"):
            with self.assertRaises(uia.SelectionUnavailable) as caught:
                uia.get_selected_text(123, timeout=3)
        self.assertEqual(caught.exception.code, "timeout")
        self.assert_reaped()

    def test_start_failure_is_fixed_error(self):
        with patch.object(uia, "_start_worker", side_effect=OSError("private path")):
            with self.assertRaises(uia.SelectionUnavailable) as caught:
                uia.get_selected_text(123)
        self.assertNotIn("private", str(caught.exception))

    def test_worker_launch_is_hidden_isolated_and_has_no_text_arguments(self):
        with patch.object(uia.subprocess, "Popen") as start:
            uia._start_worker(123, 20000)
        args, kwargs = start.call_args
        self.assertEqual(args[0][1:4], ["-I", "-S", "-B"])
        self.assertEqual(args[0][-3:], ["--worker", "123", "20000"])
        self.assertEqual(Path(args[0][4]).name, "selection_uia.py")
        self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_pythonw_parent_launches_sibling_python_without_console(self):
        with patch.object(uia.sys, "executable", r"C:\runtime\pythonw.exe"):
            with patch.object(uia.subprocess, "Popen") as start:
                uia._start_worker(123, 20000)
        self.assertEqual(start.call_args.args[0][0], r"C:\runtime\python.exe")
        self.assertEqual(start.call_args.kwargs["creationflags"],
                         subprocess.CREATE_NO_WINDOW)


class FakeAutomation:
    def __init__(self, parents, selections, *, password=None, disabled=None,
                 focus_after="focus"):
        self.parents = parents
        self.selections = selections
        self.password = password
        self.disabled = disabled
        self.focus_after = focus_after
        self.focus_reads = 0
        self.queried = []
        self.parent_reads = []

    def from_handle(self, hwnd):
        return "target"

    def focused(self):
        self.focus_reads += 1
        return "focus" if self.focus_reads == 1 else self.focus_after

    def integer(self, element, slot):
        if slot == 26:
            return 1
        if slot == 35:
            return element == self.password
        if slot == 28:
            return element != self.disabled
        if slot == 21:
            return 50030 if element != "target" else 50032
        raise AssertionError("Unexpected property read")

    def parent(self, element):
        self.parent_reads.append(element)
        return self.parents.get(element)

    def same(self, first, second):
        return first == second

    def selection(self, element, max_chars):
        self.queried.append(element)
        result = self.selections.get(element)
        if isinstance(result, Exception):
            raise result
        return result


class SelectionScopeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(uia, "_foreground_window", return_value=123)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_focus_selection_is_preferred_to_parent(self):
        api = FakeAutomation({"focus": "document", "document": "target"},
                             {"focus": "selected", "document": "other"})
        self.assertEqual(uia._read_selection(api, 123, 20), "selected")
        self.assertEqual(api.queried, ["focus"])

    def test_unsupported_focus_can_use_nearest_document_parent(self):
        api = FakeAutomation({"focus": "document", "document": "target"},
                             {"document": "selected"})
        self.assertEqual(uia._read_selection(api, 123, 20), "selected")
        self.assertEqual(api.queried, ["focus", "document"])

    def test_empty_selection_does_not_expand_to_another_container(self):
        api = FakeAutomation({"focus": "document", "document": "target"},
                             {"focus": uia.SelectionUnavailable(), "document": "stale"})
        with self.assertRaises(uia.SelectionUnavailable):
            uia._read_selection(api, 123, 20)
        self.assertEqual(api.queried, ["focus"])

    def test_wrong_window_ancestry_rejected_before_any_text_read(self):
        api = FakeAutomation({"focus": "other"}, {"focus": "wrong"})
        with self.assertRaises(uia.SelectionUnavailable):
            uia._read_selection(api, 123, 20)
        self.assertEqual(api.queried, [])

    def test_password_ancestor_rejected_before_any_text_read(self):
        api = FakeAutomation({"focus": "document", "document": "target"},
                             {"focus": "wrong"}, password="document")
        with self.assertRaises(uia.SelectionUnavailable):
            uia._read_selection(api, 123, 20)
        self.assertEqual(api.queried, [])

    def test_password_and_disabled_focus_rejected_before_any_text_read(self):
        for options in ({"password": "focus"}, {"disabled": "focus"}):
            api = FakeAutomation({"focus": "target"}, {"focus": "wrong"}, **options)
            with self.subTest(options=options), self.assertRaises(uia.SelectionUnavailable):
                uia._read_selection(api, 123, 20)
            self.assertEqual(api.queried, [])

    def test_changed_focus_in_same_window_is_rejected(self):
        api = FakeAutomation({"focus": "target"}, {"focus": "stale"},
                             focus_after="other")
        with self.assertRaises(uia.SelectionCancelled):
            uia._read_selection(api, 123, 20)

    def test_text_candidates_are_limited_to_three_parents(self):
        api = FakeAutomation({"focus": "one", "one": "two", "two": "three",
                              "three": "four", "four": "target"},
                             {"four": "too distant"})
        with self.assertRaises(uia.SelectionUnavailable):
            uia._read_selection(api, 123, 20)
        self.assertEqual(api.queried, ["focus", "one", "two", "three"])

    def test_ancestry_walk_is_bounded_even_for_provider_cycle(self):
        api = FakeAutomation({"focus": "focus"}, {"focus": "wrong"})
        with self.assertRaises(uia.SelectionUnavailable):
            uia._read_selection(api, 123, 20)
        self.assertLessEqual(len(api.parent_reads), 17)
        self.assertEqual(api.queried, [])


@unittest.skipUnless(os.name == "nt" and "--native-smoke" in sys.argv,
                     "Opt in: test_selection_uia.py --native-smoke (own public window)")
class NativeSelectionSmokeTests(unittest.TestCase):
    def test_own_edit_selection_caret_limit_and_worker_startup(self):
        import ctypes
        from ctypes import wintypes
        import threading

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, ctypes.c_void_p]
        user32.CreateWindowExW.restype = wintypes.HWND
        for name in ("SetForegroundWindow", "DestroyWindow"):
            getattr(user32, name).argtypes = [wintypes.HWND]
            getattr(user32, name).restype = wintypes.BOOL
        user32.SetFocus.argtypes = [wintypes.HWND]
        user32.SetFocus.restype = wintypes.HWND
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = wintypes.LPARAM
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = wintypes.LPARAM
        ready, stop = threading.Event(), threading.Event()
        window = {}
        selected = "公开选择😀 sample"

        def own_window():
            hwnd = None
            try:
                rich_edit = ctypes.WinDLL("Msftedit")
                hwnd = user32.CreateWindowExW(0, "STATIC", "JianVox public UIA test",
                    0x10cf0000, 100, 100, 520, 150, None, None, None, None)
                edit = user32.CreateWindowExW(0, "RICHEDIT50W", "prefix " + selected + " suffix",
                    0x50000004, 10, 10, 480, 90, hwnd, None, None, None)
                if not hwnd or not edit:
                    raise RuntimeError("Could not create own test controls")
                user32.SetForegroundWindow(hwnd)
                user32.SetFocus(edit)
                units = len(selected.encode("utf-16-le")) // 2
                user32.SendMessageW(edit, 0x00b1, 7, 7 + units)  # EM_SETSEL
                window.update(hwnd=hwnd, edit=edit)
                ready.set()
                message = wintypes.MSG()
                while not stop.is_set():
                    while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        user32.TranslateMessage(ctypes.byref(message))
                        user32.DispatchMessageW(ctypes.byref(message))
                    stop.wait(.005)
            except Exception as error:
                window["error"] = error
                ready.set()
            finally:
                if hwnd:
                    user32.DestroyWindow(hwnd)

        thread = threading.Thread(target=own_window, daemon=True)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(timeout=2)))
        self.assertTrue(ready.wait(3))
        if "error" in window:
            raise window["error"]
        # Background test runners may not acquire foreground under Windows'
        # foreground lock. Probe only our known EDIT in a disposable process;
        # never relax the production reader's foreground/focus requirements.
        probe = """import sys
sys.path.insert(0, sys.argv[1])
import selection_uia as uia
api = None
try:
    api = uia._Automation()
    element = api.from_handle(int(sys.argv[2]))
    text = api.selection(element, int(sys.argv[3]))
    result = b'O' + text.encode('utf-8') if text is not None else b'Eunavailable'
except uia.SelectionUnavailable as error:
    result = b'Etoo_long' if error.code == 'too_long' else b'Eunavailable'
finally:
    if api is not None:
        api.close()
sys.stdout.buffer.write(result)
"""

        def read_own_edit(limit):
            executable = Path(sys.executable).with_name("python.exe")
            result = subprocess.run(
                [str(executable), "-I", "-S", "-B", "-c", probe,
                 str(Path(uia.__file__).parent), str(window["edit"]), str(limit)],
                capture_output=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, "Owned-control native probe failed")
            return result.stdout

        started = time.monotonic()
        self.assertEqual(read_own_edit(len(selected)), b"O" + selected.encode("utf-8"))
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(read_own_edit(3), b"Etoo_long")
        if uia._foreground_window() == window["hwnd"]:
            self.assertEqual(uia.get_selected_text(window["hwnd"]), selected)
        else:
            with self.assertRaises(uia.SelectionCancelled):
                uia.get_selected_text(window["hwnd"])
            # This also runs under pythonw: the real worker must use python.exe
            # with valid anonymous stdout, and safely reject a background target.
            process = uia._start_worker(window["hwnd"], 20000)
            try:
                output, _ = process.communicate(timeout=3)
                self.assertEqual(output, b"Ecancelled")
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
        user32.SendMessageW(window["edit"], 0x00b1, 7, 7)
        self.assertEqual(read_own_edit(20000), b"Eunavailable")


if __name__ == "__main__":
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    args = [sys.argv[0], "NativeSelectionSmokeTests", "-v"] if "--native-smoke" in sys.argv else None
    unittest.main(argv=args)
