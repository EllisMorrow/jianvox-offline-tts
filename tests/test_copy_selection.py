import ctypes as C
from contextlib import ExitStack
import unittest
from unittest.mock import patch
import win_native as native


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("win_native.time.monotonic", side_effect=lambda: self.clock))
        self.stack.enter_context(patch("win_native.time.sleep", side_effect=self.advance))
        self.foreground = self.stack.enter_context(patch.object(native.U, "GetForegroundWindow", return_value=101))
        self.stack.enter_context(patch.object(native.U, "GetAsyncKeyState", return_value=0))
        self.sequence = self.stack.enter_context(patch.object(native.U, "GetClipboardSequenceNumber", side_effect=[20, 21]))
        self.formats = self.stack.enter_context(patch.object(native.U, "IsClipboardFormatAvailable", return_value=True))
        self.send = self.stack.enter_context(patch.object(native.U, "SendInput", return_value=4))
        self.read = self.stack.enter_context(patch("win_native.clipboard_text", return_value="新复制的公开测试文字"))

    def advance(self, seconds):
        self.clock += seconds

    def test_settling_delay_precedes_sequence_snapshot_and_copy(self):
        observed = []
        def sequence():
            observed.append(self.clock)
            return 20 if len(observed) == 1 else 21
        self.sequence.side_effect = sequence
        self.assertEqual(native.copy_selection(), "新复制的公开测试文字")
        self.assertGreaterEqual(observed[0], .3)
        self.read.assert_called_once_with(clear_after=True, expected_sequence=21, active=None)

    def test_only_one_retry_when_first_copy_produces_no_new_sequence(self):
        self.sequence.side_effect = lambda: 22 if self.send.call_count == 2 else 20
        self.assertEqual(native.copy_selection(), "新复制的公开测试文字")
        self.assertEqual(self.send.call_count, 2)
        self.assertGreaterEqual(self.clock, 2.1)

    def test_failed_copy_never_reads_stale_clipboard_and_retry_is_bounded(self):
        self.sequence.side_effect = None
        self.sequence.return_value = 20
        with self.assertRaisesRegex(ValueError, "未复制到新文字"):
            native.copy_selection()
        self.assertEqual(self.send.call_count, 2)
        self.read.assert_not_called()

    def test_window_change_before_copy_aborts_without_input(self):
        self.foreground.side_effect = [101, 102]
        with self.assertRaisesRegex(ValueError, "切换了窗口"):
            native.copy_selection()
        self.send.assert_not_called()
        self.read.assert_not_called()

    def test_stop_during_settling_aborts_without_input(self):
        with self.assertRaises(native.CopyCancelled):
            native.copy_selection(active=lambda: False)
        self.send.assert_not_called()
        self.read.assert_not_called()

    def test_new_clipboard_waits_for_text_format(self):
        self.sequence.side_effect = [20, 21, 21]
        self.formats.side_effect = [False, True]
        self.assertEqual(native.copy_selection(), "新复制的公开测试文字")
        self.send.assert_called_once()

    def test_nontext_copy_does_not_inject_another_copy(self):
        self.sequence.side_effect = lambda: 21 if self.send.called else 20
        self.formats.return_value = False
        with self.assertRaisesRegex(ValueError, "没有可朗读"):
            native.copy_selection()
        self.send.assert_called_once()
        self.read.assert_not_called()

    def test_deferred_selection_returns_snapshot_without_consuming_clipboard(self):
        self.read.return_value = ("新复制的公开测试文字", 21)
        self.assertEqual(native.copy_selection(defer_clear=True),
                         ("新复制的公开测试文字", 21))
        self.read.assert_called_once_with(expected_sequence=21, active=None, with_sequence=True)


class ClipboardReadTests(unittest.TestCase):
    def test_snapshot_read_returns_sequence_without_clearing_clipboard(self):
        text = C.create_unicode_buffer("公开快照文字")
        with patch.object(native.U, "OpenClipboard", return_value=True), \
             patch.object(native.U, "CloseClipboard") as close, \
             patch.object(native.U, "IsClipboardFormatAvailable", return_value=True), \
             patch.object(native.U, "GetClipboardData", return_value=1), \
             patch.object(native.U, "GetClipboardSequenceNumber", return_value=37), \
             patch.object(native.K, "GlobalLock", return_value=C.addressof(text)), \
             patch.object(native.K, "GlobalUnlock") as unlock, \
             patch.object(native.U, "EmptyClipboard") as clear:
            self.assertEqual(native.clipboard_text(with_sequence=True), ("公开快照文字", 37))
        clear.assert_not_called()
        unlock.assert_called_once_with(1)
        close.assert_called_once()

    def test_consuming_changed_snapshot_preserves_new_clipboard(self):
        with patch.object(native.U, "OpenClipboard", return_value=True), \
             patch.object(native.U, "CloseClipboard") as close, \
             patch.object(native.U, "GetClipboardSequenceNumber", return_value=38), \
             patch.object(native.U, "EmptyClipboard") as clear:
            self.assertFalse(native.clear_clipboard_if_unchanged(37))
        clear.assert_not_called()
        close.assert_called_once()

    def test_consuming_same_snapshot_clears_only_that_version(self):
        with patch.object(native.U, "OpenClipboard", return_value=True), \
             patch.object(native.U, "CloseClipboard") as close, \
             patch.object(native.U, "GetClipboardSequenceNumber", return_value=37), \
             patch.object(native.U, "EmptyClipboard", return_value=True) as clear:
            self.assertTrue(native.clear_clipboard_if_unchanged(37))
        clear.assert_called_once()
        close.assert_called_once()

    def test_consuming_snapshot_reports_clear_failure_and_releases_lock(self):
        with patch.object(native.U, "OpenClipboard", return_value=True), \
             patch.object(native.U, "CloseClipboard") as close, \
             patch.object(native.U, "GetClipboardSequenceNumber", return_value=37), \
             patch.object(native.U, "EmptyClipboard", return_value=False):
            with self.assertRaisesRegex(ValueError, "未能自动清空"):
                native.clear_clipboard_if_unchanged(37)
        close.assert_called_once()

    def test_sequence_change_before_lock_does_not_read_or_clear_clipboard(self):
        with patch.object(native.U, "OpenClipboard", return_value=True), \
             patch.object(native.U, "CloseClipboard") as close, \
             patch.object(native.U, "GetClipboardSequenceNumber", return_value=22), \
             patch.object(native.U, "GetClipboardData") as get_data, \
             patch.object(native.U, "EmptyClipboard") as clear:
            with self.assertRaisesRegex(ValueError, "读取前被其他程序更新"):
                native.clipboard_text(True, expected_sequence=21)
        get_data.assert_not_called()
        clear.assert_not_called()
        close.assert_called_once()

    def test_cancel_during_read_does_not_clear_clipboard(self):
        text = C.create_unicode_buffer("公开测试文字")
        checks = iter([True, False])
        with patch.object(native.U, "OpenClipboard", return_value=True), \
             patch.object(native.U, "CloseClipboard"), \
             patch.object(native.U, "IsClipboardFormatAvailable", return_value=True), \
             patch.object(native.U, "GetClipboardData", return_value=1), \
             patch.object(native.K, "GlobalLock", return_value=C.addressof(text)), \
             patch.object(native.K, "GlobalUnlock"), \
             patch.object(native.U, "EmptyClipboard") as clear:
            with self.assertRaises(native.CopyCancelled):
                native.clipboard_text(True, active=lambda: next(checks))
        clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
