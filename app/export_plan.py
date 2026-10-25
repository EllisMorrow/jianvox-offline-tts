"""In-memory output plans; filenames never depend on the source text."""
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable


@dataclass(frozen=True)
class ExportChapter:
    index: int
    target: Path
    parts: tuple[str, ...]

    @property
    def characters(self) -> int:
        return sum(map(len, self.parts))


@dataclass(frozen=True)
class ExportPlan:
    chapters: tuple[ExportChapter, ...]
    chaptered: bool

    @property
    def total_characters(self) -> int:
        return sum(chapter.characters for chapter in self.chapters)


def build_export_plan(parts: Iterable[str], target, *, chaptered=False,
                      chapter_chars=3000) -> ExportPlan:
    """Group existing synthesis segments without changing or persisting their text.

    Chapter mode is an explicit request for separate final MP3 files. It does not
    infer article headings, create a resume manifest, or preserve hidden audio.
    """
    if not isinstance(chaptered, bool):
        raise ValueError("分章导出设置无效。")
    if not isinstance(chapter_chars, int) or isinstance(chapter_chars, bool) or chapter_chars < 300:
        raise ValueError("分章字数至少为 300 字。")
    parts = tuple(parts)
    if not parts or any(not isinstance(part, str) or not part for part in parts):
        raise ValueError("没有可导出的文字片段。")
    if any(len(part) > 300 for part in parts):
        raise ValueError("请先把文字分为不超过 300 字的声音片段。")
    target = Path(target)
    if target.suffix.lower() != ".mp3":
        raise ValueError("请选择 MP3 文件名。")
    if not target.parent.is_dir():
        raise ValueError("保存目录不存在，请重新选择。")

    groups = []
    group, count = [], 0
    for part in parts:
        if chaptered and group and count + len(part) > chapter_chars:
            groups.append(tuple(group))
            group, count = [], 0
        group.append(part)
        count += len(part)
    groups.append(tuple(group))
    width = max(3, len(str(len(groups))))
    chapters = tuple(ExportChapter(
        index,
        target.with_name(f"{target.stem}_{index:0{width}d}.mp3") if chaptered else target,
        group,
    ) for index, group in enumerate(groups, 1))
    # This is read-only preflight. MP3Export also uses exclusive creation to
    # protect against files created by another application after this check.
    if any(chapter.target.exists() or chapter.target.is_symlink() for chapter in chapters):
        raise FileExistsError("导出文件已存在，请换一个文件名；不会覆盖原文件。")
    return ExportPlan(chapters, chaptered)
