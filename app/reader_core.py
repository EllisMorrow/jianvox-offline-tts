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
from settings import LOCAL, DEFAULT_STYLE, model_directory
from export_plan import build_export_plan, ExportPlan
from model_worker import ModelClient, ModelFailure
from audio_output import AudioOutput, AudioOutputError
from runtime_errors import describe_error, RuntimeIssue
from audio_gain import apply_volume

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
        cut = len(window)
        if len(window) == limit:
            sentence_ends = [m.end() for m in re.finditer(r"[。！？!?；;]|[.!?](?=\s|$)", window)]
            safe_cut = sentence_ends[-1] if sentence_ends else 0
            if safe_cut < limit // 3:
                whitespace_ends = [m.end() for m in re.finditer(r"\s+", window)]
                safe_cut = whitespace_ends[-1] if whitespace_ends else 0
            if safe_cut >= limit // 3:
                cut = safe_cut
        part = window[:cut]
        if part:
            parts.append(part)
        start += cut
    return parts


def check_generation_limit(metadata: dict, limit: int = 1800) -> None:
    if metadata.get("total_steps_so_far", 0) >= limit:
        raise GenerationLimitError("单段声音生成达到模型上限，请缩短这段文字后重试。")


def check_cache_limit(model) -> None:
    # The pinned backend exposes the last graph position but no EOS reason.
    # Conservatively refuse boundary-touching output instead of claiming completeness.
    graph = model.talker_graph
    if int(graph.cache_position.item()) >= graph.max_seq_len - 2:
        raise GenerationLimitError("单段声音触及模型缓存边界，无法确认完整，请缩短文字后重试。")


class GenerationLimitError(RuntimeError):
    code = "generation_limit"


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
    probe: bool = False
    plan: ExportPlan | None = None


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
            self.volume = min(1.5, max(0.0, float(volume)))

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
                output[position:position+count, 0] = apply_volume(
                    self.current[self.offset:self.offset+count], self.volume)
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
    PLAYBACK_RECOVERY_SECONDS = 15.0

    def __init__(self, events, model_path=None, *, model_factory=None, output_factory=None):
        self.events = events
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.number = 0
        self.pending = None
        self.closed = False
        self.ready = False
        self.export_number = None
        self.probe_number = None
        self.running_number = None
        self.audio = AudioBuffer(events)
        self.output = None
        self.restart_pending = False
        self.loading = True
        self.audio_missing_since = None
        self.model_factory = model_factory or ModelClient
        self.output_factory = output_factory or AudioOutput
        self.model_path = model_path or model_directory()
        self.thread = threading.Thread(target=self._run, name="offline-tts", daemon=True)
        self.thread.start()

    def submit(self, text, speaker="serena", style=DEFAULT_STYLE, started=None, speed=1.0,
               output=None, *, chaptered=False):
        parts = split_text(text, max_chars=1000000 if output else 20000)
        plan = build_export_plan(parts, output, chaptered=chaptered) if output else None
        if not .75 <= speed <= 1.5:
            raise ValueError("语速范围为 0.75–1.50 倍。")
        with self.condition:
            if (getattr(self, "export_number", None) is not None
                    or getattr(self, "probe_number", None) is not None):
                raise ValueError("正在导出或估算音频，请等待完成或停止后再开始。")
            if not self.ready:
                raise ValueError("声音仍在准备中，请等待窗口显示“已就绪”。")
            self.number += 1
            request = Request(self.number, parts, speaker, style, started or time.perf_counter(), speed,
                              Path(output) if output else None, self.audio.volume, plan=plan)
            if output:
                self.export_number = request.number
            self.audio.begin(request.number, request.started, active=not output)
            self.pending = request
            self.condition.notify()
        return request.number

    def probe(self, text, speaker="serena", style=DEFAULT_STYLE):
        normalized = re.sub(r"[\t\r ]+", " ", text).strip()
        if not normalized:
            raise ValueError("没有可用于估算的文字。")
        if len(normalized) > 320:
            raise ValueError("估算文字最多 320 字。")
        with self.condition:
            if (not self.ready or getattr(self, "export_number", None) is not None
                    or getattr(self, "probe_number", None) is not None
                    or self.pending is not None or getattr(self, "running_number", None) is not None):
                raise ValueError("请在没有朗读或导出任务时进行估算。")
            with self.audio.lock:
                if self.audio.active:
                    raise ValueError("请在没有朗读或导出任务时进行估算。")
                self.number += 1
                request = Request(self.number, [normalized], speaker, style,
                                  time.perf_counter(), output=None, probe=True)
                self.probe_number = request.number
                self.pending = request
                self.condition.notify()
        return request.number

    def stop(self, *, expected_number=None):
        with self.condition:
            if expected_number is not None and self.number != expected_number:
                return False
            pending_export = self.pending and self.pending.output
            pending_probe = self.pending and getattr(self.pending, "probe", False)
            if pending_export:
                self.events.put(("export_cancelled", self.pending.number))
                self.export_number = None
            if pending_probe:
                self.events.put(("probe_cancelled", self.pending.number))
                self.probe_number = None
                self.pending.parts.clear()
            self.number += 1
            self.pending = None
            self.audio.begin(self.number, time.perf_counter(), active=False)
            self.condition.notify()
        self.events.put(("stopped", None))
        return True

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

    def restart(self):
        with self.condition:
            if self.closed:
                raise ValueError("程序正在退出。")
            if self.loading:
                raise ValueError("声音正在准备中，请等待当前准备完成。")
            if not self.thread.is_alive():
                raise ValueError("声音工作线程已停止，请退出程序后重新打开。")
            if (self.pending is not None or self.running_number is not None
                    or self.export_number is not None or self.probe_number is not None
                    or self.audio.active):
                raise ValueError("请先停止当前朗读、导出或预估，再重新加载声音。")
            self.ready = False
            self.restart_pending = True
            self.condition.notify()

    def _active(self, number):
        with self.lock:
            return not self.closed and self.number == number

    def _playback_expired(self):
        with self.audio.lock:
            playing, number = self.audio.active, self.audio.number
        if not playing or self.output.available:
            self.audio_missing_since = None
            return None
        if self.audio_missing_since is None or self.audio_missing_since[0] != number:
            self.audio_missing_since = (number, time.monotonic())
        if time.monotonic() - self.audio_missing_since[1] >= self.PLAYBACK_RECOVERY_SECONDS:
            return number
        return None

    def _run_probe_request(self, model, request, np):
        started = time.perf_counter()
        audio_seconds = 0.0
        for part in request.parts:
            if not self._active(request.number):
                break
            iterator = model.stream(part, request.speaker, request.style,
                                    active=lambda: self._active(request.number))
            try:
                for wave, rate, metadata in iterator:
                    if not self._active(request.number):
                        break
                    check_generation_limit(metadata)
                    if rate != 24000:
                        raise RuntimeError(f"声音采样率与播放设备不匹配：{rate}")
                    wave = np.asarray(wave, dtype=np.float32).reshape(-1)
                    if not np.isfinite(wave).all():
                        raise RuntimeError("模型生成了无效声音，请重试。")
                    audio_seconds += len(wave) / rate
            finally:
                iterator.close()
        if self._active(request.number):
            return time.perf_counter()-started, audio_seconds
        return None

    def _finish_probe_request(self, number, result):
        with self.condition:
            active = not self.closed and self.number == number
            if getattr(self, "running_number", None) == number:
                self.running_number = None
            if getattr(self, "probe_number", None) == number:
                self.probe_number = None
            if result is not None and active:
                self.events.put(("probe_done", (number, *result)))
            else:
                self.events.put(("probe_cancelled", number))

    def _render_parts(self, model, request, parts, np, writer, completed, started):
        from tempo import Tempo
        active = lambda: self._active(request.number)
        total = sum(map(len, request.parts))
        last_activity = 0.0
        for part in parts:
            if not active():
                break
            tempo = Tempo(request.speed)
            iterator = model.stream(part, request.speaker, request.style, active=active)
            try:
                for wave, rate, metadata in iterator:
                    if not active():
                        break
                    check_generation_limit(metadata)
                    if rate != 24000:
                        raise ModelFailure("sample_rate")
                    wave = np.asarray(wave, dtype=np.float32).reshape(-1)
                    if not np.isfinite(wave).all():
                        raise ModelFailure("invalid_audio")
                    wave = tempo.process(wave)
                    if len(wave):
                        if writer:
                            writer.write(apply_volume(wave, request.volume))
                            now = time.perf_counter()
                            if now - last_activity >= .5:
                                self.events.put(("export_activity", (
                                    request.number, completed, total)))
                                last_activity = now
                        else:
                            self.audio.put(request.number, wave)
                    # Backpressure is independent of model IPC: the child waits
                    # for the next chunk request instead of queuing more audio.
                    while not writer and self.audio.seconds() > 8 and active():
                        if self._playback_expired() is not None:
                            raise AudioOutputError("Playback device did not recover")
                        with self.condition:
                            self.condition.wait(timeout=.05)
            finally:
                iterator.close()
            if active():
                tail = tempo.process(np.empty(0, dtype=np.float32), final=True)
                if len(tail):
                    if writer:
                        writer.write(apply_volume(tail, request.volume))
                    else:
                        self.audio.put(request.number, tail)
                completed += len(part)
                if writer:
                    elapsed = time.perf_counter() - started
                    eta = elapsed * (total-completed) / max(1, completed)
                    self.events.put(("export_progress", (request.number, completed, total, eta)))
        return completed

    def _run(self):
        model = None
        try:
            from mp3_export import MP3Export, ExportCancelled
            import numpy as np
            model = self.model_factory(self.model_path)
            self.output = self.output_factory(self.events, self.audio.callback)
            load_requested = True
            while True:
                if load_requested:
                    self.ready = False
                    self.loading = True
                    self.events.put(("engine_loading", None))
                    try:
                        if model.start(active=lambda: not self.closed):
                            self.ready = True
                            self.events.put(("ready", ""))
                    except Exception as exc:
                        self.events.put(("issue", describe_error(exc)))
                    finally:
                        self.loading = False
                    load_requested = False
                with self.condition:
                    woke = self.condition.wait_for(
                        lambda: self.closed or self.restart_pending or self.pending is not None, timeout=.25)
                    if self.closed:
                        break
                    if self.restart_pending:
                        self.restart_pending = False
                        model.close()
                        load_requested = True
                        continue
                    if woke:
                        request = self.pending
                        self.pending = None
                        self.running_number = request.number
                if not woke:
                    expired_number = self._playback_expired()
                    if expired_number is not None and self.stop(expected_number=expired_number):
                        self.events.put(("issue", describe_error(AudioOutputError())))
                    continue
                is_probe = request.probe
                started = time.perf_counter()
                completed = saved_chapters = 0
                exported = False
                probe_result = None
                try:
                    if not self.ready:
                        raise ModelFailure("worker_unavailable")
                    if is_probe:
                        probe_result = self._run_probe_request(model, request, np)
                    elif request.output:
                        # Only completed, explicitly requested MP3s survive.
                        # No resume manifest, source text or hidden audio is saved.
                        for chapter in request.plan.chapters:
                            if not self._active(request.number):
                                break
                            with MP3Export(chapter.target) as writer:
                                completed = self._render_parts(model, request, chapter.parts, np,
                                                               writer, completed, started)
                                if not self._active(request.number):
                                    raise ExportCancelled()
                                writer.commit(lambda: self._active(request.number))
                            saved_chapters += 1
                            if request.plan.chaptered:
                                self.events.put(("chapter_saved", (
                                    request.number, saved_chapters, len(request.plan.chapters))))
                        exported = completed == sum(map(len, request.parts)) and self._active(request.number)
                    else:
                        self.output.ensure_available(timeout=3)
                        self._render_parts(model, request, request.parts, np, None, completed, started)
                        if self._active(request.number):
                            self.audio.end(request.number)
                            self.events.put(("generated", (request.number, time.perf_counter()-started)))
                except ExportCancelled:
                    pass
                except Exception as exc:
                    if self.stop(expected_number=request.number):
                        issue = describe_error(exc, exporting=bool(request.output))
                        if saved_chapters and request.plan.chaptered:
                            issue = RuntimeIssue(issue.code, issue.message + f" 已完成的 {saved_chapters} 个分章文件已保留。")
                        self.events.put(("issue", issue))
                finally:
                    if request.output:
                        with self.condition:
                            if self.export_number == request.number:
                                self.export_number = None
                        if exported:
                            self.events.put(("export_done", (request.number, str(request.output))))
                        else:
                            self.events.put(("export_cancelled", request.number))
                    request_number = request.number
                    request.parts.clear()
                    request = chapter = writer = None
                    if is_probe:
                        self._finish_probe_request(request_number, probe_result)
                    else:
                        with self.condition:
                            if self.running_number == request_number:
                                self.running_number = None
                    # A failed or unresponsive child is replaced once, without
                    # replaying failed text. Another load failure waits for the
                    # explicit recovery button rather than retrying forever.
                    if not model.available and not self.closed:
                        load_requested = True
                        self.ready = False
                        self.events.put(("status", "正在恢复本地声音，本次失败的任务不会自动重放…"))
        except Exception as exc:
            self.events.put(("issue", describe_error(exc)))
        finally:
            self.ready = False
            self.loading = False
            if model is not None:
                try:
                    model.close()
                except Exception as exc:
                    self.events.put(("issue", describe_error(exc)))
            if self.output is not None and not self.output.close():
                self.events.put(("issue", RuntimeIssue("audio_unavailable", "播放设备未能及时关闭，请退出后重开程序。")))
