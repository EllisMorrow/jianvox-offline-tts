import json
import unittest
from unittest.mock import patch, MagicMock
from update_check import latest_version, is_newer, API_URL


class UpdateTests(unittest.TestCase):
    def test_versions_are_compared_numerically(self):
        self.assertTrue(is_newer('v0.10.0', '0.8.2'))
        self.assertFalse(is_newer('0.8.2', '0.8.2'))
        self.assertFalse(is_newer('0.8.1', '0.8.2'))

    def test_stable_release_and_bounded_request(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            dict(tag_name='v0.8.2', draft=False, prerelease=False)).encode()
        with patch('update_check.urlopen', return_value=response) as fetch:
            self.assertEqual(latest_version(), '0.8.2')
        self.assertEqual(fetch.call_args.args[0].full_url, API_URL)
        self.assertEqual(fetch.call_args.kwargs['timeout'], 10)
        response.__enter__.return_value.read.assert_called_once_with(262145)

    def test_failures_do_not_become_no_update(self):
        with patch('update_check.urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                latest_version()
        for payload in (b'bad json', b'[]', b'null', b'{}', b'{"tag_name":"invalid"}', b'x'*262145,
                        b'{"tag_name":"v1.0","prerelease":true}'):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = payload
            with patch('update_check.urlopen', return_value=response):
                with self.assertRaises((ValueError, KeyError)):
                    latest_version()
