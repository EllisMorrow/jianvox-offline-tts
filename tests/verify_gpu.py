"""Optional real-device integration check. Plays public test text through speakers."""
import json
import queue
import time
from reader_core import Reader
from settings import LOCAL, write_json
from hotkeys import DEFAULT_HOTKEYS
from win_native import Native, U

def main():
    events = queue.Queue()
    native = Native(events)
    assert not native.existing, "Close the desktop reader first"
    reader = None
    held = False
    results = []


    def wait_for(kind, timeout=90, number=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event, value = events.get(timeout=max(.1, deadline-time.monotonic()))
            print(event, value, flush=True)
            if event in {"error", "issue"}:
                raise RuntimeError(value.message if event == "issue" else value)
            if event == kind and (number is None or value[0] == number):
                return value
        raise TimeoutError(kind)


    try:
        initial = wait_for("hotkeys", timeout=5)
        assert initial["success"] and initial["bindings"] == DEFAULT_HOTKEYS, initial
        held = bool(U.RegisterHotKey(None, 8877, 0x4000, 0x85))  # F22 owned by this test thread
        assert held, "The test fixture F22 was already occupied"
        native.retry(dict(DEFAULT_HOTKEYS, read="F22"))
        conflict = wait_for("hotkeys", timeout=5)
        assert not conflict["success"] and conflict["bindings"] == DEFAULT_HOTKEYS, conflict
        U.UnregisterHotKey(None, 8877)
        held = False
        changed = dict(DEFAULT_HOTKEYS, read="Ctrl+Alt+F22")
        native.retry(changed)
        assert wait_for("hotkeys", timeout=5)["bindings"] == changed
        native.retry(DEFAULT_HOTKEYS)
        assert wait_for("hotkeys", timeout=5)["success"]
        results.append({"test": "hotkey_change_conflict_rollback", "passed": True})

        reader = Reader(events)
        wait_for("ready")
        number = reader.submit("你好，新版离线朗读已经准备好了。")
        latency = wait_for("first_audio", number=number)[1]
        done = wait_for("played", number=number)
        assert done[1] == 0, done
        results.append({"test": "normal_speed_output", "first_output_seconds": latency, "buffer_gaps": done[1]})

        text = "今天的天气很好，窗外有微风，树叶随着风轻轻摇动。我们不必急着完成所有事情，可以先整理思路，再从最重要的一件事开始。遇到不明白的地方，停下来想一想，也可以换个角度看待问题。把事情一步一步做好，往往比匆忙赶路更有效。"
        number = reader.submit(text, speed=1.25)
        latency = wait_for("first_audio", number=number)[1]
        assert reader.toggle_pause() is True
        with reader.audio.lock:
            offset = reader.audio.offset
        time.sleep(.25)
        with reader.audio.lock:
            assert offset == reader.audio.offset, "Paused output consumed samples"
        assert reader.toggle_pause() is False
        done = wait_for("played", number=number)
        assert done[1] == 0, done
        results.append({"test": "speed_1.25_pause_resume", "characters": len(text),
                        "first_output_seconds": latency, "buffer_gaps": done[1]})

        number = reader.submit(text, speed=.75)
        wait_for("first_audio", number=number)
        reader.stop()
        assert reader.audio.seconds() == 0 and not reader._active(number)
        number = reader.submit("现在已经切换为男声，可以继续朗读。", speaker="uncle_fu", speed=1.5)
        latency = wait_for("first_audio", number=number)[1]
        done = wait_for("played", number=number)
        results.append({"test": "cancel_resume_voice_speed_change", "first_output_seconds": latency,
                        "buffer_gaps": done[1]})
        write_json(LOCAL / "GPU集成验证.json", results)
        print("GPU_INTEGRATION_CHECK_PASSED", flush=True)
    finally:
        if held:
            U.UnregisterHotKey(None, 8877)
        if reader:
            reader.close()
            reader.thread.join(timeout=10)
        native.close()
        native.thread.join(timeout=2)


if __name__ == "__main__":
    main()
