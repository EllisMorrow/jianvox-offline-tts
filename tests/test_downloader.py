"""Actual curl transfers against tiny loopback fixtures, never the public internet."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sys

sys.dont_write_bytecode = True

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "download_tts.py"
spec = importlib.util.spec_from_file_location("download_tts", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PAYLOAD = (b"qwen-offline-download-test-" * 4096)


class Server(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def fixture(mode="normal"):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            value = self.headers.get("Range")
            requests.append(value)
            if mode == "missing":
                self.send_error(404)
                return
            if mode == "stall":
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.end_headers()
                time.sleep(3)
                return
            start = int(value.split("=")[1].split("-")[0]) if value else 0
            if mode == "no_range" and start:
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.end_headers()
                try:
                    self.wfile.write(PAYLOAD)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            self.send_response(206 if start else 200)
            content = PAYLOAD[start:]
            self.send_header("Content-Length", str(len(content)))
            if start:
                self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
            self.end_headers()
            try:
                if mode == "cut" and len(requests) == 1:
                    self.wfile.write(content[:8192])
                    self.wfile.flush()
                    self.close_connection = True
                elif mode == "corrupt":
                    self.wfile.write(b"X" * len(content))
                else:
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/sample.bin", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class Tests(unittest.TestCase):
    def make(self, root, url, **kwargs):
        item = {"path": "模型/sample.bin", "size": len(PAYLOAD),
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "urls": [url]}
        manifest = {"schema": 1, "model": "test", "files": [item]}
        events = []
        return module.Downloader(root, manifest, events.append, retry_delay=0,
                                 request_timeout=4, stall_timeout=2, **kwargs), events

    def test_success_report_and_cache_skip(self):
        with tempfile.TemporaryDirectory() as temp, fixture() as (url, requests):
            root = Path(temp)
            job, events = self.make(root, url)
            job.run()
            self.assertEqual((root / "模型/sample.bin").read_bytes(), PAYLOAD)
            report = json.loads((root / "下载完成报告.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "all_files_sha256_verified")
            self.assertEqual(events[-1]["kind"], "complete")
            second, _ = self.make(root, url)
            second.run()
            self.assertEqual(len(requests), 1)
            self.assertEqual(second.records[0]["status"], "already_verified")

    def test_resume_existing_partial(self):
        with tempfile.TemporaryDirectory() as temp, fixture() as (url, requests):
            root = Path(temp)
            (root / "模型").mkdir()
            (root / "模型/sample.bin.part").write_bytes(PAYLOAD[:9000])
            job, _ = self.make(root, url)
            job.run()
            self.assertEqual(requests[0], "bytes=9000-")
            self.assertEqual((root / "模型/sample.bin").read_bytes(), PAYLOAD)

    def test_broken_connection_retries_with_range(self):
        with tempfile.TemporaryDirectory() as temp, fixture("cut") as (url, requests):
            job, _ = self.make(Path(temp), url)
            job.run()
            self.assertEqual(requests[1], "bytes=8192-")
            self.assertEqual(len(requests), 2)

    def test_timeout_is_visible_and_never_success(self):
        with tempfile.TemporaryDirectory() as temp, fixture("stall") as (url, _):
            job, events = self.make(Path(temp), url, attempts=1)
            with self.assertRaisesRegex(module.DownloadError, "超时"):
                job.run()
            self.assertFalse((Path(temp) / "下载完成报告.json").exists())
            self.assertFalse(any(x["kind"] == "complete" for x in events))

    def test_http_failure_visible(self):
        with tempfile.TemporaryDirectory() as temp, fixture("missing") as (url, _):
            job, _ = self.make(Path(temp), url, attempts=1)
            with self.assertRaisesRegex(module.DownloadError, "下载失败"):
                job.run()

    def test_backup_source_after_failure(self):
        with tempfile.TemporaryDirectory() as temp, fixture("missing") as (bad, _), fixture() as (good, hits):
            root = Path(temp)
            job, _ = self.make(root, bad, attempts=2)
            job.manifest["files"][0]["urls"].append(good)
            job.run()
            self.assertEqual(len(hits), 1)
            self.assertEqual((root / "模型/sample.bin").read_bytes(), PAYLOAD)

    def test_checksum_failure_preserves_bad_file(self):
        with tempfile.TemporaryDirectory() as temp, fixture("corrupt") as (url, _):
            root = Path(temp)
            job, _ = self.make(root, url, attempts=1)
            with self.assertRaisesRegex(module.DownloadError, "校验失败"):
                job.run()
            self.assertFalse((root / "模型/sample.bin").exists())
            self.assertEqual(len(list((root / "模型").glob("*.校验未通过-*"))), 1)
            self.assertFalse((root / "下载完成报告.json").exists())

    def test_range_rejection_does_not_silently_redownload(self):
        with tempfile.TemporaryDirectory() as temp, fixture("no_range") as (url, _):
            root = Path(temp)
            (root / "模型").mkdir()
            partial = root / "模型/sample.bin.part"
            partial.write_bytes(PAYLOAD[:2000])
            job, _ = self.make(root, url, attempts=1)
            with self.assertRaisesRegex(module.DownloadError, "不支持断点续传"):
                job.run()
            self.assertEqual(partial.read_bytes(), PAYLOAD[:2000])

    def test_cancel_retains_partial(self):
        with tempfile.TemporaryDirectory() as temp, fixture("stall") as (url, _):
            root = Path(temp)
            job, _ = self.make(root, url)
            threading.Timer(0.3, job.cancel.set).start()
            with self.assertRaises(module.Paused):
                job.run()
            self.assertFalse((root / "下载完成报告.json").exists())

    def test_unsafe_path_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for path in ("../outside", "D:/outside", "/outside", "file:stream", "..\\outside"):
                with self.subTest(path=path), self.assertRaises(module.DownloadError):
                    module.target_path(Path(temp), path)

    def test_duplicate_process_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            with module.folder_lock(Path(temp)):
                with self.assertRaises(module.DownloadError):
                    with module.folder_lock(Path(temp)):
                        self.fail("second lock unexpectedly acquired")

    def test_existing_partial_is_counted_in_total_progress(self):
        with tempfile.TemporaryDirectory() as temp, fixture() as (url, _):
            root = Path(temp)
            (root / "模型").mkdir()
            (root / "模型/sample.bin.part").write_bytes(PAYLOAD[:1234])
            job, events = self.make(root, url)
            job.run()
            self.assertTrue(any(x.get("done") == len(PAYLOAD) for x in events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
