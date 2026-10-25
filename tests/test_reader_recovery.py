"""Reader thread integration with deterministic model/output substitutes."""
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audio_output import AudioOutputError
from model_worker import ModelFailure
from reader_core import Reader


class FakeModel:
    def __init__(self):
        self.available = False
        self.starts = self.closes = 0
        self.texts = []
        self.fail_loads = set()
        self.entered = threading.Event()
        self.load_gate = None
        self.loading = threading.Event()

    def start(self, active=None):
        self.starts += 1
        if self.starts in self.fail_loads:
            self.available = False
            raise ModelFailure("gpu_unavailable")
        if self.load_gate is not None:
            self.loading.set()
            while not self.load_gate.wait(.005):
                if active is not None and not active():
                    self.available = False
                    return False
        self.available = active is None or active()
        return self.available

    def stream(self, text, speaker, style, active=None):
        self.texts.append(text)
        if text == "公开故障样本。":
            self.available = False
            raise ModelFailure("out_of_memory")
        if text == "公开等待样本。":
            self.entered.set()
            while active is None or active():
                time.sleep(.005)
            return
        if text.lstrip().startswith("后章故障"):
            raise ModelFailure("generation_limit")
        if active is None or active():
            yield np.ones(2400, dtype=np.float32) * .1, 24000, {}

    def close(self):
        self.closes += 1
        self.available = False


class FakeOutput:
    def __init__(self, available=True):
        self.available = available
        self.ensure_calls = self.close_calls = 0

    def ensure_available(self, timeout=3):
        self.ensure_calls += 1
        if not self.available:
            raise AudioOutputError("No test playback device")
        return "test output"

    def close(self):
        self.close_calls += 1
        return True


class ContinuousModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.chunks_requested = 0
        self.fourteenth_requested = threading.Event()
        self.iterator_closed = threading.Event()

    def stream(self, text, speaker, style, active=None):
        self.texts.append(text)
        try:
            for _ in range(1000):
                if active is not None and not active():
                    return
                self.chunks_requested += 1
                if self.chunks_requested == 14:
                    self.fourteenth_requested.set()
                yield np.ones(15360, dtype=np.float32) * .1, 24000, {}
        finally:
            self.iterator_closed.set()


class ReaderRecoveryTests(unittest.TestCase):
    def reader(self, *, model=None, output=None):
        model = model or FakeModel()
        output = output or FakeOutput()
        events = queue.Queue()
        reader = Reader(events, model_path=Path("unused-test-model"),
                        model_factory=lambda _: model,
                        output_factory=lambda *_: output)
        self.addCleanup(self.close_reader, reader)
        return reader, model, output, events

    def close_reader(self, reader):
        reader.close()
        reader.thread.join(timeout=2)
        self.assertFalse(reader.thread.is_alive(), "Reader owner thread did not close")

    def event(self, events, kind, *, number=None, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event, value = events.get(timeout=max(.001, deadline - time.monotonic()))
            except queue.Empty:
                break
            if event == kind and (number is None or value[0] == number):
                return value
        self.fail(f"Reader did not emit {kind}")

    def idle(self, reader):
        deadline = time.monotonic() + 2
        while reader.running_number is not None and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertIsNone(reader.running_number)

    def test_fatal_generation_failure_reloads_once_without_replaying_text(self):
        reader, model, _, events = self.reader()
        self.event(events, "ready")
        reader.submit("公开故障样本。")
        self.assertEqual(self.event(events, "issue").code, "out_of_memory")
        self.event(events, "ready")
        self.assertEqual(model.starts, 2)
        self.assertEqual(model.texts, ["公开故障样本。"])
        number = reader.submit("恢复后的公开样本。")
        self.event(events, "generated", number=number)
        self.assertEqual(model.starts, 2)

    def test_failed_automatic_reload_waits_for_explicit_restart(self):
        model = FakeModel()
        model.fail_loads.add(2)
        reader, model, _, events = self.reader(model=model)
        self.event(events, "ready")
        reader.submit("公开故障样本。")
        self.assertEqual(self.event(events, "issue").code, "out_of_memory")
        self.assertEqual(self.event(events, "issue").code, "gpu_unavailable")
        time.sleep(.1)
        self.assertEqual(model.starts, 2)
        self.assertFalse(reader.ready)
        reader.restart()
        self.event(events, "ready")
        self.assertEqual(model.starts, 3)
        self.assertEqual(model.texts, ["公开故障样本。"])

    def test_initial_load_failure_can_be_recovered_from_settings_action(self):
        model = FakeModel()
        model.fail_loads.add(1)
        reader, model, _, events = self.reader(model=model)
        self.assertEqual(self.event(events, "issue").code, "gpu_unavailable")
        self.assertFalse(reader.ready)
        reader.restart()
        self.event(events, "ready")
        self.assertEqual(model.starts, 2)
        self.assertTrue(reader.ready)

    def test_exit_during_loading_cancels_owner_and_closes_model_and_output(self):
        model = FakeModel()
        model.load_gate = threading.Event()
        reader, model, output, _ = self.reader(model=model)
        self.assertTrue(model.loading.wait(2))
        reader.close()
        reader.thread.join(timeout=2)
        self.assertFalse(reader.thread.is_alive())
        self.assertFalse(reader.ready)
        self.assertEqual((model.starts, model.closes, output.close_calls), (1, 1, 1))

    def test_restart_closes_hot_model_and_does_not_accept_busy_restart(self):
        reader, model, _, events = self.reader()
        self.event(events, "ready")
        reader.restart()
        self.event(events, "ready")
        self.assertEqual((model.starts, model.closes), (2, 1))
        reader.submit("公开等待样本。")
        self.assertTrue(model.entered.wait(2))
        with self.assertRaises(ValueError):
            reader.restart()
        reader.stop()
        self.idle(reader)
        reader.restart()
        self.event(events, "ready")
        self.assertEqual(model.starts, 3)

    def test_replacing_request_discards_old_audio_and_processes_latest_text(self):
        reader, model, _, events = self.reader()
        self.event(events, "ready")
        old = reader.submit("公开等待样本。")
        self.assertTrue(model.entered.wait(2))
        latest = reader.submit("新的公开朗读样本。")
        self.event(events, "generated", number=latest)
        self.assertFalse(reader._active(old))
        self.assertEqual(reader.audio.number, latest)
        self.assertEqual(model.texts, ["公开等待样本。", "新的公开朗读样本。"])
        self.assertEqual(model.starts, 1)

    def test_stop_cancels_active_generation_and_later_request_still_works(self):
        reader, model, _, events = self.reader()
        self.event(events, "ready")
        old = reader.submit("公开等待样本。")
        self.assertTrue(model.entered.wait(2))
        reader.stop()
        self.idle(reader)
        self.assertFalse(reader._active(old))
        self.assertEqual(reader.audio.seconds(), 0)
        number = reader.submit("停止后的公开样本。")
        self.event(events, "generated", number=number)
        self.assertEqual(model.starts, 1)

    def test_stale_failure_cannot_stop_latest_request(self):
        reader, model, _, events = self.reader()
        self.event(events, "ready")
        old = reader.submit("公开等待样本。")
        self.assertTrue(model.entered.wait(2))
        latest = reader.submit("最新的公开朗读样本。")
        self.event(events, "generated", number=latest)
        self.assertFalse(reader.stop(expected_number=old))
        self.assertEqual(reader.number, latest)
        self.assertEqual(reader.audio.number, latest)
        self.assertTrue(reader.audio.active)
        self.assertGreater(reader.audio.seconds(), 0)

    def test_short_generated_audio_expires_after_device_loss_and_export_still_works(self):
        with patch.object(Reader, "PLAYBACK_RECOVERY_SECONDS", .03):
            reader, model, output, events = self.reader()
            self.event(events, "ready")
            number = reader.submit("短篇公开朗读样本。")
            self.event(events, "generated", number=number)
            self.assertGreater(reader.audio.seconds(), 0)
            self.assertLess(reader.audio.seconds(), 8)
            output.available = False
            self.assertEqual(self.event(events, "issue").code, "audio_unavailable")
            self.assertFalse(reader.audio.active)
            self.assertEqual(reader.audio.seconds(), 0)
            self.assertEqual(model.starts, 1)
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "export-after-device-loss.mp3"
                number = reader.submit("播放设备失联后的公开导出样本。", output=target)
                self.event(events, "export_done", number=number)
                self.assertGreater(sf.info(target).frames, 0)
                self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_paused_audio_with_available_device_does_not_expire(self):
        with patch.object(Reader, "PLAYBACK_RECOVERY_SECONDS", .03):
            reader, _, output, events = self.reader()
            self.event(events, "ready")
            number = reader.submit("暂停的公开朗读样本。")
            self.event(events, "generated", number=number)
            self.assertTrue(reader.toggle_pause())
            buffered = reader.audio.seconds()
            time.sleep(.65)  # More than two owner-thread idle checks.
            self.assertTrue(output.available)
            self.assertEqual(reader.number, number)
            self.assertTrue(reader.audio.active)
            self.assertTrue(reader.audio.paused)
            self.assertEqual(reader.audio.seconds(), buffered)

    def test_export_and_probe_work_without_playback_device(self):
        reader, _, output, events = self.reader(output=FakeOutput(available=False))
        self.event(events, "ready")
        number = reader.probe("公开的预估样本。")
        self.event(events, "probe_done", number=number)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "public-test.mp3"
            number = reader.submit("公开的导出样本。", output=target)
            self.event(events, "export_done", number=number)
            self.assertGreater(sf.info(target).frames, 0)
            self.assertEqual(list(Path(tmp).iterdir()), [target])
        self.assertEqual(output.ensure_calls, 0)
        number = reader.submit("公开的播放样本。")
        self.assertEqual(self.event(events, "issue").code, "audio_unavailable")
        self.assertEqual(output.ensure_calls, 1)
        self.idle(reader)
        self.assertIsNone(reader.export_number)

    def test_reader_chapter_failure_keeps_completed_audio_and_no_partial_file(self):
        reader, _, _, events = self.reader()
        self.event(events, "ready")
        # Ten complete synthesis segments form the first 3000-character chapter.
        text = ("公开" * 149 + "。 ") * 10 + "后章故障。"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "explicit-output.mp3"
            number = reader.submit(text, output=target, chaptered=True)
            self.event(events, "chapter_saved", number=number)
            issue = self.event(events, "issue")
            self.assertEqual(issue.code, "generation_limit")
            self.assertIn("1 个分章", issue.message)
            self.event(events, "export_cancelled", number=None)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["explicit-output_001.mp3"])
            self.assertGreater(sf.info(Path(tmp) / "explicit-output_001.mp3").frames, 0)

    def test_million_character_export_pipeline_preserves_every_segment(self):
        reader, model, output, events = self.reader()
        self.event(events, "ready")
        text = "字" * 1_000_000
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "million-character-pipeline.mp3"
            number = reader.submit(text, output=target)
            self.event(events, "export_done", number=number, timeout=30)
            self.assertEqual(len(model.texts), 3334)
            self.assertTrue(all(0 < len(part) <= 300 for part in model.texts))
            self.assertEqual("".join(model.texts), text)
            info = sf.info(target)
            self.assertEqual(info.format, "MP3")
            self.assertAlmostEqual(info.duration, len(model.texts) * .1, delta=.1)
            self.assertEqual(list(Path(tmp).iterdir()), [target])
            self.assertIsNone(reader.export_number)
            self.assertEqual(output.ensure_calls, 0)

    def test_paused_stream_applies_bounded_backpressure_and_stop_releases_it(self):
        model = ContinuousModel()
        reader, model, _, events = self.reader(model=model)
        self.event(events, "ready")
        buffer_full = threading.Event()
        original_put = reader.audio.put

        def observe_put(number, wave):
            accepted = original_put(number, wave)
            if reader.audio.seconds() > 8:
                buffer_full.set()
            return accepted

        # No FakeOutput callback consumes samples. Observe the real AudioBuffer
        # rather than replacing its capacity or the reader's producer loop.
        with patch.object(reader.audio, "put", side_effect=observe_put):
            reader.submit("字" * 20000)
            self.assertTrue(buffer_full.wait(3))
            self.assertTrue(reader.toggle_pause())
            self.assertFalse(model.fourteenth_requested.wait(.15))
            self.assertEqual(model.chunks_requested, 13)
            self.assertAlmostEqual(reader.audio.seconds(), 13 * .64, places=5)
            self.assertEqual(len(model.texts), 1)
            reader.stop()
            self.assertTrue(model.iterator_closed.wait(2))
            self.idle(reader)
            self.assertEqual(model.chunks_requested, 13)
            self.assertEqual(reader.audio.seconds(), 0)
            self.assertFalse(reader.audio.active)
            self.assertEqual(model.starts, 1)


if __name__ == "__main__":
    unittest.main()
