from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import errno
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import soundfile as sf
from mp3_export import MP3Export, ExportCancelled, ExportError
from privacy import clear_legacy_artifacts
import settings
from settings import Preferences
from reader_core import split_text


class ExportTests(unittest.TestCase):
    def wave(self):
        return np.sin(2*np.pi*220*np.arange(24000)/24000).astype(np.float32)*.3

    def test_streaming_mp3_is_decodable_and_no_temporary_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "测试音频.mp3"
            with MP3Export(path) as export:
                wave = self.wave()
                for offset in range(0, len(wave), 3000):
                    export.write(wave[offset:offset+3000])
                export.commit(lambda: True)
            info = sf.info(path)
            self.assertEqual(info.format, "MP3")
            self.assertAlmostEqual(info.duration, 1.0, delta=.1)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_cancel_and_generation_failure_leave_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            for error in [ExportCancelled, RuntimeError]:
                with self.assertRaises(error):
                    with MP3Export(path) as export:
                        export.write(self.wave())
                        raise error()
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_cancel_during_commit_removes_only_new_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with MP3Export(path) as export:
                export.write(self.wave())
                with self.assertRaises(ExportCancelled):
                    export.commit(lambda: False)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_insufficient_space_before_publish_closes_temp_and_creates_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with patch("mp3_export.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
                with self.assertRaisesRegex(OSError, "空间不足"):
                    with MP3Export(path) as export:
                        export.write(self.wave())
                        export.commit(lambda: True)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_existing_file_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            path.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                with MP3Export(path):
                    pass
            self.assertEqual(path.read_bytes(), b"original")

    def test_file_created_during_encoding_is_preserved_at_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with MP3Export(path) as export:
                export.write(self.wave())
                path.write_bytes(b"created by another application")
                with self.assertRaises(FileExistsError):
                    export.commit(lambda: True)
            self.assertEqual(path.read_bytes(), b"created by another application")
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_permission_failure_is_safe_and_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with patch("mp3_export.tempfile.TemporaryFile", side_effect=PermissionError("private/path")):
                with self.assertRaises(ExportError) as caught:
                    with MP3Export(path):
                        pass
            self.assertEqual(caught.exception.code, "save_permission")
            self.assertNotIn("private/path", str(caught.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_unavailable_encoder_closes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with patch("mp3_export.sf.SoundFile", side_effect=RuntimeError("private input")):
                with self.assertRaises(ExportError) as caught:
                    with MP3Export(path):
                        pass
            self.assertEqual(caught.exception.code, "mp3_encoder")
            self.assertNotIn("private input", str(caught.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_disk_full_during_encoding_is_classified_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with self.assertRaises(ExportError) as caught:
                with MP3Export(path) as export:
                    with patch.object(export.writer, "write", side_effect=OSError(errno.ENOSPC, "private/path")):
                        export.write(self.wave())
            self.assertEqual(caught.exception.code, "disk_full")
            self.assertNotIn("private/path", str(caught.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_publish_permission_failure_keeps_target_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with self.assertRaises(ExportError) as caught:
                with MP3Export(path) as export:
                    export.write(self.wave())
                    with patch("mp3_export.Path.open", side_effect=PermissionError("private/path")):
                        export.commit(lambda: True)
            self.assertEqual(caught.exception.code, "save_permission")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_finalization_failure_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with self.assertRaises(ExportError) as caught:
                with MP3Export(path) as export:
                    export.write(self.wave())
                    original_close = export.writer.close
                    def failing_close():
                        original_close()
                        raise RuntimeError("private input")
                    with patch.object(export.writer, "close", side_effect=failing_close):
                        export.commit(lambda: True)
            self.assertEqual(caught.exception.code, "mp3_encoder")
            self.assertFalse(path.exists())

    def test_disk_full_inside_real_codec_callback_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with self.assertRaises(ExportError) as caught:
                with MP3Export(path) as export:
                    with patch.object(export.temp, "write", side_effect=OSError(errno.ENOSPC, "private/path")):
                        # Encoding or finalization must report a callback I/O
                        # failure even if libsndfile itself returns successfully.
                        export.write(np.tile(self.wave(), 10))
                        export.commit(lambda: True)
            self.assertEqual(caught.exception.code, "disk_full")
            self.assertNotIn("private/path", str(caught.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_partial_disk_write_inside_codec_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            with self.assertRaises(ExportError) as caught:
                with MP3Export(path) as export:
                    with patch.object(export.temp, "write", return_value=0):
                        export.write(np.tile(self.wave(), 10))
                        export.commit(lambda: True)
            self.assertEqual(caught.exception.code, "save_io")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_process_crash_releases_temporary_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.mp3"
            script = "import os,sys,numpy as np; from mp3_export import MP3Export; e=MP3Export(sys.argv[1]); e.__enter__(); e.write(np.ones(24000,dtype='float32')*.1); os._exit(7)"
            result = subprocess.run([sys.executable, "-B", "-c", script, str(path)], timeout=10)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_long_text_split_preserves_all_text(self):
        text = "大型文本，需要完整分段。" * 2500
        self.assertGreater(len(text), 20000)
        self.assertEqual("".join(split_text(text, max_chars=1000000)), text)


class PrivacyTests(unittest.TestCase):
    def test_cleanup_preserves_configuration_and_user_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ["运行日志.txt", "state.json", "install.json", "settings.json", "my_audio.mp3"]:
                (folder / name).write_text("test", "utf-8")
            clear_legacy_artifacts(folder)
            self.assertEqual({p.name for p in folder.iterdir()}, {"install.json", "settings.json", "my_audio.mp3"})

    def test_settings_migrate_to_user_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy, user = root / "legacy", root / "roaming"
            legacy.mkdir()
            old = Preferences(speed=1.2)
            old.save(legacy / "settings.json")
            with patch.object(settings, "LOCAL", legacy), patch.object(settings, "USER_SETTINGS", user):
                self.assertEqual(Preferences.load().speed, 1.2)
                self.assertFalse((legacy / "settings.json").exists())
                self.assertTrue((user / "settings.json").is_file())
                # Updating or removing the program directory cannot remove the user's preference file.
                self.assertEqual(Preferences.load().speed, 1.2)
