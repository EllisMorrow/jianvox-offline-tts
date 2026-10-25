"""Real spawn/pipe supervision tests; no torch, GPU, clipboard or user files."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from model_worker import ModelClient, ModelFailure, ModelTimings, _error_category, _WorkerFault


def fake_worker(connection):
    _, mode = connection.recv()
    if mode == "startup_hang":
        time.sleep(30)
        return
    if mode == "startup_crash":
        os._exit(7)
    connection.send(("ready",))
    text = None
    count = 0
    while True:
        command = connection.recv()
        if command[0] == "close":
            return
        if command[0] == "stream":
            text = command[1]
            count = 0
            connection.send(("started",))
        elif command[0] == "next":
            count += 1
            if text == "hang":
                time.sleep(30)
            elif text == "crash":
                os._exit(9)
            elif text == "oom":
                connection.send(("error", "out_of_memory", True))
                return
            elif text == "limit":
                connection.send(("error", "generation_limit", False))
            elif text == "slow":
                time.sleep(.12)
                connection.send(("wave", [1, 2], 24000, {"total_steps_so_far": count}))
            elif count < 3:
                connection.send(("wave", [count], 24000, {"total_steps_so_far": count}))
            else:
                connection.send(("done",))
        elif command[0] == "cancel":
            connection.send(("cancelled",))


class ModelClientTests(unittest.TestCase):
    def client(self, mode="normal", **overrides):
        limits = dict(startup=4, chunk=.4, cancel=.4, shutdown=.5, poll=.01)
        limits.update(overrides)
        client = ModelClient(mode, worker_target=fake_worker, timings=ModelTimings(**limits))
        self.addCleanup(client.close)
        return client

    def test_hot_session_streams_multiple_requests_without_reloading(self):
        client = self.client()
        self.assertTrue(client.start())
        process_id = client._process.pid
        for text in ("public sample one", "public sample two"):
            waves = list(client.stream(text, "serena", ""))
            self.assertEqual([wave for wave, _, _ in waves], [[1], [2]])
            self.assertEqual(client._process.pid, process_id)
        client.close()
        self.assertFalse(client.available)
        self.assertIsNone(client._process)

    def test_startup_timeout_terminates_child_and_can_restart(self):
        client = self.client("startup_hang", startup=.15)
        started = time.monotonic()
        with self.assertRaises(ModelFailure) as caught:
            client.start()
        self.assertEqual(caught.exception.code, "startup_timeout")
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(client.available)
        self.assertIsNone(client._process)
        client.model_path = "normal"
        client.timings = ModelTimings(startup=4, chunk=.4, cancel=.4, shutdown=.5, poll=.01)
        self.assertTrue(client.start())
        self.assertTrue(list(client.stream("public sample", "serena", "")))

    def test_crashed_child_is_reported_without_exception_detail(self):
        client = self.client()
        client.start()
        with self.assertRaises(ModelFailure) as caught:
            list(client.stream("crash", "serena", ""))
        self.assertEqual(str(caught.exception), "worker_crashed")
        self.assertFalse(client.available)
        self.assertIsNone(client._process)

    def test_chunk_timeout_does_not_block_forever(self):
        client = self.client()
        client.start()
        started = time.monotonic()
        with self.assertRaises(ModelFailure) as caught:
            list(client.stream("hang", "serena", ""))
        self.assertEqual(caught.exception.code, "worker_timeout")
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(client.available)

    def test_cancellation_drains_inflight_wave_and_keeps_hot_model(self):
        client = self.client()
        client.start()
        process_id = client._process.pid
        cancel_at = time.monotonic() + .05
        self.assertEqual(list(client.stream("slow", "serena", "", active=lambda: time.monotonic() < cancel_at)), [])
        self.assertTrue(client.available)
        self.assertEqual(client._process.pid, process_id)
        self.assertEqual(len(list(client.stream("after cancellation", "serena", ""))), 2)

    def test_cancelling_hung_worker_has_deadline_and_allows_rebuild(self):
        client = self.client(cancel=.15)
        client.start()
        cancel_at = time.monotonic() + .05
        started = time.monotonic()
        self.assertEqual(list(client.stream("hang", "serena", "", active=lambda: time.monotonic() < cancel_at)), [])
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(client.available)
        self.assertIsNone(client._process)
        self.assertTrue(client.start())
        self.assertEqual(len(list(client.stream("after rebuild", "serena", ""))), 2)

    def test_closing_generator_cancels_without_discarding_hot_model(self):
        client = self.client()
        client.start()
        iterator = client.stream("public sample", "serena", "")
        next(iterator)
        iterator.close()
        self.assertTrue(client.available)
        self.assertEqual(len(list(client.stream("next sample", "serena", ""))), 2)

    def test_generation_limit_keeps_model_but_oom_discards_it(self):
        client = self.client()
        client.start()
        with self.assertRaises(ModelFailure) as caught:
            list(client.stream("limit", "serena", ""))
        self.assertEqual(caught.exception.code, "generation_limit")
        self.assertTrue(client.available)
        with self.assertRaises(ModelFailure) as caught:
            list(client.stream("oom", "serena", ""))
        self.assertEqual(caught.exception.code, "out_of_memory")
        self.assertFalse(client.available)

    def test_already_cancelled_start_creates_no_process(self):
        client = self.client()
        self.assertFalse(client.start(active=lambda: False))
        self.assertIsNone(client._process)

    def test_startup_cancellation_reaps_loading_child(self):
        client = self.client("startup_hang")
        cancel_at = time.monotonic() + .15
        self.assertFalse(client.start(active=lambda: time.monotonic() < cancel_at))
        self.assertFalse(client.available)
        self.assertIsNone(client._process)

    def test_actual_worker_reports_missing_model_without_loading_gpu(self):
        client = ModelClient("missing-model-for-supervision-test",
                             timings=ModelTimings(startup=4, shutdown=.5))
        self.addCleanup(client.close)
        with self.assertRaises(ModelFailure) as caught:
            client.start()
        self.assertEqual(caught.exception.code, "missing_model")
        self.assertFalse(client.available)
        self.assertIsNone(client._process)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object lifecycle")
    def test_parent_abrupt_exit_also_terminates_hung_worker(self):
        # The worker deliberately never reads EOF while hung. The OS-owned job
        # must reap it even when the owner bypasses all Python cleanup.
        script = (
            "import os,sys; "
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); "
            "from test_model_worker import fake_worker; "
            "from model_worker import ModelClient; "
            "client=ModelClient('normal', worker_target=fake_worker); "
            "client.start(); "
            "client._send(('stream','hang','serena','')); "
            "client._receive(2); client._send(('next',)); "
            "print(client._process.pid, flush=True); os._exit(13)"
        )
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True,
                                text=True, timeout=8)
        self.assertEqual(result.returncode, 13, result.stderr)
        child_id = int(result.stdout.strip())
        import ctypes
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, child_id)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
            finally:
                kernel.CloseHandle(handle)

    def test_failure_classification_only_returns_fixed_categories(self):
        for error, expected in (
                (RuntimeError("CUDA error: secret text or private path"), ("gpu_failure", True)),
                (RuntimeError("CUDA out of memory: private detail"), ("out_of_memory", True)),
                (ValueError("private user content"), ("generation_failed", True)),
                (_WorkerFault("cache_limit"), ("cache_limit", False))):
            self.assertEqual(_error_category(error), expected)


if __name__ == "__main__":
    unittest.main()
