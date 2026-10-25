import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from audio_gain import apply_volume
from mp3_export import MP3Export
from reader_core import AudioBuffer, Reader, Request


class GainTests(unittest.TestCase):
    def test_ordinary_volume_matches_previous_scaling_without_mutating_input(self):
        wave = np.linspace(-1, 1, 101, dtype=np.float32)
        original = wave.copy()
        for volume in (0, .5, .85, 1):
            np.testing.assert_array_equal(apply_volume(wave, volume), wave * volume)
        np.testing.assert_array_equal(wave, original)

    def test_quiet_audio_gets_the_full_requested_gain(self):
        wave = np.array([-.4, -.2, 0, .2, .4], dtype=np.float32)
        np.testing.assert_allclose(apply_volume(wave, 1.5), wave * 1.5)

    def test_boosted_peaks_are_bounded_symmetric_and_monotonic(self):
        wave = np.linspace(-1, 1, 10001, dtype=np.float32)
        result = apply_volume(wave, 1.5)
        self.assertTrue(np.isfinite(result).all())
        self.assertLessEqual(float(np.max(np.abs(result))), 1)
        self.assertTrue((np.diff(result) >= 0).all())
        np.testing.assert_allclose(result, -result[::-1], atol=1e-6)
        self.assertGreater(result[-1], .99)

    def test_live_playback_accepts_150_percent_and_applies_the_gain(self):
        audio = AudioBuffer(queue.Queue())
        audio.begin(1, time.perf_counter())
        audio.set_volume(1.5)
        wave = np.array([.2, -.2, .9, -.9], dtype=np.float32)
        audio.put(1, wave)
        output = np.zeros((4, 1), dtype=np.float32)
        audio.callback(output, 4, None, SimpleNamespace(output_underflow=False))
        self.assertEqual(audio.volume, 1.5)
        np.testing.assert_allclose(output[:, 0], apply_volume(wave, 1.5))
        audio.set_volume(.5)
        audio.put(1, wave)
        audio.callback(output, 4, None, SimpleNamespace(output_underflow=False))
        np.testing.assert_allclose(output[:, 0], wave * .5)

    def test_export_pipeline_uses_150_percent_snapshot_in_decodable_mp3(self):
        wave = (.2 * np.sin(2*np.pi*220*np.arange(24000)/24000)).astype(np.float32)

        class Model:
            def stream(self, *args, **kwargs):
                for offset in range(0, len(wave), 3000):
                    yield wave[offset:offset+3000], 24000, {}

        reader = Reader.__new__(Reader)
        reader.events = queue.Queue()
        reader.lock = threading.Lock()
        reader.closed, reader.number = False, 1
        request = Request(1, ["公开增益测试。"], "serena", "", time.perf_counter(), volume=1.5)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "gain.mp3"
            with MP3Export(target) as writer:
                reader._render_parts(Model(), request, request.parts, np, writer, 0, time.perf_counter())
                writer.commit(lambda: True)
            decoded, rate = sf.read(target)
            self.assertEqual(rate, 24000)
            self.assertAlmostEqual(len(decoded)/rate, 1, delta=.1)
            gain = np.sqrt(np.mean(decoded**2)) / np.sqrt(np.mean(wave**2))
            self.assertAlmostEqual(gain, 1.5, delta=.08)
            self.assertEqual(list(Path(folder).iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
