"""Stream to an OS-cleaned temporary file; publish only after successful completion."""
from pathlib import Path
import errno
import shutil
import tempfile
import soundfile as sf


class ExportCancelled(Exception):
    pass


class ExportError(OSError):
    """A safe, actionable failure without private paths or source text."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def export_error(error, *, encoding=False):
    if isinstance(error, ExportError):
        return error
    number, windows = getattr(error, "errno", None), getattr(error, "winerror", None)
    if number == errno.ENOSPC or windows in {39, 112}:
        return ExportError("disk_full", "保存目录空间不足，请释放空间或选择其他磁盘后重试。")
    if isinstance(error, PermissionError) or number in {errno.EACCES, errno.EPERM, errno.EROFS} or windows in {5, 19, 32, 33}:
        return ExportError("save_permission", "无法写入保存目录，可能没有权限、磁盘只读或文件被占用；请选择其他目录。")
    if isinstance(error, FileNotFoundError) or number in {errno.ENOENT, errno.ENOTDIR} or windows in {2, 3, 15, 21}:
        return ExportError("save_unavailable", "保存目录或磁盘已不可用，请重新连接磁盘或选择其他目录。")
    if isinstance(error, FileExistsError) or number == errno.EEXIST:
        return ExportError("file_exists", "导出文件已存在，请换一个文件名；不会覆盖原文件。")
    if encoding:
        return ExportError("mp3_encoder", "MP3 编码失败，请检查音频运行依赖；当前文件不会作为成功结果保存。")
    return ExportError("save_io", "保存音频时发生读写错误，请检查目标磁盘状态或选择其他目录。")


class _CheckedIO:
    """Keep disk failures observable across libsndfile's C callback boundary.

    Exceptions raised inside a CFFI virtual-I/O callback cannot propagate to
    SoundFile.write(). Return a failed I/O result, then check the recorded error
    from normal Python after each encoder call and finalization.
    """
    def __init__(self, file):
        self.file = file
        self.error = None

    def _call(self, method, failure, *args):
        if self.error is not None:
            return failure
        try:
            return getattr(self.file, method)(*args)
        except OSError as exc:
            self.error = exc
            return failure

    def read(self, size):
        return self._call("read", b"", size)

    def write(self, data):
        written = self._call("write", 0, data)
        if self.error is None and written != len(data):
            self.error = OSError(errno.EIO, "Incomplete encoded audio write")
        return written

    def seek(self, offset, whence=0):
        return self._call("seek", -1, offset, whence)

    def tell(self):
        return self._call("tell", -1)

    def check(self):
        if self.error is not None:
            raise export_error(self.error) from None


class MP3Export:
    def __init__(self, target):
        self.target = Path(target)
        self.committed = False
        self.temp = self.writer = None
        self.io = None

    def __enter__(self):
        if self.target.suffix.lower() != ".mp3":
            raise ValueError("请选择 MP3 文件名。")
        if self.target.exists():
            raise FileExistsError("文件已存在，请换一个文件名；不会覆盖原文件。")
        if not self.target.parent.is_dir():
            raise ValueError("保存目录不存在，请重新选择。")
        try:
            self.temp = tempfile.TemporaryFile(mode="w+b", prefix=".qwen-export-", dir=self.target.parent)
        except OSError as exc:
            raise export_error(exc) from None
        self.io = _CheckedIO(self.temp)
        try:
            self.writer = sf.SoundFile(self.io, mode="w", samplerate=24000, channels=1,
                                        format="MP3", subtype="MPEG_LAYER_III",
                                        bitrate_mode="CONSTANT", compression_level=.5)
            self.io.check()
        except Exception as exc:
            try:
                if self.writer is not None:
                    self._close_writer()
            finally:
                self.temp.close()
                self.temp = None
            self.io.check()
            if isinstance(exc, (OSError, RuntimeError, ValueError)):
                raise export_error(exc, encoding=True) from None
            raise
        return self

    def write(self, wave):
        try:
            self.writer.write(wave)
            self.io.check()
        except (OSError, RuntimeError) as exc:
            self.io.check()
            raise export_error(exc, encoding=True) from None

    def _close_writer(self):
        writer, self.writer = self.writer, None
        if writer is not None:
            try:
                writer.close()
                self.io.check()
            except (OSError, RuntimeError) as exc:
                self.io.check()
                raise export_error(exc, encoding=True) from None

    def commit(self, active):
        self._close_writer()
        try:
            self.temp.seek(0, 2)
            encoded_size = self.temp.tell()
            free_bytes = shutil.disk_usage(self.target.parent).free
            margin = max(16 * 1024 * 1024, encoded_size // 20)
            if free_bytes < encoded_size + margin:
                raise ExportError("disk_full", "保存目录空间不足，无法安全保存 MP3；请释放空间或选择其他磁盘。")
        except OSError as exc:
            self.temp.close()
            self.temp = None
            raise export_error(exc) from None
        self.temp.seek(0)
        created = False
        try:
            with self.target.open("xb") as output:
                created = True
                while chunk := self.temp.read(1024 * 1024):
                    if not active():
                        raise ExportCancelled()
                    output.write(chunk)
                if not active():
                    raise ExportCancelled()
            self.committed = True
        except FileExistsError:
            raise FileExistsError("导出文件已存在，请换一个文件名；不会覆盖原文件。") from None
        except OSError as exc:
            raise export_error(exc) from None
        finally:
            if created and not self.committed:
                try:
                    self.target.unlink(missing_ok=True)
                except OSError:
                    raise ExportError("export_cleanup", "未能清理未完成的 MP3，请检查你选择的目标文件并手动删除残留。") from None

    def __exit__(self, exc_type, *_):
        try:
            try:
                self._close_writer()
            except ExportError:
                if exc_type is None:
                    raise
        finally:
            if self.temp:
                self.temp.close()
                self.temp = None
