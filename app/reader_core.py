"""Offline inference and bounded, cancellable streaming playback."""
from __future__ import annotations
import os
import re
import time
import logging
import threading
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from contextlib import ExitStack
from settings import LOCAL, DEFAULT_STYLE, model_directory

ROOT = Path(__file__).resolve().parent
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                  HF_HUB_DISABLE_TELEMETRY="1", GRADIO_ANALYTICS_ENABLED="False",
                  DO_NOT_TRACK="1", HF_HOME=str(LOCAL / "cache" / "huggingface"),
                  OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")

VOICES = {"Serena · 温柔女声（中文）": "serena", "Vivian · 明亮女声（中文）": "vivian",
          "Uncle Fu · 成熟男声（中文）": "uncle_fu", "Dylan · 北京男声": "dylan",
          "Eric · 四川男声": "eric", "Ryan · 男声（英语）": "ryan",
          "Aiden · 男声（英语）": "aiden", "Ono Anna · 女声（日语）": "ono_anna",
          "Sohee · 女声（韩语）": "sohee"}


def split_text(text: str, limit: int = 300, max_chars: int = 20000) -> list[str]:
    text = re.sub(r"[\t\r ]+", " ", text).strip()
    if not text:
        raise ValueError("没有可朗读的文字。请先选中文字，或把文字粘贴到窗口中。")
    if len(text) > max_chars:
        raise ValueError(f"一次最多处理 {max_chars} 字，请分次选择。")
    parts = []
    start = 0
    while start < len(text):
        window = text[start:start+limit]
        candidates = [m.end() for m in re.finditer(r"[。！？!?；;\n]", window)]
        cut = candidates[-1] if len(window) == limit and candidates and candidates[-1] >= limit // 3 else len(window)
        part = window[:cut].strip()
        if part:
            parts.append(part)
        start += cut
    return parts


@dataclass
class Request:
    number: int
    parts: list[str]
    speaker: str
    style: str
    started: float
    speed: float = 1.0
    output: Path | None = None
    volume: float = .85


class AudioBuffer:
    """Only the active request can supply audio; cancellation invalidates old chunks."""
    def __init__(self, events):
        self.events = events
        self.lock = threading.Lock()
        self.chunks = deque()
        self.current = None
        self.offset = 0
        self.number = 0
        self.first = False
        self.finished = False
        self.started = 0.0
        self.has_played = False
        self.gaps = 0
        self.gap_active = False
        self.paused = False
        self.active = False
        self.volume = .85

    def begin(self, number, started, active=True):
        with self.lock:
            self.number = number
            self.started = started
            self.chunks.clear()
            self.current = None
            self.offset = 0
            self.first = self.finished = self.has_played = self.gap_active = False
            self.gaps = 0
            self.paused = False
            self.active = active

    def toggle_pause(self):
        with self.lock:
            if not self.active:
                return None
            self.paused = not self.paused
            return self.paused

    def set_volume(self, volume):
        with self.lock:
            self.volume = min(1.0, max(0.0, float(volume)))

    def put(self, number, wave):
        with self.lock:
            if number != self.number:
                return False
            self.chunks.append(wave)
            return True

    def end(self, number):
        with self.lock:
            if self.number == number:
                self.finished = True

    def seconds(self):
        with self.lock:
            frames = sum(len(w) for w in self.chunks)
            if self.current is not None:
                frames += len(self.current) - self.offset
            return frames / 24000

    def callback(self, output, frames, timing, status):
        output.fill(0)
        position = 0
        with self.lock:
            if self.paused:
                return
            if status.output_underflow:
                self.events.put(("audio_warning", "声卡输出发生短暂延迟"))
            while position < frames:
                if self.current is None:
                    if not self.chunks:
                        break
                    self.current = self.chunks.popleft()
                    self.offset = 0
                count = min(frames - position, len(self.current) - self.offset)
                output[position:position+count, 0] = self.current[self.offset:self.offset+count] * self.volume
                position += count
                self.offset += count
                if self.offset == len(self.current):
                    self.current = None
                if not self.first:
                    self.first = True
                    self.events.put(("first_audio", (self.number, time.perf_counter()-self.started)))
            if position:
                self.has_played = True
                self.gap_active = False
            if position < frames and self.has_played and not self.finished and not self.gap_active:
                self.gaps += 1
                self.gap_active = True
            if self.finished and self.current is None and not self.chunks:
                self.finished = self.has_played = False
                self.active = False
                self.events.put(("played", (self.number, self.gaps)))


class Reader:
    def __init__(self, events, model_path=None):
        self.events = events
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.number = 0
        self.pending = None
        self.closed = False
        self.ready = False
        self.export_number = None
        self.audio = AudioBuffer(events)
        self.stream = None
        self.model_path = model_path or model_directory()
        self.thread = threading.Thread(target=self._run, name="offline-tts", daemon=True)
        self.thread.start()

    def submit(self, text, speaker="serena", style=DEFAULT_STYLE, started=None, speed=1.0, output=None):
        parts = split_text(text, max_chars=1000000 if output else 20000)
        if not .75 <= speed <= 1.5:
            raise ValueError("语速范围为 0.75–1.50 倍。")
        with self.condition:
            if getattr(self, "export_number", None) is not None:
                raise ValueError("正在导出音频，请先停止导出，再开始新的朗读或导出。")
            if not self.ready:
                raise ValueError("声音仍在准备中，请等待窗口显示“已就绪”。")
            self.number += 1
            request = Request(self.number, parts, speaker, style, started or time.perf_counter(), speed,
                              Path(output) if output else None, self.audio.volume)
            if output:
                self.export_number = request.number
            self.audio.begin(request.number, request.started, active=not output)
            self.pending = request
            self.condition.notify()
        return request.number

    def stop(self):
        with self.condition:
            pending_export = self.pending and self.pending.output
            if pending_export:
                self.events.put(("export_cancelled", self.pending.number))
                self.export_number = None
            self.number += 1
            self.pending = None
            self.audio.begin(self.number, time.perf_counter(), active=False)
            self.condition.notify()
        self.events.put(("stopped", None))

    def toggle_pause(self):
        paused = self.audio.toggle_pause()
        if paused is not None:
            self.events.put(("paused", (self.number, paused)))
        return paused

    def close(self):
        self.stop()
        with self.condition:
            self.closed = True
            self.condition.notify()

    def _active(self, number):
        with self.lock:
            return not self.closed and self.number == number

    def _run(self):
        try:
            import numpy as np
            import torch
            import sounddevice as sd
            from tempo import Tempo
            from mp3_export import MP3Export, ExportCancelled
            from faster_qwen3_tts import FasterQwen3TTS
            torch.set_num_threads(4)
            torch.set_num_interop_threads(1)
            if not torch.cuda.is_available():
                raise RuntimeError("未检测到可用的 NVIDIA 显卡，无法按当前速度配置启动。")
            if not (self.model_path / "config.json").is_file():
                raise RuntimeError("模型尚未准备好。请先双击项目中的“开始下载.cmd”。")
            self.events.put(("status", "正在载入本地声音，首次启动约需半分钟…"))
            model = FasterQwen3TTS.from_pretrained(
                str(self.model_path),
                device="cuda", dtype=torch.bfloat16, attn_implementation="sdpa",
                max_seq_len=2048, local_files_only=True)
            model.warmup(prefill_len=100)
            self.events.put(("status", "正在预热声音与播放设备…"))
            with torch.inference_mode():
                for _ in model.generate_custom_voice_streaming(
                        text="你好，声音已经准备好了。", speaker="serena", language="Chinese",
                        chunk_size=8, max_new_tokens=150):
                    pass
            api = next((a for a in sd.query_hostapis() if a["name"] == "Windows WASAPI"), None)
            options = {}
            if api and api["default_output_device"] >= 0:
                options = {"device": api["default_output_device"],
                           "extra_settings": sd.WasapiSettings(auto_convert=True)}
            self.stream = sd.OutputStream(samplerate=24000, channels=1, dtype="float32",
                                          latency="low", callback=self.audio.callback, **options)
            self.stream.start()
            self.ready = True
            self.events.put(("ready", str(sd.query_devices(self.stream.device)["name"])))
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.pending is not None)
                    if self.closed:
                        break
                    request = self.pending
                    self.pending = None
                self.events.put(("generating", (request.number, sum(map(len, request.parts)))))
                started = time.perf_counter()
                completed = 0
                total = sum(map(len, request.parts))
                exported = False
                try:
                    with ExitStack() as stack, torch.inference_mode():
                        writer = stack.enter_context(MP3Export(request.output)) if request.output else None
                        for part_index, part in enumerate(request.parts, 1):
                            if not self._active(request.number):
                                break
                            tempo = Tempo(request.speed)
                            iterator = model.generate_custom_voice_streaming(
                                text=part, speaker=request.speaker, language="Auto",
                                instruct=request.style, chunk_size=8, max_new_tokens=1800,
                                temperature=0.7, top_p=0.95)
                            try:
                                for wave, rate, _ in iterator:
                                    if not self._active(request.number):
                                        break
                                    if rate != 24000:
                                        raise RuntimeError(f"声音采样率与播放设备不匹配：{rate}")
                                    wave = np.asarray(wave, dtype=np.float32).reshape(-1)
                                    if not np.isfinite(wave).all():
                                        raise RuntimeError("模型生成了无效声音，请重试。")
                                    wave = tempo.process(wave)
                                    if len(wave):
                                        if writer:
                                            writer.write(wave * request.volume)
                                            self.events.put(("export_activity", (request.number, part_index, len(request.parts))))
                                        else:
                                            self.audio.put(request.number, wave)
                                    # Bound audio memory for long selections; stop/new requests wake this promptly.
                                    while not writer and self.audio.seconds() > 8 and self._active(request.number):
                                        with self.condition:
                                            self.condition.wait(timeout=0.05)
                            finally:
                                iterator.close()
                            if self._active(request.number):
                                tail = tempo.process(np.empty(0, dtype=np.float32), final=True)
                                if len(tail):
                                    if writer:
                                        writer.write(tail * request.volume)
                                    else:
                                        self.audio.put(request.number, tail)
                                completed += len(part)
                                if writer:
                                    elapsed = time.perf_counter() - started
                                    eta = elapsed * (total-completed) / max(1, completed)
                                    self.events.put(("export_progress", (request.number, completed, total, eta)))
                        if self._active(request.number):
                            if writer:
                                writer.commit(lambda: self._active(request.number))
                                exported = True
                            else:
                                self.audio.end(request.number)
                                self.events.put(("generated", (request.number, time.perf_counter()-started)))
                except ExportCancelled:
                    pass
                except Exception as exc:
                    if self._active(request.number):
                        self.stop()
                        self.events.put(("error", f"本次处理失败：{type(exc).__name__}。请检查保存目录与磁盘空间，或重试。"))
                finally:
                    if request.output:
                        with self.condition:
                            if self.export_number == request.number:
                                self.export_number = None
                        if exported:
                            self.events.put(("export_done", (request.number, str(request.output))))
                        else:
                            self.events.put(("export_cancelled", request.number))
                    request.parts.clear()
                    request = part = iterator = wave = tail = tempo = writer = None
        except Exception as exc:
            logging.exception("Reader initialization failed")
            self.events.put(("error", f"启动失败：{exc}"))
        finally:
            self.ready = False
            if self.stream:
                self.stream.close()
