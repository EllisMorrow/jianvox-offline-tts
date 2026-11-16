import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from symbol_filter import filter_symbols, DEFAULT_IGNORED_SYMBOLS
from settings import Preferences
from reader_app import App
from reader_core import split_text


class SymbolFilterTests(unittest.TestCase):
    def test_markdown_defaults_and_preserved_user_customization(self):
        self.assertEqual(Preferences().ignored_symbols, DEFAULT_IGNORED_SYMBOLS)
        self.assertEqual(filter_symbols('# **重点** ==高亮== ~~删除~~ `代码` > 引用 | 表格',
                                        True, DEFAULT_IGNORED_SYMBOLS),
                         ' 重点 高亮 删除 代码  引用  表格')
        self.assertEqual(filter_symbols('-12.5 + 3，结束。', True, DEFAULT_IGNORED_SYMBOLS),
                         '-12.5 + 3，结束。')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'prefs.json'
            Preferences(ignored_symbols='').save(path)
            self.assertEqual(Preferences.load(path).ignored_symbols, '')

    def test_literal_characters_no_regex_and_whitespace_preserved(self):
        text = '**你好** # Hello_world~ [test].\n下一行。'
        result = filter_symbols(text, True, '*#_~[] .\n')
        self.assertEqual(result, '你好  Helloworld test\n下一行。')
        self.assertEqual(text, '**你好** # Hello_world~ [test].\n下一行。')
        self.assertEqual(filter_symbols(text, False, text), text)
        self.assertEqual(filter_symbols(text, True, ''), text)

    def test_same_app_preparation_and_no_symbols_leaves_no_readable_text(self):
        app = App.__new__(App)
        app.preferences = SimpleNamespace(symbol_filter_enabled=True, ignored_symbols='*#')
        self.assertEqual(app.prepare_speech_text('*中文# English'), '中文 English')
        with self.assertRaises(ValueError):
            split_text(app.prepare_speech_text('**##'))
        app.preferences.symbol_filter_enabled = False
        self.assertEqual(app.prepare_speech_text('**##'), '**##')

    def test_preferences_persist_and_old_settings_default_off(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text('{}')
            self.assertFalse(Preferences.load(path).symbol_filter_enabled)
            prefs = Preferences(symbol_filter_enabled=True, ignored_symbols='*#_')
            prefs.save(path)
            loaded = Preferences.load(path)
            self.assertTrue(loaded.symbol_filter_enabled)
            self.assertEqual(loaded.ignored_symbols, '*#_')
            loaded.symbol_filter_enabled = False
            loaded.save(path)
            self.assertEqual(Preferences.load(path).ignored_symbols, '*#_')

    def test_invalid_preferences_are_rejected(self):
        for prefs in (Preferences(symbol_filter_enabled='yes'),
                      Preferences(ignored_symbols='*'*501)):
            with self.assertRaises(ValueError):
                prefs.validate()
