"""Task coordination without constructing windows or loading a model."""
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, patch
import queue
import unittest

from reader_app import App
from export_estimate import analyze_text, select_sample, Calibration


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
        app.export_button = Mock()
        app.estimate_button = Mock()
        app.pause_button = Mock()
        app.show_error = Mock()
        return app

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


if __name__ == "__main__":
    unittest.main()
