"""Requested chapter outputs survive a later chapter's failure or cancellation."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
import soundfile as sf
from export_plan import build_export_plan
from mp3_export import MP3Export, ExportCancelled


class ExportPlanTests(unittest.TestCase):
    def test_single_file_mode_preserves_order_and_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts = ["第一段。", "English and 中文。"]
            target = Path(tmp) / "用户选择.mp3"
            plan = build_export_plan(parts, target)
            self.assertFalse(plan.chaptered)
            self.assertEqual(len(plan.chapters), 1)
            self.assertEqual(plan.chapters[0].target, target)
            self.assertEqual(plan.chapters[0].parts, tuple(parts))
            self.assertEqual(plan.total_characters, sum(map(len, parts)))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_chaptered_mode_preserves_every_segment_and_uses_only_chosen_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts = ["正文不能进入文件名。" * 20] * 39
            target = Path(tmp) / "我的音频.mp3"
            plan = build_export_plan(parts, target, chaptered=True)
            self.assertGreater(len(plan.chapters), 1)
            self.assertEqual([p for chapter in plan.chapters for p in chapter.parts], parts)
            self.assertTrue(all(chapter.characters <= 3000 for chapter in plan.chapters))
            self.assertEqual(plan.chapters[0].target.name, "我的音频_001.mp3")
            self.assertTrue(all(chapter.target.parent == target.parent for chapter in plan.chapters))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_conflict_in_later_chapter_is_detected_before_any_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "audio.mp3"
            conflict = Path(tmp) / "audio_003.mp3"
            conflict.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                build_export_plan(["字" * 300] * 30, target, chaptered=True)
            self.assertEqual(list(Path(tmp).iterdir()), [conflict])
            self.assertEqual(conflict.read_bytes(), b"original")

    def test_invalid_segments_and_options_are_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "audio.mp3"
            for parts in ([], [""], [None], ["字" * 301]):
                with self.assertRaises(ValueError):
                    build_export_plan(parts, target)
            for value in (0, 299, True, "3000"):
                with self.assertRaises(ValueError):
                    build_export_plan(["文字"], target, chapter_chars=value)
            with self.assertRaises(ValueError):
                build_export_plan(["文字"], target, chaptered="true")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_completed_chapter_survives_later_generation_failure_and_cancel(self):
        for failure in (RuntimeError, ExportCancelled):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                plan = build_export_plan(["公开的测试文字。" * 25] * 20,
                                         Path(tmp) / "音频.mp3", chaptered=True)
                self.assertGreater(len(plan.chapters), 1)
                wave = np.sin(np.arange(24000, dtype=np.float32) * .02) * .1
                with self.assertRaises(failure):
                    for chapter in plan.chapters:
                        with MP3Export(chapter.target) as export:
                            export.write(wave)
                            if chapter.index == 2:
                                raise failure()
                            export.commit(lambda: True)
                files = list(Path(tmp).iterdir())
                self.assertEqual(files, [plan.chapters[0].target])
                self.assertAlmostEqual(sf.info(files[0]).duration, 1, delta=.1)

    def test_new_conflict_after_preflight_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_export_plan(["公开测试。"], Path(tmp) / "音频.mp3", chaptered=True)
            target = plan.chapters[0].target
            target.write_bytes(b"another application")
            with self.assertRaises(FileExistsError):
                with MP3Export(target):
                    pass
            self.assertEqual(target.read_bytes(), b"another application")


if __name__ == "__main__":
    unittest.main()
