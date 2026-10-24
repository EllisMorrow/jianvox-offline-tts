"""Download-only Windows helper. No model loading, installation, or global settings."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent
ALLOWED_HOSTS = {
    "modelscope.cn", "www.modelscope.cn", "mirrors.aliyun.com",
    "mirrors.huaweicloud.com", "mirrors.nju.edu.cn",
}


class DownloadError(RuntimeError):
    pass


class Paused(DownloadError):
    pass


def size_text(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:,.2f} {unit}"
        value /= 1000


def time_text(seconds):
    if seconds is None:
        return "正在估算"
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours} 小时 {minutes:02d} 分 {secs:02d} 秒" if hours else f"{minutes} 分 {secs:02d} 秒"


def target_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise DownloadError("下载清单中的文件路径无效。")
    p = PurePosixPath(relative)
    if p.is_absolute() or any(x in ("..", ".") or ":" in x for x in p.parts):
        raise DownloadError("下载清单包含不安全的文件路径。")
    result = root.joinpath(*p.parts).resolve()
    if not result.is_relative_to(root.resolve()):
        raise DownloadError("目标文件超出了下载文件夹。")
    return result


def load_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not data.get("files"):
        raise DownloadError("下载清单为空或版本不受支持。")
    seen = set()
    for item in data["files"]:
        relative = item["path"]
        target_path(BASE, relative)
        if relative.casefold() in seen:
            raise DownloadError("下载清单出现重复文件。")
        seen.add(relative.casefold())
        if not isinstance(item["size"], int) or item["size"] <= 0:
            raise DownloadError("下载清单缺少准确的文件大小。")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise DownloadError("下载清单缺少 SHA-256 校验值。")
        if not item.get("urls"):
            raise DownloadError("下载清单缺少国内下载地址。")
        for url in item["urls"]:
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.username:
                raise DownloadError("下载清单包含未允许的下载来源。")
    return data


def digest(path, cancel=None):
    result = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise Paused("下载已暂停，已有文件和下载片段均已保留。")
            result.update(block)
    return result.hexdigest()


def write_json_atomic(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def folder_lock(root):
    folder = root / "_下载记录"
    folder.mkdir(exist_ok=True)
    handle = (folder / "download.lock").open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise DownloadError("同一文件夹已有下载器在运行，请使用已打开的窗口。") from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


class Downloader:
    def __init__(self, root, manifest, emit, cancel=None, *, curl=None, attempts=3,
                 connect_timeout=20, stall_timeout=90, request_timeout=7200,
                 retry_delay=3):
        self.root = Path(root).resolve()
        self.manifest = manifest
        self.emit = emit
        self.cancel = cancel if cancel is not None else threading.Event()
        self.curl = curl or shutil.which("curl.exe") or shutil.which("curl")
        self.attempts = attempts
        self.connect_timeout = connect_timeout
        self.stall_timeout = stall_timeout
        self.request_timeout = request_timeout
        self.retry_delay = retry_delay
        self.records = []
        self.completed = 0
        self.process = None
        self.started = time.monotonic()
        self.total = sum(item["size"] for item in manifest["files"])
        self.samples = deque()
        self.received_session = 0
        self.present = {}
        self.log_path = self.root / "_下载记录" / "下载日志.txt"

    def log(self, message):
        self.log_path.parent.mkdir(exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
        self.emit({"kind": "log", "text": message})

    def check_cancel(self):
        if self.cancel.is_set():
            raise Paused("下载已暂停；重新打开或点击继续即可断点续传。")

    def progress(self, item, count, file_number, status="正在下载", transferred=0):
        now = time.monotonic()
        self.received_session += max(0, transferred)
        self.samples.append((now, self.received_session))
        while len(self.samples) > 2 and now - self.samples[0][0] > 12:
            self.samples.popleft()
        duration = now - self.samples[0][0]
        speed = ((self.received_session - self.samples[0][1]) / duration
                 if duration >= 1.5 else 0)
        self.present[item["path"]] = min(count, item["size"])
        done = min(self.total, sum(self.present.values()))
        remaining = max(0, self.total - done)
        self.emit({
            "kind": "progress", "status": status, "file": item["path"],
            "number": file_number, "count": len(self.manifest["files"]),
            "done": done, "total": self.total, "file_done": min(count, item["size"]),
            "file_total": item["size"], "speed": speed,
            "eta": remaining / speed if speed > 0 else None,
            "elapsed": now - self.started,
        })

    def preserve_bad(self, path):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        other = path.with_name(path.name + ".校验未通过-" + stamp)
        path.rename(other)
        self.log(f"校验未通过的文件已保留为：{other.name}；将重新下载该文件。")

    def transfer(self, url, partial, item, number):
        command = [self.curl, "--location", "--fail", "--silent", "--show-error",
                   "--proto", "=https", "--proto-redir", "=https",
                   "--connect-timeout", str(self.connect_timeout),
                   "--speed-time", str(self.stall_timeout), "--speed-limit", "1024",
                   "--max-time", str(self.request_timeout),
                   "--max-filesize", str(item["size"]),
                   "--continue-at", "-", "--output", str(partial),
                   "--user-agent", "QwenOfflineDownloader/1.0", url]
        # Local fixture tests explicitly use HTTP; production manifests reject it.
        if urlsplit(url).scheme == "http" and urlsplit(url).hostname in {"127.0.0.1", "localhost"}:
            command[command.index("=https")] = "=http,https"
            command[command.index("=https")] = "=http,https"
        last_count = partial.stat().st_size if partial.exists() else 0
        err_path = self.root / "_下载记录" / "最近一次传输详情.txt"
        with err_path.open("wb") as errors:
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                while self.process.poll() is None:
                    if self.cancel.wait(0.2):
                        raise Paused("下载已暂停，下载片段已保留。")
                    count = partial.stat().st_size if partial.exists() else 0
                    self.progress(item, count, number, transferred=count - last_count)
                    last_count = count
                code = self.process.returncode
            finally:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                self.process = None
        count = partial.stat().st_size if partial.exists() else 0
        self.progress(item, count, number, transferred=count - last_count)
        if code:
            details = err_path.read_text(encoding="utf-8", errors="replace").strip()[-1500:]
            if code == 28:
                raise DownloadError(f"下载超时：连接等待、连续低速/无数据或单次下载时限已触发。{details}")
            if code == 33:
                raise DownloadError("下载来源暂不支持断点续传，片段已保留，没有自动从头重下。" + details)
            raise DownloadError(f"下载失败（错误码 {code}）：{details}")

    def download_one(self, item, number):
        path = target_path(self.root, item["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        self.check_cancel()
        if path.exists():
            self.progress(item, path.stat().st_size, number, "正在校验已有文件")
            if path.stat().st_size == item["size"] and digest(path, self.cancel) == item["sha256"]:
                self.log(f"已完整校验，跳过下载：{item['path']}")
                return "already_verified"
            self.preserve_bad(path)
        last_error = None
        for attempt in range(1, self.attempts + 1):
            self.check_cancel()
            try:
                count = partial.stat().st_size if partial.exists() else 0
                if count > item["size"]:
                    self.preserve_bad(partial)
                    count = 0
                if count < item["size"]:
                    self.log(f"[{number}/{len(self.manifest['files'])}] {item['path']}，"
                             f"本次尝试 {attempt}/{self.attempts}，已有 {size_text(count)}")
                    url = item["urls"][(attempt - 1) % len(item["urls"])]
                    self.transfer(url, partial, item, number)
                if not partial.exists() or partial.stat().st_size != item["size"]:
                    raise DownloadError("下载长度不完整，将保留片段并尝试续传。")
                self.progress(item, item["size"], number, "正在进行 SHA-256 完整校验")
                if digest(partial, self.cancel) != item["sha256"]:
                    self.preserve_bad(partial)
                    raise DownloadError("文件完整性校验失败；不能将此文件视为下载成功。")
                os.replace(partial, path)
                self.log(f"下载并校验成功：{item['path']}")
                return "downloaded_verified"
            except Paused:
                raise
            except (OSError, DownloadError) as exc:
                last_error = exc
                self.log(f"{item['path']}：{exc}")
                if attempt < self.attempts:
                    wait = self.retry_delay * attempt
                    self.log(f"{wait} 秒后重试；同一文件的片段将用于续传。")
                    if self.cancel.wait(wait):
                        self.check_cancel()
        raise DownloadError(f"{item['path']} 连续 {self.attempts} 次未能完成。\n{last_error}\n"
                            "已有完整文件和可续传片段均已保留；网络恢复后重新运行即可。")

    def run(self):
        if not self.curl:
            raise DownloadError("找不到 Windows 自带的 curl 下载工具，请联系配置人员检查系统。")
        with folder_lock(self.root):
            self.log("开始下载。只使用清单列出的国内来源；不会安装环境或加载模型。")
            previous = self.root / "下载完成报告.json"
            if previous.exists():
                previous.rename(self.root / "_下载记录" / ("上次完成报告-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json"))
            existing = 0
            for item in self.manifest["files"]:
                path = target_path(self.root, item["path"])
                partial = path.with_name(path.name + ".part")
                size = min(item["size"], path.stat().st_size if path.exists()
                           else partial.stat().st_size if partial.exists() else 0)
                self.present[item["path"]] = size
                existing += size
            required = max(0, self.total - existing) + 1024 ** 3
            if shutil.disk_usage(self.root).free < required:
                raise DownloadError(f"磁盘剩余空间不足，建议至少保留 {size_text(required)} 可用空间。")
            for number, item in enumerate(self.manifest["files"], 1):
                result = self.download_one(item, number)
                self.completed += item["size"]
                self.records.append({"path": item["path"], "size": item["size"],
                                     "sha256": item["sha256"], "status": result})
                self.progress(item, item["size"], number, "文件校验完成")
            self.check_cancel()
            report = {
                "status": "all_files_sha256_verified", "completed_at": datetime.now().isoformat(),
                "model": self.manifest["model"], "total_bytes": self.total,
                "elapsed_seconds": round(time.monotonic() - self.started, 2),
                "manifest_sha256": self.manifest.get("manifest_sha256"),
                "files": self.records,
                "note": "只证明下载文件完整；未安装环境，未验证 GPU 朗读质量或速度。",
            }
            write_json_atomic(self.root / "下载完成报告.json", report)
            self.log("全部文件下载完成，且 SHA-256 校验通过。可以把下载完成报告交给后续配置人员。")
            self.emit({"kind": "complete", "report": report})


def run_gui(manifest):
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("中文朗读文件下载器 · 国内来源")
    root.geometry("850x640")
    root.minsize(760, 580)
    root.option_add("*Font", ("Microsoft YaHei UI", 10))
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Qwen3-TTS 1.7B CustomVoice 下载准备", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
    total = sum(x["size"] for x in manifest["files"])
    ttk.Label(frame, text=f"模型＋Windows 64 位运行环境安装包｜{len(manifest['files'])} 个文件，共 {size_text(total)}").pack(anchor="w", pady=(8, 2))
    ttk.Label(frame, text="来源：魔搭官方国内仓库、南京大学镜像、阿里云镜像、华为云镜像").pack(anchor="w")
    ttk.Label(frame, text="启动后自动下载；支持暂停续传。只下载文件，不安装、不更改系统设置。", foreground="#555555").pack(anchor="w", pady=(3, 12))
    status = tk.StringVar(value="准备开始，请确认已切换到合适的网络……")
    file_label = tk.StringVar(value="")
    metrics = tk.StringVar(value="预计剩余时间将在下载速度稳定后显示。")
    overall = tk.StringVar(value="总进度 0.00%")
    ttk.Label(frame, textvariable=status, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
    ttk.Label(frame, textvariable=file_label, wraplength=790).pack(anchor="w", pady=6)
    bar = ttk.Progressbar(frame, maximum=100)
    bar.pack(fill="x", pady=3)
    ttk.Label(frame, textvariable=overall).pack(anchor="w", pady=3)
    small_bar = ttk.Progressbar(frame, maximum=100)
    small_bar.pack(fill="x", pady=3)
    ttk.Label(frame, textvariable=metrics, wraplength=790).pack(anchor="w", pady=(4, 12))
    text = tk.Text(frame, height=12, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
    text.pack(fill="both", expand=True)
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))
    events = queue.Queue()
    state = {"thread": None, "cancel": threading.Event(), "closing": False, "complete": False}

    def worker():
        job = Downloader(BASE, manifest, events.put, state["cancel"])
        try:
            job.run()
        except Paused as exc:
            try:
                job.log(str(exc))
            except OSError:
                pass
            events.put({"kind": "paused", "text": str(exc)})
        except Exception as exc:
            try:
                job.log("下载未完成：" + str(exc))
            except OSError:
                pass
            events.put({"kind": "error", "text": str(exc)})

    def start():
        if state["closing"] or (state["thread"] and state["thread"].is_alive()):
            return
        state["cancel"] = threading.Event()
        state["complete"] = False
        start_button.configure(state="disabled")
        pause_button.configure(state="normal")
        status.set("正在检查已有文件并开始下载……")
        state["thread"] = threading.Thread(target=worker, daemon=False)
        state["thread"].start()

    def pause():
        state["cancel"].set()
        status.set("正在暂停并保留下载片段……")
        pause_button.configure(state="disabled")

    start_button = ttk.Button(buttons, text="继续下载 / 重试", command=start)
    start_button.pack(side="left")
    pause_button = ttk.Button(buttons, text="暂停下载", command=pause)
    pause_button.pack(side="left", padx=8)
    ttk.Button(buttons, text="打开文件夹", command=lambda: os.startfile(str(BASE))).pack(side="right")

    def add_log(value):
        text.configure(state="normal")
        text.insert("end", value + "\n")
        text.see("end")
        text.configure(state="disabled")

    def poll():
        try:
            while True:
                event = events.get_nowait()
                kind = event["kind"]
                if kind == "log":
                    add_log(event["text"])
                elif kind == "progress":
                    percent = event["done"] / event["total"] * 100
                    bar["value"] = percent
                    small_bar["value"] = event["file_done"] / event["file_total"] * 100
                    status.set(event["status"])
                    file_label.set(f"文件 {event['number']}/{event['count']}：{event['file']}")
                    overall.set(f"总进度 {percent:.2f}%｜已下载 {size_text(event['done'])} / {size_text(event['total'])}｜"
                                f"剩余 {size_text(event['total'] - event['done'])}")
                    metrics.set(f"速度 {size_text(event['speed'])}/秒｜已用 {time_text(event['elapsed'])}｜"
                                f"预计剩余 {time_text(event['eta'])}（随网速变化；校验期间可能暂停更新）")
                elif kind in {"error", "paused", "complete"}:
                    pause_button.configure(state="disabled")
                    start_button.configure(state="normal")
                    if kind == "complete":
                        state["complete"] = True
                        status.set("全部下载完成，并通过完整性校验")
                        bar["value"] = 100
                        small_bar["value"] = 100
                        if not state["closing"]:
                            messagebox.showinfo("下载完成", "全部文件已下载并校验通过。\n关闭此窗口后，将自动继续安装运行环境。", parent=root)
                    else:
                        status.set("已暂停，进度已保留" if kind == "paused" else "下载未完成：已保留进度")
                        add_log(event["text"])
                        if kind == "error" and not state["closing"]:
                            messagebox.showerror("下载失败或超时", event["text"] + "\n\n请检查网络后点击“继续下载 / 重试”。", parent=root)
        except queue.Empty:
            pass
        if state["closing"] and (not state["thread"] or not state["thread"].is_alive()):
            root.destroy()
            return
        root.after(150, poll)

    def close():
        state["closing"] = True
        state["cancel"].set()
        status.set("正在停止下载，保留进度后关闭……")

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(150, poll)
    root.after(1200, start)
    root.mainloop()
    return state["complete"]


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查清单，不联网，不下载")
    parser.add_argument("--root", type=Path, default=BASE.parent / ".downloads")
    args = parser.parse_args()
    path = Path(__file__).resolve().parent / "download_manifest.json"
    BASE = args.root.resolve()
    manifest = load_manifest(path)
    manifest["manifest_sha256"] = digest(path)
    if args.check:
        print(json.dumps({"files": len(manifest["files"]),
                          "total_bytes": sum(x["size"] for x in manifest["files"]),
                          "origins": sorted({urlsplit(u).hostname for x in manifest["files"] for u in x["urls"]}),
                          "network_used": False}, ensure_ascii=False, indent=2))
        return
    BASE.mkdir(parents=True, exist_ok=True)
    if not run_gui(manifest):
        raise SystemExit(3)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if "--check" in sys.argv:
            print("检查失败：" + str(exc), file=sys.stderr)
            raise SystemExit(1)
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("下载器启动失败", str(exc))
        window.destroy()
        raise SystemExit(1)
