import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from reader_core import AudioBuffer, Reader, split_text
import win_native


class ReaderTests(unittest.TestCase):
    def test_split_preserves_text_and_bounds(self):
        text = ("这是一段用于验证分段的文字。" * 43) + "末尾"
        parts = split_text(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(0 < len(p) <= 300 for p in parts))
        self.assertEqual(split_text("中文与 English 123。"), ["中文与 English 123。"])

    def test_reject_empty_and_oversize(self):
        for text in [" \n\t", "字" * 20001]:
            with self.assertRaises(ValueError):
                split_text(text)

    def test_cancel_discards_queued_and_late_chunks(self):
        q = queue.Queue()
        audio = AudioBuffer(q)
        audio.set_volume(1)
        audio.begin(1, time.perf_counter())
        audio.put(1, np.ones(40, dtype=np.float32))
        audio.begin(2, time.perf_counter())
        self.assertFalse(audio.put(1, np.ones(40, dtype=np.float32)))
        out = np.ones((64, 1), dtype=np.float32)
        audio.callback(out, 64, None, SimpleNamespace(output_underflow=False))
        self.assertTrue((out == 0).all())
        audio.put(2, np.full(10, .25, dtype=np.float32))
        audio.end(2)
        audio.callback(out, 64, None, SimpleNamespace(output_underflow=False))
        self.assertTrue((out[:10] == .25).all())
        self.assertTrue((out[10:] == 0).all())
        self.assertEqual([x[0] for x in list(q.queue)], ["first_audio", "played"])

    def test_stop_clears_latest_pending_request(self):
        reader = Reader.__new__(Reader)
        reader.events = queue.Queue()
        reader.lock = threading.Lock()
        reader.condition = threading.Condition(reader.lock)
        reader.number, reader.pending, reader.ready, reader.closed = 0, None, True, False
        reader.audio = AudioBuffer(reader.events)
        reader.submit("第一段")
        reader.submit("第二段")
        self.assertEqual(reader.pending.parts, ["第二段"])
        reader.stop()
        self.assertIsNone(reader.pending)
        self.assertFalse(reader._active(2))

    def test_copy_timeout_does_not_read_old_clipboard(self):
        with patch.object(win_native.U, "GetClipboardSequenceNumber", return_value=20), \
             patch.object(win_native.U, "GetAsyncKeyState", return_value=0), \
             patch.object(win_native.U, "SendInput", return_value=4), \
             patch.object(win_native, "clipboard_text") as read:
            with self.assertRaisesRegex(ValueError, "未复制到新文字"):
                win_native.copy_selection()
            read.assert_not_called()

    def test_copy_success_reads_updated_clipboard(self):
        with patch.object(win_native.U, "GetClipboardSequenceNumber", side_effect=[20, 21]), \
             patch.object(win_native.U, "GetAsyncKeyState", return_value=0), \
             patch.object(win_native.U, "SendInput", return_value=4), \
             patch.object(win_native, "clipboard_text", return_value="新文字"):
            self.assertEqual(win_native.copy_selection(), "新文字")

    def test_windows_input_layout(self):
        self.assertEqual(win_native.C.sizeof(win_native.INPUT), 40)

    def test_pause_does_not_consume_audio_and_stop_resets_pause(self):
        audio = AudioBuffer(queue.Queue())
        audio.begin(1, time.perf_counter())
        audio.set_volume(.5)
        audio.put(1, np.ones(30, dtype=np.float32))
        self.assertTrue(audio.toggle_pause())
        out = np.ones((10, 1), dtype=np.float32)
        audio.callback(out, 10, None, SimpleNamespace(output_underflow=False))
        self.assertTrue((out == 0).all())
        self.assertEqual(audio.seconds(), 30 / 24000)
        self.assertFalse(audio.toggle_pause())
        audio.callback(out, 10, None, SimpleNamespace(output_underflow=False))
        self.assertTrue((out == .5).all())
        audio.begin(2, time.perf_counter(), active=False)
        self.assertIsNone(audio.toggle_pause())


if __name__ == "__main__":
    unittest.main()
