import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from reader_core import AudioBuffer, Reader, check_cache_limit, check_generation_limit, split_text
import win_native


class ReaderTests(unittest.TestCase):
    def test_split_preserves_text_and_bounds(self):
        text = ("这是一段用于验证分段的文字。" * 43) + "末尾"
        parts = split_text(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(0 < len(p) <= 300 for p in parts))
        self.assertEqual(split_text("中文与 English 123。"), ["中文与 English 123。"])

    def test_split_uses_english_sentence_and_word_boundaries(self):
        sentence = "A" * 180 + ". " + "word " * 60
        parts = split_text(sentence)
        self.assertTrue(parts[0].endswith("."))
        self.assertEqual("".join(parts), sentence.strip())
        self.assertTrue(all(0 < len(part) <= 300 for part in parts))

        numbers = "1234567890 " * 60
        number_parts = split_text(numbers)
        self.assertEqual("".join(number_parts), numbers.strip())
        self.assertTrue(all(0 < len(part) <= 300 for part in number_parts))

    def test_reject_empty_and_oversize(self):
        for text in [" \n\t", "字" * 20001]:
            with self.assertRaises(ValueError):
                split_text(text)

    def test_generation_limit_is_reported_instead_of_silent_completion(self):
        check_generation_limit({"total_steps_so_far": 1792})
        with self.assertRaisesRegex(RuntimeError, "模型上限"):
            check_generation_limit({"total_steps_so_far": 1800})

    def test_cache_boundary_is_checked_from_graph_after_generation(self):
        model = SimpleNamespace(talker_graph=SimpleNamespace(
            cache_position=SimpleNamespace(item=lambda: 2045), max_seq_len=2048))
        check_cache_limit(model)
        model.talker_graph.cache_position = SimpleNamespace(item=lambda: 2046)
        with self.assertRaisesRegex(RuntimeError, "触及模型缓存边界"):
            check_cache_limit(model)

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

    def test_probe_is_idle_only_bounded_and_cancel_has_terminal_event(self):
        reader = Reader.__new__(Reader)
        reader.events = queue.Queue()
        reader.lock = threading.Lock()
        reader.condition = threading.Condition(reader.lock)
        reader.number, reader.pending, reader.ready, reader.closed = 0, None, True, False
        reader.audio = AudioBuffer(reader.events)
        number = reader.probe(" calibration\ttext ")
        self.assertEqual(reader.pending.parts, ["calibration text"])
        self.assertTrue(reader.pending.probe)
        with self.assertRaises(ValueError):
            reader.probe("another probe")
        reader.stop()
        self.assertIsNone(reader.pending)
        self.assertIsNone(reader.probe_number)
        with self.assertRaises(ValueError):
            reader.probe("x" * 321)
        number = reader.probe("x" * 320)
        self.assertEqual(len(reader.pending.parts[0]), 320)
        self.assertEqual(reader.pending.parts, ["x" * 320])
        reader.stop()
        messages = list(reader.events.queue)
        self.assertEqual(sum(kind == "probe_cancelled" for kind, _ in messages), 2)

    def test_submit_is_rejected_while_probe_is_pending(self):
        reader = Reader.__new__(Reader)
        reader.events = queue.Queue()
        reader.lock = threading.Lock()
        reader.condition = threading.Condition(reader.lock)
        reader.number, reader.pending, reader.ready, reader.closed = 0, None, True, False
        reader.audio = AudioBuffer(reader.events)
        reader.probe("sample")
        with self.assertRaises(ValueError):
            reader.submit("while probing")

    def test_probe_rejects_every_busy_reader_state(self):
        reader = Reader.__new__(Reader)
        reader.events = queue.Queue()
        reader.lock = threading.Lock()
        reader.condition = threading.Condition(reader.lock)
        reader.number, reader.pending, reader.ready, reader.closed = 1, None, True, False
        reader.audio = AudioBuffer(reader.events)
        reader.export_number = None
        reader.probe_number = None
        for configure_busy in (
            lambda: setattr(reader, "export_number", 2),
            lambda: setattr(reader, "running_number", 2),
            lambda: setattr(reader.audio, "active", True),
        ):
            configure_busy()
            with self.assertRaises(ValueError):
                reader.probe("sample")
            reader.export_number = None
            reader.running_number = None
            reader.audio.active = False
        reader.pending = SimpleNamespace(probe=False)
        with self.assertRaises(ValueError):
            reader.probe("sample")

    def test_probe_worker_measures_raw_audio_without_gpu(self):
        class FakeModel:
            talker_graph = SimpleNamespace(cache_position=SimpleNamespace(item=lambda: 100), max_seq_len=2048)
            def stream(self, text, speaker, style, active=None):
                self.text = text
                yield np.ones(12000, dtype=np.float32), 24000, {}
                yield np.ones(6000, dtype=np.float32), 24000, {}

        reader = Reader.__new__(Reader)
        reader.events = queue.Queue()
        reader.lock = threading.Lock()
        reader.condition = threading.Condition(reader.lock)
        reader.number, reader.closed, reader.probe_number = 7, False, 7
        reader.running_number, reader.pending, reader.ready = 7, None, True
        reader.audio = AudioBuffer(reader.events)
        request = SimpleNamespace(number=7, parts=["sample"], speaker="serena", style="", probe=True)
        result = reader._run_probe_request(FakeModel(), request, np)
        self.assertIsNotNone(result)
        reader._finish_probe_request(request.number, result)
        kind, data = reader.events.get_nowait()
        self.assertEqual(kind, "probe_done")
        self.assertEqual(data[0], 7)
        self.assertAlmostEqual(data[2], .75)
        self.assertGreaterEqual(data[1], 0)
        self.assertIsNone(reader.probe_number)
        self.assertTrue(reader.events.empty())
        self.assertEqual(reader.probe("next"), 8)

    def test_running_probe_cancel_emits_terminal_event_and_clears_guard(self):
        class CancelModel:
            def __init__(self, reader):
                self.reader = reader

            def stream(self, text, speaker, style, active=None):
                yield np.ones(12000, dtype=np.float32), 24000, {}
                self.reader.stop()

        reader = Reader.__new__(Reader)
        reader.events = queue.Queue()
        reader.lock = threading.Lock()
        reader.condition = threading.Condition(reader.lock)
        reader.number, reader.closed, reader.pending, reader.probe_number = 8, False, None, 8
        reader.audio = AudioBuffer(reader.events)
        request = SimpleNamespace(number=8, parts=["sample"], speaker="serena", style="")
        result = reader._run_probe_request(CancelModel(reader), request, np)
        self.assertIsNone(result)
        reader._finish_probe_request(request.number, result)
        messages = list(reader.events.queue)
        self.assertEqual(sum(kind == "probe_cancelled" and payload == 8 for kind, payload in messages), 1)
        self.assertIsNone(reader.probe_number)

    def test_copy_timeout_does_not_read_old_clipboard(self):
        with patch.object(win_native.U, "GetForegroundWindow", return_value=101), \
             patch.object(win_native.U, "GetClipboardSequenceNumber", return_value=20), \
             patch.object(win_native.U, "GetAsyncKeyState", return_value=0), \
             patch.object(win_native.U, "SendInput", return_value=4), \
             patch.object(win_native, "clipboard_text") as read:
            with self.assertRaisesRegex(ValueError, "未复制到新文字"):
                win_native.copy_selection()
            read.assert_not_called()

    def test_copy_success_reads_updated_clipboard(self):
        with patch.object(win_native.U, "GetForegroundWindow", return_value=101), \
             patch.object(win_native.U, "IsClipboardFormatAvailable", return_value=True), \
             patch.object(win_native.U, "GetClipboardSequenceNumber", side_effect=[20, 21]), \
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
