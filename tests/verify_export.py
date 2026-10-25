"""Real GPU multi-part MP3 export and cancellation. All test audio is auto-deleted."""
import queue
import tempfile
import time
from pathlib import Path
import soundfile as sf
from reader_core import Reader, split_text
from export_estimate import select_sample, analyze_text, Calibration, estimate_export
from win_native import Native, U
from hotkeys import DEFAULT_HOTKEYS

events = queue.Queue()
native = Native(events, {key: "" for key in DEFAULT_HOTKEYS})
assert not native.existing, "Close the desktop reader before this optional check"
reader = None


def wait_for(kind, number=None, timeout=150):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        event, data = events.get(timeout=max(.1, end-time.monotonic()))
        if event == "error":
            raise RuntimeError(data)
        if event == kind and (number is None or (data[0] if isinstance(data, tuple) else data) == number):
            return data
    raise TimeoutError(kind)


try:
    keys = wait_for("hotkeys", timeout=5)
    assert keys["success"] and not any(keys["bindings"].values())
    # Actual registration and release of an independently optional shortcut.
    native.retry(dict(DEFAULT_HOTKEYS, read="", stop="", pause="F7"))
    assert wait_for("hotkeys", timeout=5)["success"]
    native.retry({key: "" for key in DEFAULT_HOTKEYS})
    assert wait_for("hotkeys", timeout=5)["success"]
    assert U.RegisterHotKey(None, 9012, 0x4000, 0x76), "F7 was not released"
    U.UnregisterHotKey(None, 9012)

    reader = Reader(events)
    wait_for("ready")
    text = "今天的天气很好，窗外有微风，树叶随着风轻轻摇动。我们不必急着完成所有事情，可以先整理思路，再从最重要的一件事开始。遇到不明白的地方，停下来想一想，也可以换个角度看待问题。把事情一步一步做好，往往比匆忙赶路更有效。" * 3
    sample = select_sample(text)
    number = reader.probe(sample.text)
    _, generation, duration = wait_for("probe_done", number)
    assert generation > 0 and duration > 0
    assert not reader.audio.active and reader.audio.seconds() == 0
    assert reader.probe_number is None and reader.running_number is None
    estimate = estimate_export(analyze_text(text, split_text(text)),
        Calibration(sample.analysis, generation, duration), speed=1.25)
    print(f"PROBE_CHECK_PASSED: {len(sample.text)} sample chars; {generation:.2f}s generation; {duration:.2f}s audio; export range {estimate.total_seconds.lower:.2f}–{estimate.total_seconds.upper:.2f}s", flush=True)
    number = reader.probe(sample.text)
    time.sleep(.3)
    reader.stop()
    wait_for("probe_cancelled", number)
    assert reader.probe_number is None and reader.running_number is None
    print("PROBE_CANCELLATION_CHECK_PASSED", flush=True)
    with tempfile.TemporaryDirectory(prefix="qwen-export-check-") as tmp:
        target = Path(tmp) / "分段验证.mp3"
        started = time.perf_counter()
        number = reader.submit(text, speed=1.25, output=target)
        wait_for("export_done", number)
        info = sf.info(target)
        print(f"EXPORT_ELAPSED: {time.perf_counter()-started:.2f}s", flush=True)
        assert info.format == "MP3" and info.frames > 24000 * 20
        assert reader.export_number is None
        assert list(Path(tmp).iterdir()) == [target]
        print(f"MULTIPART_MP3_CHECK_PASSED: {len(text)} characters; {info.duration:.2f}s audio", flush=True)
        target.unlink()
        number = reader.submit(text, output=target)
        wait_for("export_activity", number)
        reader.stop()
        wait_for("export_cancelled", number)
        assert list(Path(tmp).iterdir()) == [], "Cancelled export left audio behind"
        print("CANCELLED_MP3_CLEANUP_CHECK_PASSED", flush=True)
    number = reader.submit("导出完成以后，朗读仍然可以正常使用。")
    wait_for("played", number)
    print("EXPORT_TO_PLAYBACK_CHECK_PASSED", flush=True)
finally:
    if reader:
        reader.close()
        reader.thread.join(timeout=10)
    native.close()
    native.thread.join(timeout=2)
