import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from reader_app import App
from settings import Preferences
from ui_themes import ARTWORK_COLORS, PALETTES, PALETTE_LABELS, get_palette


def contrast(first, second):
    def luminance(color):
        values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in values]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


class PaletteTests(unittest.TestCase):
    def test_five_complete_palettes_have_real_light_and_dark_colors(self):
        self.assertEqual(len(PALETTES), 5)
        self.assertEqual(set(PALETTES), set(PALETTE_LABELS))
        self.assertEqual(set(PALETTES), set(ARTWORK_COLORS))
        roles = set(PALETTES['lavender'])
        for name, palette in PALETTES.items():
            with self.subTest(name=name):
                self.assertEqual(set(palette), roles)
                self.assertNotEqual(palette['BG'][0], palette['BG'][1])
                for pair in palette.values():
                    self.assertEqual(len(pair), 2)
                    for color in pair:
                        self.assertRegex(color, r'^#[0-9A-F]{6}$')

    def test_readable_text_and_primary_buttons_in_all_ten_variants(self):
        pairs = [(ink, background)
                 for ink, backgrounds in (
                     ('INK', ('BG', 'PANEL', 'FIELD', 'CONTROL', 'CONTROL_BUTTON')),
                     ('MUTED', ('BG', 'PANEL', 'CONTROL', 'SIDEBAR')),
                     ('ACCENT', ('SELECTED', 'CONTROL', 'PANEL')))
                 for background in backgrounds]
        for name, palette in PALETTES.items():
            for mode in (0, 1):
                for ink, background in pairs:
                    with self.subTest(name=name, mode=mode, ink=ink, background=background):
                        self.assertGreaterEqual(contrast(palette[ink][mode], palette[background][mode]), 4.5)
                for background in ('PRIMARY_FILL', 'PRIMARY_HOVER'):
                    with self.subTest(name=name, mode=mode, button=background):
                        self.assertGreaterEqual(contrast('#FFFFFF', palette[background][mode]), 4.5)

    def test_palette_and_appearance_roundtrip_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            for palette in PALETTES:
                for mode in ('light', 'dark', 'system'):
                    prefs = Preferences(color_theme=palette, theme=mode, volume=1.5,
                                        close_to_tray=False, style='已保存的语气。')
                    prefs.save(path)
                    self.assertEqual(Preferences.load(path), prefs)

    def test_unknown_palette_is_rejected_without_saving_or_applying(self):
        app = App.__new__(App)
        app.preferences, app.ui, app.color_theme = Preferences(), Mock(), Mock()
        with patch.object(app, 'persist') as persist:
            with self.assertRaises(ValueError):
                app.change_palette('未知主题')
        app.ui.apply_palette.assert_not_called()
        persist.assert_not_called()
        for value in ('unknown', None, []):
            with self.assertRaises(ValueError):
                get_palette(value)
            with self.assertRaises(ValueError):
                Preferences(color_theme=value).validate()

    def test_change_updates_only_palette_and_skips_demo_disk_writes(self):
        app = App.__new__(App)
        app.demo = True
        app.preferences = Preferences(theme='dark', speed=1.25, volume=1.5)
        app.ui, app.color_theme = Mock(), Mock()
        with patch.object(Preferences, 'save') as save:
            app.change_palette('海洋蓝')
        app.ui.apply_palette.assert_called_once_with('ocean')
        app.color_theme.set.assert_called_once_with('海洋蓝')
        self.assertEqual(app.preferences.theme, 'dark')
        self.assertEqual(app.preferences.color_theme, 'ocean')
        self.assertEqual((app.preferences.speed, app.preferences.volume), (1.25, 1.5))
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
