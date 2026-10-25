"""Optional real GPU/output recovery check; public text and temporary audio only.

Close the desktop reader first. This briefly disconnects this test's own output
stream and terminates this test's own model child. It never changes Windows
audio settings, user preferences or other applications.
"""
import queue
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import soundfile as sf
import reader_core
from audio_output import AudioOutput, WindowsDefaultEndpoint
from export_plan import build_export_plan
from hotkeys import DEFAULT_HOTKEYS
from model_worker import ModelClient
from win_native import Native


class EndpointGate:
    def __init__(self, disconnected):
        self.disconnected = disconnected
        self.endpoint = WindowsDefaultEndpoint()

    def __call__(self):
        return None if self.disconnected.is_set() else self.endpoint()

    def close(self):
        self.endpoint.close()


def main():
    events = queue.Queue()
    native = Native(events, {action: "" for action in DEFAULT_HOTKEYS})
    assert not native.existing, "Close the desktop reader before this optional check"
    reader = None
    clients = []
    disconnected = threading.Event()

    def event(kind, number=None, timeout=190, allow_issue=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            name, data = events.get(timeout=max(.01, deadline-time.monotonic()))
            if name == "issue" and not allow_issue:
                raise RuntimeError(data.message)
            if name == kind and (number is None or (data[0] if isinstance(data, tuple) else data) == number):
                return data
        raise TimeoutError(kind)

    def wait_for(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.02)
        assert predicate(), "Recovery did not complete before the deadline"

    def model_factory(path):
        client = ModelClient(path)
        clients.append(client)
        return client

    try:
        event("hotkeys", timeout=5)
        reader = reader_core.Reader(events, model_factory=model_factory,
            output_factory=lambda queue, callback: AudioOutput(queue, callback,
                endpoint_factory=lambda: EndpointGate(disconnected), poll_interval=.2))
        event("ready")
        wait_for(lambda: reader.output.available)
        number = reader.submit("这是中英文混读检查。Hello, welcome back. 现在继续中文。", speed=1.25)
        latency = event("first_audio", number)[1]
        assert reader.toggle_pause() is True
        disconnected.set()
        wait_for(lambda: not reader.output.available)
        disconnected.clear()
        wait_for(lambda: reader.output.available)
        assert reader.toggle_pause() is False
        event("played", number)
        print(f"REAL_OUTPUT_RECONNECT_PASSED: first output {latency:.2f}s", flush=True)

        # Inject an actual exit into our own idle model, then exercise detection
        # and one automatic reload. No driver reset or other process is touched.
        old_pid = clients[0]._process.pid
        clients[0]._process.terminate()
        clients[0]._process.join(timeout=3)
        assert not clients[0]._process.is_alive()
        reader.submit("这是公开的模型恢复检测文字。")
        issue = event("issue", allow_issue=True)
        assert issue.code == "worker_crashed", issue.code
        event("ready", allow_issue=True)
        assert clients[0]._process.pid != old_pid
        number = reader.submit("声音已经恢复。We can continue reading.")
        event("played", number)
        print("REAL_MODEL_CRASH_AND_RELOAD_PASSED", flush=True)

        text = ("今天的天气很好，我们先整理思路，再从最重要的一件事开始。"
                "遇到不明白的地方，可以停下来想一想，然后继续前进。") * 7
        with tempfile.TemporaryDirectory(prefix="jianvox-reliability-") as folder:
            target = Path(folder) / "public-check.mp3"
            # Compress only the grouping boundary to 300 chars so this hardware
            # check exercises multiple committed files without a book-length run.
            with patch.object(reader_core, "build_export_plan",
                    side_effect=lambda parts, target, **kw: build_export_plan(
                        parts, target, chaptered=kw["chaptered"], chapter_chars=300)):
                number = reader.submit(text, output=target, chaptered=True)
            event("chapter_saved", number)
            reader.stop()
            event("export_cancelled", number)
            files = list(Path(folder).iterdir())
            assert files and all(file.suffix == ".mp3" for file in files)
            assert all(sf.info(file).frames > 0 for file in files)
            assert not target.exists()
            print(f"REAL_CHAPTER_CANCEL_PRESERVES_OUTPUT_PASSED: {len(files)} completed MP3", flush=True)
        print("REAL_RELIABILITY_CHECK_PASSED", flush=True)
    finally:
        if reader is not None:
            reader.close()
            reader.thread.join(timeout=10)
            assert not reader.thread.is_alive(), "Reader failed to close"
        native.close()
        native.thread.join(timeout=2)


if __name__ == "__main__":
    main()
