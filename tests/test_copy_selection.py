import ctypes as C
from contextlib import ExitStack
import unittest
from unittest.mock import ANY, patch
import win_native as native


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("win_native.time.monotonic", side_effect=lambda: self.clock))
        self.stack.enter_context(patch("win_native.time.sleep", side_effect=self.advance))
        self.foreground = self.stack.enter_context(patch.object(native.U, "GetForegroundWindow", return_value=101))
        self.open_clipboard = self.stack.enter_context(patch.object(native.U, "OpenClipboard", return_value=True))
        self.close_clipboard = self.stack.enter_context(patch.object(native.U, "CloseClipboard", return_value=True))
        self.keys = self.stack.enter_context(patch.object(native.U, "GetAsyncKeyState", return_value=0))
        self.sequence = self.stack.enter_context(patch.object(native.U, "GetClipboardSequenceNumber", side_effect=[20, 21]))
        self.formats = self.stack.enter_context(patch.object(native.U, "IsClipboardFormatAvailable", return_value=True))
        self.send = self.stack.enter_context(patch.object(native.U, "SendInput", return_value=4))
        self.read = self.stack.enter_context(patch("win_native.clipboard_text", return_value="新复制的公开测试文字"))
        self.uia = self.stack.enter_context(patch("win_native.get_selected_text", return_value="直接取得的公开选中文字"))

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
        self.read.assert_called_once_with(clear_after=True, expected_sequence=21, active=ANY,
                                          with_sequence=False, wait=False)
        self.uia.assert_not_called()

    def test_no_new_copy_switches_to_uia_without_another_ctrl_c(self):
        self.sequence.side_effect = None
        self.sequence.return_value = 20
        self.assertEqual(native.copy_selection(), "直接取得的公开选中文字")
        self.assertEqual(self.send.call_count, 1)
        self.uia.assert_called_once()
        self.assertLess(self.clock, 1.9)

    def test_both_channels_failed_never_reads_stale_clipboard(self):
        self.sequence.side_effect = None
        self.sequence.return_value = 20
        self.uia.side_effect = native.SelectionUnavailable("当前程序没有可读取的选中文字。")
        with self.assertRaisesRegex(ValueError, "直接取字也未成功"):
            native.copy_selection()
        self.assertEqual(self.send.call_count, 1)
        self.uia.assert_called_once()
        self.read.assert_not_called()

    def test_busy_clipboard_switches_without_copy_or_lock_retries(self):
        self.open_clipboard.return_value = False
        self.assertEqual(native.copy_selection(defer_clear=True), ("直接取得的公开选中文字", None))
        self.assertAlmostEqual(self.clock, .3)
        self.open_clipboard.assert_called_once()
        self.close_clipboard.assert_not_called()
        self.send.assert_not_called()
        self.sequence.assert_not_called()
        self.read.assert_not_called()
        self.uia.assert_called_once()

    def test_read_lock_failure_switches_immediately_after_single_copy(self):
        self.read.side_effect = native.CopyUnavailable("剪贴板暂时被其他程序占用。", code="busy")
        self.assertEqual(native.copy_selection(defer_clear=True), ("直接取得的公开选中文字", None))
        self.assertAlmostEqual(self.clock, .3)
        self.send.assert_called_once()
        self.read.assert_called_once()
        self.uia.assert_called_once()
        self.open_clipboard.assert_called_once()

    def test_copy_injection_failure_switches_without_waiting_for_clipboard(self):
        self.send.return_value = 0
        self.assertEqual(native.copy_selection(), "直接取得的公开选中文字")
        self.assertAlmostEqual(self.clock, .3)
        self.send.assert_called_once()
        self.read.assert_not_called()
        self.uia.assert_called_once()

    def test_clipboard_data_failure_switches_to_uia(self):
        self.read.side_effect = OSError("unavailable")
        self.assertEqual(native.copy_selection(), "直接取得的公开选中文字")
        self.uia.assert_called_once()

    def test_empty_copy_switches_to_selected_text(self):
        self.read.return_value = ("  ", 21)
        self.assertEqual(native.copy_selection(defer_clear=True), ("直接取得的公开选中文字", None))
        self.uia.assert_called_once()

    def test_window_change_before_copy_aborts_without_input(self):
        self.foreground.side_effect = [101, 102]
        with self.assertRaisesRegex(ValueError, "切换了窗口"):
            native.copy_selection()
        self.send.assert_not_called()
        self.read.assert_not_called()
        self.uia.assert_not_called()

    def test_stop_during_settling_aborts_without_input(self):
        with self.assertRaises(native.CopyCancelled):
            native.copy_selection(active=lambda: False)
        self.send.assert_not_called()
        self.read.assert_not_called()
        self.uia.assert_not_called()

    def test_new_clipboard_waits_for_text_format(self):
        self.sequence.side_effect = [20, 21, 21]
        self.formats.side_effect = [False, True]
        self.assertEqual(native.copy_selection(), "新复制的公开测试文字")
        self.send.assert_called_once()

    def test_nontext_copy_does_not_inject_another_copy(self):
        self.sequence.side_effect = lambda: 21 if self.send.called else 20
        self.formats.return_value = False
        self.assertEqual(native.copy_selection(), "直接取得的公开选中文字")
        self.send.assert_called_once()
        self.read.assert_not_called()
        self.uia.assert_called_once()

    def test_deferred_selection_returns_snapshot_without_consuming_clipboard(self):
        self.read.return_value = ("新复制的公开测试文字", 21)
        self.assertEqual(native.copy_selection(defer_clear=True),
                         ("新复制的公开测试文字", 21))
        self.read.assert_called_once_with(clear_after=False, expected_sequence=21, active=ANY,
                                          with_sequence=True, wait=False)
        self.uia.assert_not_called()

    def test_cancel_during_fallback_never_returns_text(self):
        self.open_clipboard.return_value = False
        self.uia.side_effect = native.SelectionCancelled()
        with self.assertRaises(native.CopyCancelled):
            native.copy_selection()
        self.send.assert_not_called()

    def test_late_uia_result_after_window_switch_is_rejected(self):
        self.open_clipboard.return_value = False
        def switch_window(*args, **kwargs):
            self.foreground.return_value = 102
            return "不应朗读的迟到结果"
        self.uia.side_effect = switch_window
        with self.assertRaisesRegex(ValueError, "切换了窗口"):
            native.copy_selection()

    def test_busy_probe_then_uia_failure_recovers_only_fresh_snapshot(self):
        self.open_clipboard.side_effect = [False, False, True]
        self.uia.side_effect = native.SelectionUnavailable()
        self.read.return_value = ("补偿复制的新文字", 21)
        self.assertEqual(native.copy_selection(defer_clear=True), ("补偿复制的新文字", 21))
        self.assertEqual(self.open_clipboard.call_count, 3)
        self.close_clipboard.assert_called_once()
        self.send.assert_called_once()
        self.uia.assert_called_once()
        self.assertLessEqual(self.clock, .3 + native.COPY_RECOVERY_SECONDS)
        self.read.assert_called_once_with(clear_after=False, expected_sequence=21,
                                          active=ANY, with_sequence=True, wait=False)

    def test_busy_read_recovery_never_reuses_the_original_copy(self):
        self.sequence.side_effect = [20, 21, 21, 22]
        self.uia.side_effect = native.SelectionUnavailable()
        self.read.side_effect = [native.CopyUnavailable(code="busy"), ("补偿新文字", 22)]
        self.assertEqual(native.copy_selection(defer_clear=True), ("补偿新文字", 22))
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.read.call_args_list[1].kwargs["expected_sequence"], 22)
        self.uia.assert_called_once()

    def test_persistent_contention_has_a_single_short_total_budget(self):
        self.open_clipboard.return_value = False
        self.uia.side_effect = native.SelectionUnavailable("timeout")
        with self.assertRaisesRegex(ValueError, "剪贴板被占用.*辅助功能读取超时.*补偿复制未成功"):
            native.copy_selection()
        self.assertAlmostEqual(self.clock, .3 + native.COPY_RECOVERY_SECONDS)
        self.send.assert_not_called()
        self.read.assert_not_called()
        self.sequence.assert_not_called()
        self.uia.assert_called_once()

    def test_recovery_without_new_sequence_cannot_read_or_retry(self):
        self.open_clipboard.side_effect = [False, True]
        self.uia.side_effect = native.SelectionUnavailable()
        self.sequence.side_effect = None
        self.sequence.return_value = 20
        with self.assertRaisesRegex(ValueError, "补偿复制未成功（未复制到新文字）"):
            native.copy_selection()
        self.assertAlmostEqual(self.clock, .3 + native.COPY_RECOVERY_SECONDS)
        self.send.assert_called_once()
        self.read.assert_not_called()
        self.uia.assert_called_once()

    def test_recovery_read_contention_never_injects_a_third_copy(self):
        self.sequence.side_effect = [20, 21, 21, 22]
        self.read.side_effect = native.CopyUnavailable(code="busy")
        self.uia.side_effect = native.SelectionUnavailable()
        with self.assertRaisesRegex(ValueError, "补偿复制未成功（剪贴板被占用）"):
            native.copy_selection()
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.read.call_count, 2)
        self.uia.assert_called_once()

    def test_other_primary_failures_never_allow_compensation(self):
        for error in [native.CopyUnavailable(code="no_new_text"),
                      native.CopyUnavailable(code="no_text"),
                      native.CopyUnavailable(code="changed"),
                      native.CopyUnavailable(code="unsupported"),
                      native.CopyUnavailable(code="too_long"),
                      native.CopyUnavailable("private provider contents"),
                      OSError(5, "private OS details")]:
            with self.subTest(error=type(error).__name__, code=getattr(error, "code", None)):
                self.open_clipboard.reset_mock()
                self.send.reset_mock()
                self.sequence.side_effect = [20, 21]
                self.read.side_effect = error
                self.uia.side_effect = native.SelectionUnavailable()
                with self.assertRaises(ValueError) as caught:
                    native.copy_selection()
                self.assertNotIn("private", str(caught.exception))
                self.open_clipboard.assert_called_once()
                self.send.assert_called_once()

    def test_injection_failure_does_not_allow_compensation(self):
        self.sequence.side_effect = None
        self.sequence.return_value = 20
        self.send.return_value = 0
        self.uia.side_effect = native.SelectionUnavailable()
        with self.assertRaisesRegex(ValueError, "无法向当前程序发送复制操作"):
            native.copy_selection()
        self.open_clipboard.assert_called_once()
        self.send.assert_called_once()

    def test_uia_long_selection_or_unsupported_does_not_allow_compensation(self):
        for reason in ["too_long", "unsupported"]:
            with self.subTest(reason=reason):
                self.open_clipboard.reset_mock()
                self.open_clipboard.return_value = False
                self.uia.side_effect = native.SelectionUnavailable(reason)
                with self.assertRaises(ValueError):
                    native.copy_selection()
                self.open_clipboard.assert_called_once()
                self.send.assert_not_called()

    def test_stop_or_focus_change_during_recovery_prevents_input(self):
        for change_window in [False, True]:
            with self.subTest(change_window=change_window):
                self.foreground.return_value = 101
                self.open_clipboard.reset_mock()
                self.open_clipboard.return_value = False
                self.uia.side_effect = native.SelectionUnavailable()
                stopped = False
                def sleep(seconds):
                    nonlocal stopped
                    self.advance(seconds)
                    if self.open_clipboard.call_count >= 2:
                        stopped = True
                        if change_window:
                            self.foreground.return_value = 102
                with patch("win_native.time.sleep", side_effect=sleep):
                    expected = ValueError if change_window else native.CopyCancelled
                    with self.assertRaises(expected):
                        native.copy_selection(active=lambda: change_window or not stopped)
                self.send.assert_not_called()
                self.read.assert_not_called()

    def test_recovery_waits_for_keys_without_copying_with_them_held(self):
        self.open_clipboard.side_effect = [False, True]
        def uia(*args, **kwargs):
            self.keys.return_value = 0x8000
            raise native.SelectionUnavailable()
        self.uia.side_effect = uia
        with self.assertRaisesRegex(ValueError, "补偿复制未成功"):
            native.copy_selection()
        self.open_clipboard.assert_called_once()
        self.send.assert_not_called()
        self.read.assert_not_called()

    def test_focus_change_when_recovery_opens_lock_releases_it_without_copying(self):
        self.uia.side_effect = native.SelectionUnavailable()
        def open_clipboard(*args):
            if self.open_clipboard.call_count == 1:
                return False
            self.foreground.return_value = 102
            return True
        self.open_clipboard.side_effect = open_clipboard
        with self.assertRaisesRegex(ValueError, "切换了窗口"):
            native.copy_selection()
        self.close_clipboard.assert_called_once()
        self.sequence.assert_not_called()
        self.send.assert_not_called()
        self.read.assert_not_called()

    def test_read_uses_foreground_guard_even_after_clipboard_opened(self):
        def read(**kwargs):
            self.foreground.return_value = 102
            kwargs["active"]()
            self.fail("A changed foreground must reject the read")
        self.read.side_effect = read
        with self.assertRaisesRegex(ValueError, "切换了窗口"):
            native.copy_selection()
        self.uia.assert_not_called()


class ClipboardReadTests(unittest.TestCase):
    def test_nonwaiting_read_does_not_retry_or_touch_busy_clipboard(self):
        with patch.object(native.U, "OpenClipboard", return_value=False) as open_clipboard, \
             patch.object(native.U, "CloseClipboard") as close, \
             patch.object(native.U, "GetClipboardData") as read, \
             patch("win_native.time.sleep") as sleep:
            with self.assertRaises(native.CopyUnavailable) as caught:
                native.clipboard_text(wait=False)
        self.assertEqual(caught.exception.code, "busy")
        open_clipboard.assert_called_once()
        close.assert_not_called()
        read.assert_not_called()
        sleep.assert_not_called()

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

    def test_cancellation_after_open_or_data_lock_never_reads_or_clears(self):
        for stage in ["OpenClipboard", "GlobalLock"]:
            with self.subTest(stage=stage), ExitStack() as stack:
                active = True
                def cancel(*args):
                    nonlocal active
                    active = False
                    return 1
                stack.enter_context(patch.object(native.U, "OpenClipboard", return_value=True,
                    side_effect=cancel if stage == "OpenClipboard" else None))
                close = stack.enter_context(patch.object(native.U, "CloseClipboard"))
                stack.enter_context(patch.object(native.U, "IsClipboardFormatAvailable", return_value=True))
                get_data = stack.enter_context(patch.object(native.U, "GetClipboardData", return_value=1))
                stack.enter_context(patch.object(native.K, "GlobalLock", side_effect=cancel))
                unlock = stack.enter_context(patch.object(native.K, "GlobalUnlock"))
                read = stack.enter_context(patch("win_native.C.wstring_at"))
                clear = stack.enter_context(patch.object(native.U, "EmptyClipboard"))
                with self.assertRaises(native.CopyCancelled):
                    native.clipboard_text(True, active=lambda: active)
                read.assert_not_called()
                clear.assert_not_called()
                close.assert_called_once()
                if stage == "OpenClipboard":
                    get_data.assert_not_called()
                    unlock.assert_not_called()
                else:
                    unlock.assert_called_once_with(1)

    def test_cancel_after_sequence_snapshot_preserves_clipboard(self):
        text = C.create_unicode_buffer("公开测试文字")
        active = True
        def snapshot():
            nonlocal active
            active = False
            return 37
        with patch.object(native.U, "OpenClipboard", return_value=True), \
             patch.object(native.U, "CloseClipboard") as close, \
             patch.object(native.U, "IsClipboardFormatAvailable", return_value=True), \
             patch.object(native.U, "GetClipboardData", return_value=1), \
             patch.object(native.K, "GlobalLock", return_value=C.addressof(text)), \
             patch.object(native.K, "GlobalUnlock"), \
             patch.object(native.U, "GetClipboardSequenceNumber", side_effect=snapshot), \
             patch.object(native.U, "EmptyClipboard") as clear:
            with self.assertRaises(native.CopyCancelled):
                native.clipboard_text(True, active=lambda: active, with_sequence=True)
        clear.assert_not_called()
        close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
