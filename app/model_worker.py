"""A supervised, local-only model process with bounded IPC and cancellation."""
from __future__ import annotations

import ctypes
import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelTimings:
    startup: float = 180.0
    chunk: float = 30.0
    cancel: float = 2.0
    shutdown: float = 2.0
    poll: float = .05

    def __post_init__(self):
        if any(value <= 0 for value in (self.startup, self.chunk, self.cancel,
                                        self.shutdown, self.poll)):
            raise ValueError("Model time limits must be positive.")


class ModelFailure(RuntimeError):
    """Only a fixed error category crosses the process boundary."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _WorkerFault(Exception):
    def __init__(self, code, fatal=False):
        self.code, self.fatal = code, fatal


def _error_category(error):
    if isinstance(error, _WorkerFault):
        return error.code, error.fatal
    # Inspect locally, but never send exception strings, source text or paths.
    detail = str(error).lower()
    if type(error).__name__ == "OutOfMemoryError" or "out of memory" in detail:
        return "out_of_memory", True
    if any(value in detail for value in ("cuda", "cublas", "cudnn", "device-side assert")):
        return "gpu_failure", True
    return "generation_failed", True


def _close_iterator(iterator):
    if iterator is not None:
        try:
            iterator.close()
        except Exception:
            # Preserve the original fault; the parent terminates an unresponsive
            # process and never reuses a model after a fatal failure.
            return False
    return True


def _worker(connection):
    """Loaded only in the child; the desktop process never imports torch."""
    from privacy import quiet_runtime
    from settings import LOCAL
    quiet_runtime()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", GRADIO_ANALYTICS_ENABLED="False",
                      DO_NOT_TRACK="1", HF_HOME=str(LOCAL / "cache" / "huggingface"),
                      OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
    iterator = None
    try:
        command = connection.recv()
        if command[0] != "load":
            return
        model_path = Path(command[1])
        if not (model_path / "config.json").is_file():
            raise _WorkerFault("missing_model", True)
        import numpy as np
        import torch
        from faster_qwen3_tts import FasterQwen3TTS
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        if not torch.cuda.is_available():
            raise _WorkerFault("gpu_unavailable", True)
        model = FasterQwen3TTS.from_pretrained(
            str(model_path), device="cuda", dtype=torch.bfloat16,
            attn_implementation="sdpa", max_seq_len=2048, local_files_only=True)
        model.warmup(prefill_len=100)
        with torch.inference_mode():
            for _ in model.generate_custom_voice_streaming(
                    text="你好，声音已经准备好了。", speaker="serena", language="Chinese",
                    chunk_size=8, max_new_tokens=150):
                pass
        connection.send(("ready",))
        while True:
            # EOF also closes idle children when the desktop process disappears.
            command = connection.recv()
            kind = command[0]
            if kind in {"close", "cancel"}:
                closed = _close_iterator(iterator)
                iterator = None
                if kind == "close":
                    return
                if not closed:
                    connection.send(("error", "generation_failed", True))
                    return
                connection.send(("cancelled",))
            elif kind == "stream" and iterator is None:
                _, text, speaker, style = command
                try:
                    iterator = model.generate_custom_voice_streaming(
                        text=text, speaker=speaker, language="Auto", instruct=style,
                        chunk_size=8, max_new_tokens=1800, temperature=.7, top_p=.95)
                    connection.send(("started",))
                finally:
                    text = speaker = style = command = None
            elif kind == "next" and iterator is not None:
                try:
                    with torch.inference_mode():
                        wave, rate, metadata = next(iterator)
                    steps = int(metadata.get("total_steps_so_far", 0))
                    if steps >= 1800:
                        raise _WorkerFault("generation_limit")
                    if rate != 24000:
                        raise _WorkerFault("sample_rate")
                    wave = np.asarray(wave, dtype=np.float32).reshape(-1)
                    if not np.isfinite(wave).all():
                        raise _WorkerFault("invalid_audio")
                    connection.send(("wave", wave, rate, {"total_steps_so_far": steps}))
                    wave = metadata = None
                except StopIteration:
                    iterator = None
                    graph = model.talker_graph
                    if int(graph.cache_position.item()) >= graph.max_seq_len - 2:
                        connection.send(("error", "cache_limit", False))
                    else:
                        connection.send(("done",))
                except Exception as error:
                    code, fatal = _error_category(error)
                    fatal = not _close_iterator(iterator) or fatal
                    iterator = None
                    connection.send(("error", code, fatal))
                    if fatal:
                        return
            else:
                connection.send(("error", "worker_protocol", True))
                return
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
    except Exception as error:
        code, _ = _error_category(error)
        try:
            connection.send(("error", code, True))
        except (OSError, EOFError):
            pass
    finally:
        _close_iterator(iterator)
        connection.close()


class _WindowsJob:
    """Kill the model even if its parent exits without Python cleanup."""
    def __init__(self, process_id):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("minimum", ctypes.c_size_t),
                        ("maximum", ctypes.c_size_t), ("active", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                        ("scheduling", w.DWORD)]

        class IOCounts(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IOCounts),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.SetInformationJobObject.restype = w.BOOL
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.AssignProcessToJobObject.restype = w.BOOL
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        self.kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ModelFailure("worker_supervision")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = None
        try:
            if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ModelFailure("worker_supervision")
            process = kernel.OpenProcess(0x100000 | 0x100 | 0x1, False, process_id)
            if not process or not kernel.AssignProcessToJobObject(handle, process):
                raise ModelFailure("worker_supervision")
            self.handle = handle
        finally:
            if process:
                kernel.CloseHandle(process)
            if self.handle is None:
                kernel.CloseHandle(handle)

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ModelClient:
    """Single-owner model session. No automatic replay of failed user text."""
    def __init__(self, model_path, *, timings=None, worker_target=None):
        self.model_path = str(model_path)
        self.timings = timings or ModelTimings()
        self._target = worker_target or _worker
        self._process = self._connection = self._job = None
        self.available = False

    def _send(self, packet):
        try:
            self._connection.send(packet)
        except (OSError, EOFError, AttributeError) as error:
            self._terminate()
            raise ModelFailure("worker_crashed") from None

    def _receive(self, timeout, *, active=None, timeout_code="worker_timeout"):
        deadline = time.monotonic() + timeout
        while True:
            if active is not None and not active():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate()
                raise ModelFailure(timeout_code)
            try:
                if self._connection.poll(min(self.timings.poll, remaining)):
                    return self._connection.recv()
            except (OSError, EOFError, AttributeError):
                self._terminate()
                raise ModelFailure("worker_crashed") from None
            if self._process is None or not self._process.is_alive():
                self._terminate()
                raise ModelFailure("worker_crashed")

    def _check(self, packet, expected):
        if packet[0] == "error":
            _, code, fatal = packet
            if fatal:
                self._terminate()
            raise ModelFailure(code)
        if packet[0] != expected:
            self._terminate()
            raise ModelFailure("worker_protocol")

    def start(self, active=None):
        if self.available:
            return True
        self.close()
        if active is not None and not active():
            return False
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=self._target, args=(child,),
                                  name="jianvox-model", daemon=True)
        self._connection, self._process = parent, process
        try:
            process.start()
            child.close()
            self._job = _WindowsJob(process.pid)
            self._send(("load", self.model_path))
            packet = self._receive(self.timings.startup, active=active,
                                   timeout_code="startup_timeout")
            if packet is None:
                self._terminate()
                return False
            self._check(packet, "ready")
            self.available = True
            return True
        except Exception:
            child.close()
            self._terminate()
            raise

    def _cancel(self):
        if self._connection is None:
            return
        try:
            self._send(("cancel",))
            deadline = time.monotonic() + self.timings.cancel
            while True:
                packet = self._receive(max(.001, deadline - time.monotonic()))
                if packet[0] == "cancelled":
                    return
                if packet[0] == "error" and packet[2]:
                    self._terminate()
                    return
                if time.monotonic() >= deadline:
                    self._terminate()
                    return
        except ModelFailure:
            self._terminate()

    def stream(self, text, speaker, style, active=None):
        if not self.available:
            raise ModelFailure("worker_unavailable")
        if active is not None and not active():
            return
        completed = False
        self._send(("stream", text, speaker, style))
        text = None
        try:
            packet = self._receive(self.timings.chunk, active=active)
            if packet is None:
                return
            self._check(packet, "started")
            while active is None or active():
                self._send(("next",))
                packet = self._receive(self.timings.chunk, active=active)
                if packet is None:
                    return
                if packet[0] == "done":
                    completed = True
                    return
                self._check(packet, "wave")
                yield packet[1], packet[2], packet[3]
                packet = None
        finally:
            if not completed and self.available:
                try:
                    self._cancel()
                except ModelFailure:
                    # Cleanup must not mask the generation failure or
                    # GeneratorExit. start() still refuses a surviving worker.
                    pass

    def _terminate(self):
        self.available = False
        process = self._process
        if process is not None and process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(self.timings.shutdown)
            if process.is_alive():
                process.kill()
                process.join(self.timings.shutdown)
            if process.is_alive():
                # Keep this handle so a later start cannot create a second model.
                raise ModelFailure("worker_cleanup")
            process.close()
        self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._job is not None:
            self._job.close()
            self._job = None

    def close(self):
        if self._connection is not None:
            try:
                self._connection.send(("close",))
                self._process.join(self.timings.shutdown)
            except (OSError, EOFError, AttributeError):
                pass
        self._terminate()
