"""Task coordination without constructing windows or loading a model."""
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, patch
import queue
import threading
import unittest

from reader_app import App
from reader_core import Reader, AudioBuffer
from export_estimate import analyze_text, select_sample, Calibration
from runtime_errors import RuntimeIssue


class WorkflowTests(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.reader = SimpleNamespace(ready=True, number=1, stop=Mock(),
                                     audio=SimpleNamespace(active=False))
        app.exporting = app.estimating = app.closed = False
        app.estimate_id = app.estimate_job = app.estimate_token = None
        app.events = queue.Queue()
        app.calibrations = OrderedDict()
        app.root = Mock()
        app.status = Mock()
        app.status_value = ""
        app.status.set.side_effect = lambda value: setattr(app, "status_value", value)
        app.status.get.side_effect = lambda: app.status_value
        app.export_button = Mock()
        app.estimate_button = Mock()
        app.pause_button = Mock()
        app.export_mode_menu = Mock()
        app.export_id = None
        app.export_chapters_saved = 0
        app.export_chaptered = False
        app.export_error = None
        app.editor_request = None
        app.ui = Mock()
        app.dot = Mock()
        app.diagnostic_text = Mock()
        app.last_issue = None
        app.show = Mock()
        app.clear_completed_text = Mock()
        app.show_error = Mock()
        return app

    def reader_without_worker(self, app):
        reader = Reader.__new__(Reader)
        reader.events = app.events
        reader.lock = threading.Lock()
        reader.condition = threading.Condition(reader.lock)
        reader.number, reader.pending = 1, None
        reader.closed, reader.ready = False, True
        reader.export_number = reader.probe_number = None
        reader.audio = AudioBuffer(app.events)
        app.reader = reader
        app.preferences = SimpleNamespace(voice="serena", style="", speed=1.0)
        app.text = Mock()
        app.text.get.return_value = ""
        app.save_playback = Mock()
        return reader

    def test_unready_clipboard_read_does_not_access_or_clear_content(self):
        app = self.app()
        app.reader.ready = False
        with patch("reader_app.clipboard_text") as clipboard, \
             patch("reader_app.clear_clipboard_if_unchanged") as clear:
            app.read_clipboard()
        clipboard.assert_not_called()
        clear.assert_not_called()
        app.show_error.assert_called_once()

    def test_oversize_submission_leaves_clipboard_untouched(self):
        app = self.app()
        reader = self.reader_without_worker(app)
        with patch("reader_app.clipboard_text", return_value=("字" * 20001, 17)) as clipboard, \
             patch("reader_app.clear_clipboard_if_unchanged") as clear:
            app.read_clipboard()
        clipboard.assert_called_once_with(with_sequence=True)
        clear.assert_not_called()
        self.assertIsNone(reader.pending)
        self.assertEqual(reader.number, 1)
        self.assertIn("20000", app.show_error.call_args.args[0])

    def test_clipboard_is_cleared_only_after_submission_accepts_snapshot(self):
        app = self.app()
        reader = self.reader_without_worker(app)
        def check_accepted(sequence):
            self.assertEqual(sequence, 42)
            self.assertIsNotNone(reader.pending)
            self.assertEqual(reader.pending.parts, ["公开的测试文字。"])
            self.assertEqual(reader.pending.number, 2)
            return True
        with patch("reader_app.clipboard_text", return_value=("公开的测试文字。", 42)), \
             patch("reader_app.clear_clipboard_if_unchanged", side_effect=check_accepted) as clear:
            app.read_clipboard()
        clear.assert_called_once_with(42)
        app.show_error.assert_not_called()

    def test_new_clipboard_after_acceptance_is_left_to_native_sequence_guard(self):
        app = self.app()
        reader = self.reader_without_worker(app)
        with patch("reader_app.clipboard_text", return_value=("原选中文字。", 23)), \
             patch("reader_app.clear_clipboard_if_unchanged", return_value=False) as clear:
            app.read_clipboard()
        clear.assert_called_once_with(23)
        self.assertEqual(reader.pending.parts, ["原选中文字。"])
        app.show_error.assert_not_called()

    def test_clear_failure_preserves_accepted_read_and_reports_safe_warning(self):
        app = self.app()
        reader = self.reader_without_worker(app)
        with patch("reader_app.clipboard_text", return_value=("公开测试。", 9)), \
             patch("reader_app.clear_clipboard_if_unchanged", side_effect=OSError("private path")):
            app.read_clipboard()
        self.assertEqual(reader.pending.parts, ["公开测试。"])
        self.assertIn("朗读已接受", app.show_error.call_args.args[0])
        self.assertNotIn("private path", app.show_error.call_args.args[0])

    def test_selected_text_also_clears_only_after_acceptance(self):
        app = self.app()
        reader = self.reader_without_worker(app)
        app.events.put(("selection_copied", ("选中文字。", 10.0, 1, 71)))
        with patch("reader_app.clear_clipboard_if_unchanged") as clear:
            app.poll()
        self.assertEqual(reader.pending.parts, ["选中文字。"])
        self.assertEqual(reader.pending.started, 10.0)
        clear.assert_called_once_with(71)
        app.show.assert_not_called()

    def test_selection_and_clipboard_do_not_interrupt_export(self):
        app = self.app()
        app.exporting = True
        with patch("reader_app.copy_selection") as copy, patch("reader_app.clipboard_text") as clipboard:
            app.selection(1)
            app.read_clipboard()
        app.reader.stop.assert_not_called()
        copy.assert_not_called()
        clipboard.assert_not_called()

    def test_estimation_refuses_playback_before_reading_editor(self):
        app = self.app()
        app.reader.audio.active = True
        app.text = Mock()
        app.estimate_export_time()
        app.text.get.assert_not_called()
        app.show_error.assert_called_once()

    def test_analysis_cancellation_ignores_stale_result(self):
        app = self.app()
        token = object()
        app.estimating, app.estimate_token = True, token
        app.stop()
        self.assertFalse(app.estimating)
        app.events.put(("estimate_prepared", (token, None, None, None, None, None)))
        app.poll()
        self.assertIsNone(app.estimate_id)

    def test_probe_completion_only_caches_numerical_analysis(self):
        app = self.app()
        sample = analyze_text("这是公开的校准样本文字。")
        whole = analyze_text("这是一段公开的较长测试文字。" * 10)
        key = ("serena", "", "zh")
        app.estimating, app.estimate_id = True, 5
        app.reader.number = 5
        app.estimate_job = (whole, sample, key, 1.1)
        app.show_estimate = Mock()
        app.events.put(("probe_done", (5, 3.0, 4.0)))
        app.poll()
        self.assertIsInstance(app.calibrations[key], Calibration)
        app.show_estimate.assert_called_once_with(whole, app.calibrations[key], 1.1)
        self.assertFalse(hasattr(app.calibrations[key], "text"))

    def test_stop_invalidates_probe_done_already_in_event_queue(self):
        app = self.app()
        app.estimating, app.estimate_id = True, 5
        app.reader.number = 5
        app.events.put(("probe_done", (5, 3.0, 4.0)))
        app.show_estimate = Mock()
        app.stop()
        app.poll()
        app.show_estimate.assert_not_called()
        self.assertFalse(app.calibrations)

    def test_cache_uses_sample_language_instead_of_article_language(self):
        app = self.app()
        token = object()
        text = "中文介绍。" * 20 + "English reading words. " * 200
        analysis, sample = analyze_text(text), select_sample(text)
        self.assertEqual(analysis.language_category, "en")
        self.assertEqual(sample.analysis.language_category, "zh")
        app.estimating, app.estimate_token = True, token
        app.reader.probe = Mock(return_value=9)
        app.events.put(("estimate_prepared", (token, analysis, sample, "serena", "", 1.0)))
        app.poll()
        self.assertEqual(app.estimate_job[2], ("serena", "", "zh"))

    def test_event_burst_yields_to_gui(self):
        app = self.app()
        for _ in range(300):
            app.events.put(("generating", None))
        app.poll()
        self.assertEqual(app.events.qsize(), 200)
        app.root.after.assert_called_once()

    def test_background_error_reports_status_without_revealing_window(self):
        app = self.app()
        del app.show_error
        app.dot = Mock()
        app.show = Mock()
        app.background_start = False  # Includes a window hidden after ordinary launch.
        app.show_error("本次复制未完成。")
        app.status.set.assert_called_once_with("本次复制未完成。")
        app.show.assert_not_called()

    def test_issue_and_device_events_keep_background_window_hidden(self):
        app = self.app()
        del app.show_error
        app.events.put(("issue", RuntimeIssue("worker_timeout", "声音生成超时。")))
        app.events.put(("audio_unavailable", "正在重新连接播放设备。"))
        app.events.put(("audio_reconnected", None))
        app.poll()
        app.show.assert_not_called()
        self.assertEqual(app.last_issue, "audio_unavailable")
        self.assertIn("播放设备已连接", app.status.get())
        self.assertIn("audio_unavailable", app.diagnostic_text.set.call_args.args[0])

    def test_chapter_saved_updates_count_without_marking_export_complete(self):
        app = self.app()
        app.exporting, app.export_chaptered, app.export_id = True, True, 8
        app.events.put(("chapter_saved", (8, 2, 5)))
        app.poll()
        self.assertEqual(app.export_chapters_saved, 2)
        self.assertTrue(app.exporting)
        self.assertEqual(app.export_id, 8)
        app.clear_completed_text.assert_not_called()
        app.export_button.configure.assert_not_called()

    def test_cancel_after_saved_chapter_stops_continuing_message_and_preserves_input(self):
        app = self.app()
        app.exporting, app.export_chaptered, app.export_id = True, True, 8
        app.editor_request = (8, "需要保留在窗口供重试的文字。")
        app.events.put(("chapter_saved", (8, 1, 3)))
        app.events.put(("export_cancelled", 8))
        app.poll()
        self.assertFalse(app.exporting)
        self.assertIsNone(app.export_id)
        self.assertIn("已取消", app.status.get())
        self.assertIn("1", app.status.get())
        self.assertIn("已保留", app.status.get())
        self.assertNotIn("正在继续", app.status.get())
        app.clear_completed_text.assert_not_called()

    def test_export_failure_retains_error_and_completed_chapter_count(self):
        app = self.app()
        del app.show_error
        app.exporting, app.export_chaptered, app.export_id = True, True, 8
        app.events.put(("chapter_saved", (8, 1, 3)))
        app.events.put(("issue", RuntimeIssue("disk_full", "磁盘空间不足。已完成的 1 个分章文件已保留。")))
        app.events.put(("export_cancelled", 8))
        app.poll()
        self.assertFalse(app.exporting)
        self.assertIn("磁盘空间不足", app.status.get())
        self.assertIn("已保留", app.status.get())
        app.clear_completed_text.assert_not_called()

    def test_old_error_does_not_override_a_later_normal_export_cancel(self):
        app = self.app()
        app.exporting, app.export_chaptered, app.export_id = True, True, 8
        app.last_issue = "disk_full"
        app.events.put(("chapter_saved", (8, 1, 3)))
        app.events.put(("export_cancelled", 8))
        app.poll()
        self.assertIn("已取消", app.status.get())
        self.assertIn("已保留", app.status.get())
        self.assertNotIn("空间不足", app.status.get())

    def test_chapter_export_done_reports_final_count_and_clears_completed_input(self):
        app = self.app()
        app.exporting, app.export_chaptered, app.export_id = True, True, 8
        app.events.put(("chapter_saved", (8, 3, 3)))
        app.events.put(("export_done", (8, "not-read-by-ui.mp3")))
        app.poll()
        self.assertFalse(app.exporting)
        self.assertIn("3 个分章 MP3", app.status.get())
        app.clear_completed_text.assert_called_once_with(8)

    def test_stale_chapter_events_do_not_finish_a_new_export(self):
        app = self.app()
        app.exporting, app.export_chaptered, app.export_id = True, True, 9
        app.events.put(("chapter_saved", (8, 3, 3)))
        app.events.put(("export_cancelled", 8))
        app.events.put(("export_done", (8, "not-read-by-ui.mp3")))
        app.poll()
        self.assertTrue(app.exporting)
        self.assertEqual(app.export_id, 9)
        self.assertEqual(app.export_chapters_saved, 0)
        app.clear_completed_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
