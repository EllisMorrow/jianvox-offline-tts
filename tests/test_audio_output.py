"""Output lifecycle tests without opening real audio devices or changing Windows."""
import queue
import ctypes
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from audio_output import AudioOutput, AudioOutputError, WindowsDefaultEndpoint, _GUID


class Endpoint:
    def __init__(self, value="A"):
        self.value = value
        self.closed = False

    def __call__(self):
        return self.value

    def close(self):
        self.closed = True


class Stream:
    def __init__(self, backend, **options):
        self.backend, self.options = backend, options
        self.active = False
        self.closed = False
        self.close_failure = False

    def start(self):
        self.backend.record("start")
        if self.backend.fail_start:
            raise OSError("Test device disappeared")
        self.active = True
        self.options["callback"](np.zeros((8, 1), dtype=np.float32), 8, None,
                                 SimpleNamespace(output_underflow=False))

    def abort(self):
        self.backend.record("abort")
        self.active = False

    def close(self):
        self.backend.record("close")
        if self.close_failure:
            raise OSError("Test close failed")
        self.closed = True


class Backend:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self._initialized = 1
        self.calls = []
        self.streams = []
        self.fail_open = 0
        self.fail_start = False

    def record(self, name):
        self.calls.append((name, threading.get_ident()))

    def _terminate(self):
        self.record("terminate")
        if any(not stream.closed for stream in self.streams):
            raise AssertionError("A stream was still open during refresh")
        self._initialized -= 1

    def _initialize(self):
        self.record("initialize")
        self._initialized += 1

    def query_hostapis(self):
        self.record("hostapis")
        return [{"name": "Windows WASAPI", "default_output_device": 1}]

    def query_devices(self, device):
        self.record("devices")
        return {"name": self.endpoint.value}

    def WasapiSettings(self, **options):
        return options

    def OutputStream(self, **options):
        self.record("open")
        if self.fail_open:
            self.fail_open -= 1
            raise OSError("Test open failed")
        stream = Stream(self, **options)
        self.streams.append(stream)
        return stream


class AudioOutputTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=1):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def output(self, endpoint=None, callback=lambda *_: None, backend=None, **options):
        endpoint = endpoint or Endpoint()
        backend = backend or Backend(endpoint)
        output = AudioOutput(queue.Queue(), callback, sd=backend,
            endpoint_factory=lambda: endpoint, poll_interval=.01,
            retry_delays=(0, .01, .02), **options)
        self.addCleanup(output.close)
        return output, endpoint, backend

    def test_initialization_and_close_are_owned_by_one_management_thread(self):
        output, endpoint, backend = self.output()
        self.assertEqual(output.ensure_available(.5), "A")
        options = backend.streams[0].options
        self.assertEqual((options["samplerate"], options["channels"], options["dtype"]),
                         (24000, 1, "float32"))
        self.assertTrue(options["extra_settings"]["auto_convert"])
        self.assertTrue(output.close())
        self.assertTrue(endpoint.closed)
        self.assertTrue(backend.streams[0].closed)
        self.assertEqual(backend._initialized, 0)
        self.assertEqual([name for name, _ in backend.calls][-2:], ["close", "terminate"])
        self.assertEqual({thread for _, thread in backend.calls}, {output.thread.ident})
        self.assertNotEqual(output.thread.ident, threading.get_ident())
        with self.assertRaises(AudioOutputError):
            output.ensure_available(.01)

    def test_endpoint_change_closes_old_stream_before_refresh_and_opens_new_default(self):
        output, endpoint, backend = self.output()
        output.ensure_available(.5)
        endpoint.value = "B"
        output.wake.set()
        self.wait_for(lambda: len(backend.streams) == 2 and output.available)
        self.assertTrue(backend.streams[0].closed)
        self.assertEqual(output.device_name, "B")
        calls = [call for call, _ in backend.calls]
        first_close = calls.index("close")
        self.assertEqual(calls[first_close:first_close+3], ["close", "terminate", "initialize"])

    def test_no_device_does_not_fail_application_and_reinsertion_recovers(self):
        endpoint = Endpoint(None)
        output, _, backend = self.output(endpoint)
        with self.assertRaises(AudioOutputError):
            output.ensure_available(.025)
        self.assertFalse(backend.streams)
        self.assertTrue(output.thread.is_alive())
        endpoint.value = "Headphones"
        output.wake.set()
        self.assertEqual(output.ensure_available(.5), "Headphones")

    def test_disconnect_releases_stream_then_same_endpoint_can_return(self):
        output, endpoint, backend = self.output()
        output.ensure_available(.5)
        endpoint.value = None
        output.wake.set()
        self.wait_for(lambda: backend.streams[0].closed)
        self.assertFalse(output.available)
        endpoint.value = "A"
        output.wake.set()
        self.assertEqual(output.ensure_available(.5), "A")
        self.assertEqual(len(backend.streams), 2)

    def test_open_retries_are_bounded_and_explicit_request_can_recover(self):
        endpoint = Endpoint()
        backend = Backend(endpoint)
        backend.fail_open = 10
        output, _, _ = self.output(endpoint, backend=backend)
        self.wait_for(lambda: sum(name == "open" for name, _ in backend.calls) == 3)
        time.sleep(.06)
        self.assertEqual(sum(name == "open" for name, _ in backend.calls), 3)
        backend.fail_open = 0
        self.assertEqual(output.ensure_available(.5), "A")
        self.assertEqual(sum(name == "open" for name, _ in backend.calls), 4)

    def test_failed_start_closes_created_stream_before_retry(self):
        endpoint = Endpoint()
        backend = Backend(endpoint)
        backend.fail_start = True
        output, _, _ = self.output(endpoint, backend=backend)
        self.wait_for(lambda: len(backend.streams) == 3)
        self.wait_for(lambda: all(stream.closed for stream in backend.streams))
        backend.fail_start = False
        self.assertEqual(output.ensure_available(.5), "A")

    def test_inactive_stream_is_reconnected(self):
        output, _, backend = self.output()
        output.ensure_available(.5)
        backend.streams[0].active = False
        output.wake.set()
        self.wait_for(lambda: len(backend.streams) == 2 and output.available)
        self.assertTrue(backend.streams[0].closed)

    def test_missing_callback_is_detected_instead_of_waiting_forever(self):
        output, _, backend = self.output(callback_timeout=.03)
        output.ensure_available(.5)
        self.wait_for(lambda: len(backend.streams) >= 2)
        self.assertTrue(backend.streams[0].closed)

    def test_callback_exception_is_silent_audio_and_bounded_reconnection(self):
        def fail(*_):
            raise RuntimeError("User text must never appear in output errors")
        output, _, backend = self.output(callback=fail)
        self.wait_for(lambda: len(backend.streams) == 3)
        time.sleep(.06)
        self.assertEqual(len(backend.streams), 3)
        messages = list(output.events.queue)
        self.assertFalse(any("User text" in str(value) for _, value in messages))

    def test_close_failure_prevents_portaudio_refresh_with_live_handle(self):
        output, endpoint, backend = self.output()
        output.ensure_available(.5)
        backend.streams[0].close_failure = True
        previous = sum(name == "terminate" for name, _ in backend.calls)
        endpoint.value = "B"
        output.wake.set()
        time.sleep(.05)
        self.assertEqual(sum(name == "terminate" for name, _ in backend.calls), previous)
        self.assertFalse(output.available)
        backend.streams[0].close_failure = False
        self.assertTrue(output.close())

    def test_final_close_failure_does_not_terminate_live_portaudio(self):
        output, _, backend = self.output()
        output.ensure_available(.5)
        backend.streams[0].close_failure = True
        previous = sum(name == "terminate" for name, _ in backend.calls)
        self.assertFalse(output.close())
        self.assertEqual(sum(name == "terminate" for name, _ in backend.calls), previous)
        self.assertEqual(backend._initialized, 1)

    def test_provider_initialization_transient_failure_recovers_automatically(self):
        endpoint = Endpoint()
        backend = Backend(endpoint)
        factory = Mock(side_effect=[OSError("Audio service starting"), OSError("Still starting"), endpoint])
        output = AudioOutput(queue.Queue(), lambda *_: None, sd=backend,
            endpoint_factory=factory, poll_interval=.01, retry_delays=(0, .01, .02))
        self.addCleanup(output.close)
        self.wait_for(lambda: output.available)
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(output.device_name, "A")

    def test_provider_initialization_budget_is_bounded_and_user_can_retry(self):
        endpoint = Endpoint()
        backend = Backend(endpoint)
        factory = Mock(side_effect=OSError("Audio service unavailable"))
        output = AudioOutput(queue.Queue(), lambda *_: None, sd=backend,
            endpoint_factory=factory, poll_interval=.01, retry_delays=(0, .01, .02))
        self.addCleanup(output.close)
        self.wait_for(lambda: factory.call_count == 3)
        time.sleep(.05)
        self.assertEqual(factory.call_count, 3)
        self.assertTrue(output.thread.is_alive())
        factory.side_effect = None
        factory.return_value = endpoint
        self.assertEqual(output.ensure_available(.5), "A")
        self.assertEqual(factory.call_count, 4)

    def test_provider_query_failure_releases_and_recreates_provider(self):
        class FailedEndpoint(Endpoint):
            def __call__(self):
                raise OSError("Audio service restarted")
        bad, good = FailedEndpoint(), Endpoint()
        backend = Backend(good)
        factory = Mock(side_effect=[bad, good])
        output = AudioOutput(queue.Queue(), lambda *_: None, sd=backend,
            endpoint_factory=factory, poll_interval=.01, retry_delays=(0, .01, .02))
        self.addCleanup(output.close)
        self.wait_for(lambda: output.available)
        self.assertTrue(bad.closed)
        self.assertEqual(factory.call_count, 2)

    def test_guid_uses_windows_little_endian_structure(self):
        guid = _GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
        self.assertEqual((guid.data1, guid.data2, guid.data3), (0xBCDE0395, 0xE52F, 0x467C))
        self.assertEqual(bytes(guid.data4), bytes.fromhex("8e3dc4579291692e"))

    def test_endpoint_monitor_uses_portaudio_multimedia_role(self):
        provider = WindowsDefaultEndpoint.__new__(WindowsDefaultEndpoint)
        provider.pointer = ctypes.c_void_p(1)
        get_default = Mock(return_value=0x80070490)
        provider._method = Mock(return_value=get_default)
        self.assertIsNone(provider())
        self.assertEqual(get_default.call_args.args[1:3], (0, 1))

    def test_close_wait_is_bounded_even_if_native_driver_blocks(self):
        output, endpoint, backend = self.output()
        output.ensure_available(.5)
        started, release = threading.Event(), threading.Event()
        original_close = backend.streams[0].close
        def blocked_close():
            started.set()
            release.wait(.5)
            original_close()
        backend.streams[0].close = blocked_close
        endpoint.value = None
        output.wake.set()
        self.assertTrue(started.wait(.5))
        before = time.monotonic()
        try:
            self.assertFalse(output.close(timeout=.02))
            self.assertLess(time.monotonic() - before, .15)
        finally:
            release.set()
        self.wait_for(lambda: not output.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
