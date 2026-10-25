"""Fixed, actionable diagnostics must not expose text, paths or traceback logs."""
import errno
import logging
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from audio_output import AudioOutputError
from model_worker import ModelFailure
import privacy
from reader_app import App
from runtime_errors import MESSAGES, RuntimeIssue, describe_error


class RuntimeErrorTests(unittest.TestCase):
    secret = "PRIVATE_TEXT_AND_PATH_DO_NOT_COPY"

    def test_known_worker_categories_always_use_fixed_messages(self):
        for code in ("out_of_memory", "gpu_failure", "worker_timeout", "worker_crashed",
                     "startup_timeout", "worker_cleanup", "generation_limit", "cache_limit"):
            with self.subTest(code=code):
                issue = describe_error(ModelFailure(code))
                self.assertEqual(issue, RuntimeIssue(code, MESSAGES[code]))

    def test_unknown_errors_do_not_stringify_private_exception_details(self):
        class PrivateError(Exception):
            def __str__(self):
                raise AssertionError("Diagnostic formatting inspected private details")
        with patch("builtins.open", side_effect=AssertionError("No diagnostic file writes")):
            issue = describe_error(PrivateError(self.secret))
        self.assertEqual(issue, RuntimeIssue("unexpected", MESSAGES["unexpected"]))

    def test_playback_failure_has_audio_guidance_without_private_details(self):
        issue = describe_error(AudioOutputError(self.secret))
        self.assertEqual(issue.code, "audio_unavailable")
        self.assertIn("MP3", issue.message)
        self.assertNotIn(self.secret, issue.message)

    def test_export_failure_categories_are_actionable_and_omit_original_paths(self):
        examples = [
            (OSError(errno.ENOSPC, self.secret), "disk_full"),
            (PermissionError(errno.EACCES, self.secret), "save_permission"),
            (FileNotFoundError(errno.ENOENT, self.secret), "save_unavailable"),
            (FileExistsError(errno.EEXIST, self.secret), "file_exists"),
            (OSError(errno.EIO, self.secret), "save_io"),
        ]
        for error, code in examples:
            with self.subTest(code=code):
                issue = describe_error(error, exporting=True)
                self.assertEqual(issue, RuntimeIssue(code, MESSAGES[code]))
                self.assertNotIn(self.secret, issue.message)

    def test_windows_disk_error_categories(self):
        for number, code in ((112, "disk_full"), (32, "save_permission"), (21, "save_unavailable")):
            error = OSError(self.secret)
            error.winerror = number
            self.assertEqual(describe_error(error, exporting=True).code, code)

    def test_recording_and_copying_diagnostics_never_persists_or_reads_editor(self):
        app = App.__new__(App)
        app.exporting = False
        app.root = Mock()
        app.reader = SimpleNamespace(ready=True)
        app.preferences = SimpleNamespace(style=self.secret, export_directory=self.secret)
        app.text = Mock()
        app.diagnostic_text, app.status, app.show_error = Mock(), Mock(), Mock()
        app.persist = Mock()
        app.show = Mock()
        with patch("builtins.open", side_effect=AssertionError("No diagnostic file writes")):
            app.report_issue(describe_error(AudioOutputError(self.secret)))
            app.copy_diagnostics()
        copied = app.root.clipboard_append.call_args.args[0]
        self.assertIn("Last error: audio_unavailable", copied)
        self.assertNotIn(self.secret, copied)
        self.assertNotIn(self.secret, app.diagnostic_text.set.call_args.args[0])
        app.text.get.assert_not_called()
        app.persist.assert_not_called()
        app.show.assert_not_called()

    def test_quiet_runtime_uses_null_handler_and_only_opens_os_null(self):
        fake_sys = SimpleNamespace(dont_write_bytecode=False, stdout=None, stderr=None)
        sink = Mock()
        with patch.object(privacy, "sys", fake_sys), \
             patch.object(privacy.logging, "basicConfig") as configure, \
             patch.object(privacy, "open", return_value=sink, create=True) as opened, \
             patch.dict(privacy.os.environ, {}):
            self.assertIs(privacy.quiet_runtime(), sink)
            self.assertEqual(privacy.os.environ["PYTHONDONTWRITEBYTECODE"], "1")
        opened.assert_called_once_with(os.devnull, "w", encoding="utf-8")
        self.assertIs(fake_sys.stdout, sink)
        self.assertIs(fake_sys.stderr, sink)
        self.assertTrue(fake_sys.dont_write_bytecode)
        handlers = configure.call_args.kwargs["handlers"]
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], logging.NullHandler)
        self.assertTrue(configure.call_args.kwargs["force"])


if __name__ == "__main__":
    unittest.main()
