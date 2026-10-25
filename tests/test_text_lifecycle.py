"""Editor content survives tasks; only explicit clear/exit removes it."""
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import queue
import tempfile
import unittest

from reader_app import App


class Editor:
    def __init__(self, text):
        self.value = text
        self.undo_cleared = False

    def get(self, *_):
        return self.value + "\n"

    def delete(self, *_):
        self.value = ""

    def edit_reset(self):
        self.undo_cleared = True


class TextLifecycleTests(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.text = Editor("  保留这段文字。\nKeep this text.\n\n")
        app.reader = SimpleNamespace(ready=True, number=7, submit=Mock(return_value=7),
                                     stop=Mock(), close=Mock())
        app.preferences = SimpleNamespace(voice="serena", style="", speed=1, export_directory="")
        app.events = queue.Queue()
        app.root = Mock()
        app.status, app.dot, app.counter = Mock(), Mock(), Mock()
        app.pause_button = Mock()
        app.export_button, app.estimate_button, app.export_mode_menu = Mock(), Mock(), Mock()
        app.export_directory, app.export_progress, app.ui = Mock(), Mock(), Mock()
        app.export_mode = Mock()
        app.export_mode.get.return_value = "单个 MP3"
        app.save_playback, app.persist, app.finish_exit = Mock(), Mock(), Mock()
        app.exporting = app.estimating = app.closed = False
        app.export_id = None
        app.metrics = {}
        app.native = None
        app.calibrations = OrderedDict()
        return app

    def test_read_button_flow_keeps_exact_input_after_completion(self):
        app = self.app()
        source = app.text.value
        app.read(app.text.get("1.0", "end"))
        app.events.put(("played", (7, 0)))
        app.poll()
        self.assertEqual(app.text.value, source)
        self.assertFalse(app.text.undo_cleared)
        self.assertIn("朗读完成", app.status.set.call_args.args[0])

    def test_single_and_chapter_export_keep_exact_input_after_success(self):
        with tempfile.TemporaryDirectory() as folder:
            for mode in ("单个 MP3", "分章 MP3"):
                with self.subTest(mode=mode):
                    app = self.app()
                    app.export_mode.get.return_value = mode
                    source = app.text.value
                    target = str(Path(folder) / "public-test.mp3")
                    with patch("reader_app.filedialog.asksaveasfilename", return_value=target):
                        app.export_mp3()
                    self.assertTrue(app.exporting)
                    app.events.put(("export_done", (7, target)))
                    app.poll()
                    self.assertFalse(app.exporting)
                    self.assertEqual(app.text.value, source)
                    self.assertFalse(app.text.undo_cleared)

    def test_stop_error_and_tray_hiding_do_not_clear_input(self):
        app = self.app()
        source = app.text.value
        app.stop()
        app.poll()
        app.show_error("公开的错误提示。")
        app.native, app.tray_ok = Mock(), True
        app.hide()
        self.assertEqual(app.text.value, source)
        self.assertFalse(app.text.undo_cleared)
        app.root.withdraw.assert_called_once()

    def test_explicit_clear_removes_text_and_undo(self):
        app = self.app()
        app.clear_text()
        self.assertEqual(app.text.value, "")
        self.assertTrue(app.text.undo_cleared)
        app.counter.configure.assert_called_once_with(text="0 字")

    def test_actual_exit_clears_text_and_undo(self):
        app = self.app()
        app.exit()
        self.assertTrue(app.closed)
        self.assertEqual(app.text.value, "")
        self.assertTrue(app.text.undo_cleared)
        app.reader.close.assert_called_once()
        app.finish_exit.assert_called_once()
        app.persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
