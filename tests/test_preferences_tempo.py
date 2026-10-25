import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from hotkeys import parse_hotkey, validate_bindings, DEFAULT_HOTKEYS
from settings import Preferences
from tempo import Tempo


class PreferenceTests(unittest.TestCase):
    def test_hotkey_normalization_and_modifier_bits(self):
        key = parse_hotkey("ctrl + ALT + r")
        self.assertEqual((key.label, key.modifiers, key.vk), ("Ctrl+Alt+R", 3, 82))
        self.assertEqual(parse_hotkey("F24").vk, 135)

    def test_invalid_and_reserved_keys(self):
        for value in ["A", "F25", "Ctrl+Ctrl+R", "Ctrl+C", "Alt+F4", "", "Ctrl+", "Ctrl+中文"]:
            with self.assertRaises(ValueError, msg=value):
                parse_hotkey(value)
        with self.assertRaises(ValueError):
            validate_bindings(dict(DEFAULT_HOTKEYS, stop="F4"))

    def test_settings_roundtrip_excludes_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            prefs = Preferences(theme="dark", speed=1.25, volume=.4, priority_hotkeys=True,
                                hotkeys=dict(DEFAULT_HOTKEYS, read="Ctrl+Alt+R"))
            prefs.save(path)
            self.assertEqual(Preferences.load(path), prefs)
            self.assertEqual(set(json.loads(path.read_text("utf-8"))), {"voice", "style", "theme", "speed", "volume", "hotkeys", "priority_hotkeys", "export_directory", "chaptered_export"})

    def test_legacy_settings_default_to_system_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"voice":"serena","speed":1.1}', "utf-8")
            self.assertEqual(Preferences.load(path).theme, "system")
            self.assertFalse(Preferences.load(path).priority_hotkeys)
            self.assertFalse(Preferences.load(path).chaptered_export)
            self.assertEqual(path.read_text("utf-8"), '{"voice":"serena","speed":1.1}')

    def test_blank_hotkeys_are_independently_disabled(self):
        empty = {action: "" for action in DEFAULT_HOTKEYS}
        self.assertEqual(validate_bindings(empty), empty)
        partial = dict(DEFAULT_HOTKEYS, read=" ", pause="")
        self.assertEqual(validate_bindings(partial), {"read":"", "stop":"F6", "pause":""})

    def test_invalid_settings_preserve_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            original = '{"speed":99}'
            path.write_text(original, "utf-8")
            self.assertEqual(Preferences.load(path).speed, 1)
            self.assertEqual(path.read_text("utf-8"), original)

    def test_settings_reject_out_of_bounds(self):
        for theme in ("light", "dark", "system"):
            self.assertEqual(Preferences(theme=theme).validate().theme, theme)
        for kwargs in [{"speed": .5}, {"speed": 2}, {"volume": 1.51}, {"volume": -1}, {"voice": "unknown"}, {"theme": "automatic"}, {"theme": []}]:
            with self.assertRaises(ValueError):
                Preferences(**kwargs).validate()
        for value in (None, 1, "true"):
            with self.assertRaises(ValueError):
                Preferences(priority_hotkeys=value).validate()
            with self.assertRaises(ValueError):
                Preferences(chaptered_export=value).validate()

    def test_boosted_volume_roundtrip_keeps_other_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            prefs = Preferences(volume=1.5, voice="vivian", theme="dark", speed=1.25)
            prefs.save(path)
            self.assertEqual(Preferences.load(path), prefs)
            for level in (0, .85, 1, 1.01, 1.5):
                self.assertEqual(Preferences(volume=level).validate().volume, level)


class TempoTests(unittest.TestCase):
    def test_normal_speed_is_bit_exact(self):
        wave = np.random.default_rng(15).uniform(-1, 1, 24000).astype(np.float32)
        self.assertTrue(np.array_equal(Tempo(1).process(wave), wave))

    def test_streamed_speed_changes_duration_and_preserves_pitch(self):
        sr, frequency = 24000, 240
        wave = np.sin(2 * np.pi * frequency * np.arange(sr * 4) / sr).astype(np.float32)
        for speed in [.75, 1.25, 1.5]:
            tempo = Tempo(speed)
            outputs = [tempo.process(wave[i:i+15360]) for i in range(0, len(wave), 15360)]
            outputs.append(tempo.process(np.empty(0, dtype=np.float32), final=True))
            result = np.concatenate(outputs)
            self.assertTrue(np.isfinite(result).all())
            self.assertAlmostEqual(len(result) / len(wave), 1 / speed, delta=.04)
            middle = result[len(result)//4:3*len(result)//4]
            spectrum = np.abs(np.fft.rfft(middle))
            peak = np.argmax(spectrum) * sr / len(middle)
            self.assertAlmostEqual(peak, frequency, delta=3)


if __name__ == "__main__":
    unittest.main()
