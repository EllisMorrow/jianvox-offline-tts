"""Stream to an OS-cleaned temporary file; publish only after successful completion."""
from pathlib import Path
import shutil
import tempfile
import soundfile as sf


class ExportCancelled(Exception):
    pass


class MP3Export:
    def __init__(self, target):
        self.target = Path(target)
        self.committed = False
        self.temp = self.writer = None

    def __enter__(self):
        if self.target.suffix.lower() != ".mp3":
            raise ValueError("请选择 MP3 文件名。")
        if self.target.exists():
            raise FileExistsError("文件已存在，请换一个文件名；不会覆盖原文件。")
        if not self.target.parent.is_dir():
            raise ValueError("保存目录不存在，请重新选择。")
        self.temp = tempfile.TemporaryFile(mode="w+b", prefix=".qwen-export-", dir=self.target.parent)
        try:
            self.writer = sf.SoundFile(self.temp, mode="w", samplerate=24000, channels=1,
                                        format="MP3", subtype="MPEG_LAYER_III",
                                        bitrate_mode="CONSTANT", compression_level=.5)
        except Exception:
            self.temp.close()
            raise
        return self

    def write(self, wave):
        self.writer.write(wave)

    def commit(self, active):
        self.writer.close()
        self.writer = None
        try:
            self.temp.seek(0, 2)
            encoded_size = self.temp.tell()
            free_bytes = shutil.disk_usage(self.target.parent).free
            margin = max(16 * 1024 * 1024, encoded_size // 20)
            if free_bytes < encoded_size + margin:
                raise OSError("保存目录剩余空间不足，无法安全完成 MP3 文件提交。")
        except Exception:
            self.temp.close()
            self.temp = None
            raise
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
        finally:
            if created and not self.committed:
                self.target.unlink(missing_ok=True)

    def __exit__(self, *_):
        try:
            if self.writer:
                self.writer.close()
        finally:
            if self.temp:
                self.temp.close()
