import math
import unittest

from export_estimate import (
    Calibration, MAX_SAMPLE_CHARACTERS, analyze_text, estimate_export, select_sample,
)


class AnalysisTests(unittest.TestCase):
    def test_spoken_units_ignore_whitespace_punctuation(self):
        self.assertEqual(analyze_text("中文， 123。Hello world!").units,
                         analyze_text("中文123 Hello world").units)
        chinese = analyze_text("中文。")
        self.assertEqual((chinese.units, chinese.language_category), (2, "zh"))
        self.assertEqual(analyze_text("Hello world").language_category, "en")
        self.assertEqual(analyze_text("中文 Hello world").language_category, "mixed")

    def test_actual_parts_and_input_limits(self):
        text = "第一段。第二段。"
        self.assertEqual(analyze_text(text, ["第一段。", "第二段。"]).segments, 2)
        with self.assertRaises(ValueError):
            analyze_text(text, ["不同正文"])
        for text in ("", " ，。!? ", "×" * 1000, "÷" * 1000, "中" * 1_000_001):
            with self.assertRaises(ValueError):
                analyze_text(text)
        self.assertEqual(analyze_text("中" * 1_000_000).characters, 1_000_000)


class SampleTests(unittest.TestCase):
    def test_complete_sentences_and_short_input(self):
        sentence = "今天我们来到图书馆一起阅读有趣的故事。"
        sample = select_sample(sentence * 100)
        self.assertTrue(sample.text.endswith("。"))
        self.assertGreaterEqual(sample.analysis.units, 40)
        self.assertLessEqual(sample.analysis.units, 80)
        self.assertEqual(sample.analysis.segments, 1)
        self.assertEqual(select_sample("你好。").text, "你好。")

    def test_long_unbroken_input_and_punctuation_are_bounded(self):
        for text in ("中" * 10_000, "a" * 10_000, "1234567890" * 1_000,
                     "Hello world, " * 1_000, "。" * 1_000 + "有效内容。",
                     "Hi " + "a" * 10_000):
            sample = select_sample(text)
            self.assertLessEqual(len(sample.text), MAX_SAMPLE_CHARACTERS)
            self.assertGreater(sample.analysis.units, 0)
            self.assertLessEqual(sample.analysis.units, 80)
            self.assertIn(sample.text, text)

    def test_english_sentence_boundary_and_word_boundary(self):
        sentence = "We enjoy reading interesting stories every morning. "
        sample = select_sample(sentence * 100)
        self.assertTrue(sample.text.endswith("."))
        self.assertGreater(sample.analysis.units, 30)
        ordinary = select_sample("Good morning everyone and welcome to our library " * 100)
        self.assertTrue(ordinary.text.endswith(("Good", "morning", "everyone", "and", "welcome", "to", "our", "library")))

    def test_uses_a_substantial_clause_instead_of_tiny_fragment(self):
        text = "大家好，" + "这是一句较长但仍然完整的介绍" * 3 + "，" + "继续阅读" * 30
        sample = select_sample(text)
        self.assertGreater(sample.analysis.units, 30)
        self.assertTrue(sample.text.endswith("，"))


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.sample = analyze_text("中" * 60)
        self.calibration = Calibration(self.sample, 12, 15)
        self.whole = analyze_text("中" * 600)

    def test_uncalibrated_does_not_invent_hardware_time(self):
        result = estimate_export(self.whole)
        self.assertIsNone(result.total_seconds)
        self.assertIsNone(result.generation_seconds)
        self.assertFalse(result.calibrated)
        self.assertGreater(result.audio_seconds.upper, result.audio_seconds.lower)

    def test_generation_time_is_independent_of_tempo(self):
        normal = estimate_export(self.whole, self.calibration)
        fast = estimate_export(self.whole, self.calibration, speed=1.5)
        self.assertEqual(normal.generation_seconds, fast.generation_seconds)
        self.assertAlmostEqual(normal.audio_seconds.lower / 1.5, fast.audio_seconds.lower)
        self.assertAlmostEqual(normal.audio_seconds.upper / 1.5, fast.audio_seconds.upper)
        self.assertGreater(fast.total_seconds.upper, normal.total_seconds.upper / 1.5)

    def test_ranges_scale_and_account_for_segment_overhead(self):
        base = estimate_export(self.whole, self.calibration)
        bigger = estimate_export(analyze_text("中" * 6000), self.calibration)
        fragmented = estimate_export(analyze_text("中" * 600, ["中" * 10] * 60), self.calibration)
        self.assertGreater(bigger.total_seconds.lower, base.total_seconds.lower)
        self.assertGreater(bigger.total_seconds.upper, base.total_seconds.upper)
        self.assertGreater(fragmented.total_seconds.upper, base.total_seconds.upper)
        relative_width = lambda value: value.upper / value.lower
        self.assertGreater(relative_width(bigger.generation_seconds), relative_width(base.generation_seconds))

    def test_small_probe_and_language_mismatch_widen_bounds(self):
        baseline = estimate_export(self.whole, self.calibration).generation_seconds
        small = estimate_export(self.whole, Calibration(analyze_text("中" * 6), 1.2, 1.5)).generation_seconds
        english = estimate_export(self.whole, Calibration(analyze_text("hello " * 36), 12, 15)).generation_seconds
        self.assertGreater(small.upper / small.lower, baseline.upper / baseline.lower)
        self.assertGreater(english.upper / english.lower, baseline.upper / baseline.lower)

    def test_disk_includes_temporary_and_final_files_at_conservative_rate(self):
        result = estimate_export(self.whole, self.calibration)
        self.assertGreater(result.disk_bytes_upper, 2 * result.audio_seconds.upper * 320_000 / 8)
        self.assertIsInstance(result.disk_bytes_upper, int)

    def test_invalid_calibration_and_speed_are_rejected(self):
        for value in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                Calibration(self.sample, value, 15)
            with self.assertRaises(ValueError):
                Calibration(self.sample, 12, value)
        for speed in (0, .7, 1.6, math.inf, math.nan):
            with self.assertRaises(ValueError):
                estimate_export(self.whole, self.calibration, speed)


if __name__ == "__main__":
    unittest.main()
