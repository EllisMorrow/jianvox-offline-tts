"""Own the application's sole PortAudio stream and recover default-device changes.

All stream creation, closing and device-list refreshes belong to one thread.
The default endpoint is read through Windows MMDevice, because PortAudio's
enumerated default device does not itself track hot-plugged devices.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
import uuid


class AudioOutputError(RuntimeError):
    """Playback is unavailable; inference and MP3 export remain usable."""


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class WindowsDefaultEndpoint:
    """Thread-owned IMMDeviceEnumerator; eMultimedia matches PortAudio WASAPI."""
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows audio endpoint lookup requires Windows")
        self.ole = ctypes.OleDLL("ole32")
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.ole.CoInitializeEx.restype = ctypes.c_long
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.ole.CoCreateInstance.restype = ctypes.c_long
        self.ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self.pointer = ctypes.c_void_p()
        self.initialized = False
        result = self.ole.CoInitializeEx(None, 0)
        if result < 0:
            raise OSError("Windows audio endpoint lookup could not initialize")
        self.initialized = True
        clsid = _GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
        iid = _GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
        try:
            result = self.ole.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                             ctypes.byref(iid), ctypes.byref(self.pointer))
            if result < 0:
                raise OSError("Windows audio endpoint lookup is unavailable")
        except Exception:
            self.close()
            raise

    @staticmethod
    def _method(pointer, index, result_type, *argument_types):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(table[index])

    def __call__(self):
        endpoint = ctypes.c_void_p()
        method = self._method(self.pointer, 4, ctypes.c_long, ctypes.c_int,
                              ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        # PortAudio's WASAPI enumeration selects eRender/eMultimedia (0/1).
        # Monitoring eConsole instead can miss a changed multimedia default.
        result = method(self.pointer, 0, 1, ctypes.byref(endpoint))
        if result & 0xFFFFFFFF == 0x80070490:  # No default playback endpoint.
            return None
        if result < 0:
            raise OSError("Windows default playback endpoint could not be read")
        identifier = ctypes.c_void_p()
        try:
            result = self._method(endpoint, 5, ctypes.c_long,
                ctypes.POINTER(ctypes.c_void_p))(endpoint, ctypes.byref(identifier))
            if result < 0:
                raise OSError("Windows playback endpoint identifier could not be read")
            return ctypes.wstring_at(identifier)
        finally:
            if identifier:
                self.ole.CoTaskMemFree(identifier)
            self._method(endpoint, 2, ctypes.c_ulong)(endpoint)

    def close(self):
        if self.pointer:
            self._method(self.pointer, 2, ctypes.c_ulong)(self.pointer)
            self.pointer = ctypes.c_void_p()
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False


class AudioOutput:
    """Recoverable output; callers must not create other sounddevice streams.

    Playback calls ``ensure_available`` before submitting text and while waiting
    on a full audio buffer. Export/probe calls do not depend on this object.
    A disconnected device gets at most three opening attempts per endpoint or
    explicit request; reinserting/changing the endpoint starts a fresh attempt.
    """
    def __init__(self, events, callback, *, sd=None, endpoint_factory=None,
                 poll_interval=.75, retry_delays=(0, 1, 3), callback_timeout=4):
        self.events, self.callback = events, callback
        self.sd = sd
        self.endpoint_factory = endpoint_factory or WindowsDefaultEndpoint
        self.poll_interval = poll_interval
        self.retry_delays = retry_delays
        self.callback_timeout = callback_timeout
        self.condition = threading.Condition()
        self.wake = threading.Event()
        self.closed = False
        self.available = False
        self.device_name = ""
        self._stream = None
        self._last_callback = 0.0
        self._callback_failed = False
        self._attempt_request = 0
        self._reported_error = None
        self._owns_portaudio = False
        self._cleanup_ok = True
        self.thread = threading.Thread(target=self._run, name="audio-output", daemon=True)
        self.thread.start()

    def ensure_available(self, timeout=3):
        """Wait for a finite attempt; never retain text or wait indefinitely."""
        deadline = time.monotonic() + max(0, timeout)
        with self.condition:
            if self.available and not self.closed:
                return self.device_name
            if self.closed:
                raise AudioOutputError("播放设备已关闭。请重新启动程序。")
            self._attempt_request += 1
            self.wake.set()
            while not self.available and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(.1, remaining))
            if self.available and not self.closed:
                return self.device_name
        raise AudioOutputError("当前播放设备不可用。请连接耳机或扬声器后重试；MP3 导出仍可使用。")

    def _set_available(self, available, name=""):
        with self.condition:
            available = available and not self.closed
            was_available = self.available
            self.available, self.device_name = available, name if available else ""
            self.condition.notify_all()
        if available:
            self._reported_error = None
            if not was_available:
                self.events.put(("audio_reconnected", name))

    def _report_unavailable(self, message):
        self._set_available(False)
        if message != self._reported_error:
            self._reported_error = message
            self.events.put(("audio_unavailable", message))

    def _audio_callback(self, output, frames, timing, status):
        self._last_callback = time.monotonic()
        try:
            self.callback(output, frames, timing, status)
        except Exception:
            output.fill(0)
            self._callback_failed = True
            self.wake.set()

    def _close_stream(self):
        self._set_available(False)
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass  # A disconnected device may reject abort; still close it.
            try:
                stream.close()
            except Exception:
                # Do not reinitialize PortAudio if closing a native stream did
                # not succeed. Keeping the handle prevents an unsafe refresh.
                return False
            finally:
                self._callback_failed = False
            self._stream = None
        return True

    def _refresh_devices(self):
        # sounddevice 0.5.3 snapshots endpoints at initialization. Refresh only
        # after our sole stream has been CLOSED, on the same owner thread. Do
        # not balance or terminate somebody else's PortAudio initialization.
        if self._stream is not None:
            raise RuntimeError("Cannot refresh PortAudio with a live stream")
        initialized = self.sd._initialized
        if initialized not in (0, 1):
            raise RuntimeError("PortAudio ownership is shared unexpectedly")
        self._owns_portaudio = True
        if initialized:
            self.sd._terminate()
        self.sd._initialize()

    def _open_stream(self):
        if self.sd is None:
            import sounddevice
            self.sd = sounddevice
        self._refresh_devices()
        api = next((api for api in self.sd.query_hostapis()
                    if api["name"] == "Windows WASAPI"), None)
        if not api or api["default_output_device"] < 0:
            raise AudioOutputError("No WASAPI output device")
        device = api["default_output_device"]
        stream = self.sd.OutputStream(samplerate=24000, channels=1, dtype="float32",
            latency="low", callback=self._audio_callback, device=device,
            extra_settings=self.sd.WasapiSettings(auto_convert=True))
        self._stream = stream  # Failed start must still close the created stream.
        self._callback_failed = False
        self._last_callback = time.monotonic()
        self._opened_at = self._last_callback
        stream.start()
        self._set_available(True, str(self.sd.query_devices(device)["name"]))

    def _run(self):
        provider = None
        endpoint = object()
        attempt = 0
        next_attempt = 0.0
        provider_attempt = 0
        provider_next_attempt = 0.0
        request = 0
        try:
            while not self.closed:
                with self.condition:
                    requested = self._attempt_request
                if requested != request:
                    request, attempt, next_attempt = requested, 0, 0.0
                    provider_attempt, provider_next_attempt = 0, 0.0
                if provider is None:
                    if (provider_attempt >= len(self.retry_delays)
                            or time.monotonic() < provider_next_attempt):
                        self.wake.wait(self.poll_interval)
                        self.wake.clear()
                        continue
                    try:
                        provider = self.endpoint_factory()
                    except Exception:
                        provider_attempt += 1
                        if provider_attempt < len(self.retry_delays):
                            provider_next_attempt = time.monotonic() + self.retry_delays[provider_attempt]
                        self._report_unavailable("无法初始化 Windows 播放设备检测，请稍后重新朗读以重试；MP3 导出仍可使用。")
                        self.wake.wait(self.poll_interval)
                        self.wake.clear()
                        continue
                try:
                    current = provider()
                except Exception:
                    self._close_stream()
                    try:
                        if hasattr(provider, "close"):
                            provider.close()
                    finally:
                        provider = None
                    provider_attempt += 1
                    if provider_attempt < len(self.retry_delays):
                        provider_next_attempt = time.monotonic() + self.retry_delays[provider_attempt]
                    self._report_unavailable(
                        "无法读取 Windows 播放设备，正在有限重试；MP3 导出仍可使用。"
                        if provider_attempt < len(self.retry_delays) else
                        "播放设备检测重试失败。请稍后重新朗读以重试；MP3 导出仍可使用。")
                    self.wake.wait(self.poll_interval)
                    self.wake.clear()
                    continue
                provider_attempt = 0
                try:
                    if current != endpoint:
                        if not self._close_stream():
                            raise AudioOutputError("Previous audio stream could not close")
                        endpoint, attempt, next_attempt = current, 0, 0.0
                    if self._stream is not None:
                        healthy = (self._stream.active and not self._callback_failed
                            and time.monotonic() - self._last_callback < self.callback_timeout)
                        if not healthy:
                            if not self._close_stream():
                                raise AudioOutputError("Previous audio stream could not close")
                            attempt += 1
                            next_attempt = time.monotonic() + (
                                self.retry_delays[attempt] if attempt < len(self.retry_delays) else 0)
                            self._report_unavailable("播放设备已断开或停止响应，正在尝试重新连接；MP3 导出仍可使用。")
                        elif time.monotonic() - self._opened_at >= 2:
                            # Several transient disconnects in one short burst
                            # share a retry budget; a genuinely healthy session
                            # earns a fresh budget for a later disconnection.
                            attempt = 0
                    if current is None:
                        self._report_unavailable("未检测到播放设备。连接耳机或扬声器后会自动恢复；MP3 导出仍可使用。")
                    elif self._stream is None and attempt < len(self.retry_delays):
                        if time.monotonic() >= next_attempt:
                            try:
                                self._open_stream()
                            except Exception:
                                self._close_stream()
                                self._report_unavailable("播放设备暂时不可用，正在尝试重新连接；MP3 导出仍可使用。")
                                attempt += 1
                                if attempt < len(self.retry_delays):
                                    next_attempt = time.monotonic() + self.retry_delays[attempt]
                                else:
                                    self._report_unavailable("播放设备重连失败。请检查默认输出设备后重新朗读；MP3 导出仍可使用。")
                except Exception:
                    self._close_stream()
                    self._report_unavailable("无法读取或连接 Windows 播放设备。请检查默认输出设备；MP3 导出仍可使用。")
                self.wake.wait(self.poll_interval)
                self.wake.clear()
        except Exception:
            self._report_unavailable("无法初始化 Windows 播放设备检测；MP3 导出仍可使用。")
        finally:
            try:
                self._cleanup_ok = self._close_stream()
                if self._cleanup_ok and self._owns_portaudio and self.sd is not None:
                    try:
                        # PortAudio's WASAPI COM initialization must be balanced
                        # on this owner thread, before the MMDevice provider.
                        # A count of zero also makes sounddevice's main-thread
                        # atexit handler leave this backend untouched.
                        if self.sd._initialized == 1:
                            self.sd._terminate()
                        elif self.sd._initialized != 0:
                            self._cleanup_ok = False
                    except Exception:
                        self._cleanup_ok = False
            finally:
                if provider is not None and hasattr(provider, "close"):
                    try:
                        provider.close()
                    except Exception:
                        self._cleanup_ok = False

    def close(self, timeout=2):
        with self.condition:
            self.closed = True
            self.available = False
            self.condition.notify_all()
        self.wake.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout)
        return not self.thread.is_alive() and self._cleanup_ok
