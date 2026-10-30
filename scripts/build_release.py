"""Build a source ZIP from an explicit allowlist. Never walk local payload directories."""
import hashlib
from pathlib import Path
import zipfile
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from version import APP_SLUG, VERSION
FIXED = ["README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY.md", ".gitignore", ".gitattributes", "开始下载.cmd", "启动朗读助手.cmd"]
PATTERNS = {"app": ("*.py",), "scripts": ("*.py", "*.ps1", "*.json", "requirements*.txt"),
            "tests": ("*.py",), "docs": ("*.md",), ".github/workflows": ("*.yml",),
            "assets": ("app.svg", "app.ico", "app.png"),
            "docs/screenshots": ("reader.png", "reader-dark.png", "hotkeys.png"),
            "docs/illustrations": ("reading-flow.png",)}


def release_files(root=ROOT):
    paths = [root / name for name in FIXED]
    for folder, patterns in PATTERNS.items():
        for pattern in patterns:
            paths.extend(sorted((root / folder).glob(pattern)))
    for path in paths:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
            raise RuntimeError(f"Unacceptable release file: {path.relative_to(root)}")
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Release path escaped the project")
        if path.suffix.lower() in {".py", ".md", ".json", ".ps1", ".yml", ".txt", ".cmd", ".svg"}:
            text = path.read_text(encoding="utf-8-sig")
            if re.search(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s\"']+", text, re.I):
                raise RuntimeError(f"Private user path in release file: {path.name}")
            if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{30,})", text):
                raise RuntimeError(f"Possible credential in release file: {path.name}")
    return sorted(set(paths))


def main():
    paths = release_files()
    output = ROOT / "dist" / f"{APP_SLUG}-v{VERSION}-windows-source.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, APP_SLUG + "/" + path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print(f"{len(paths)} source files; {output.stat().st_size} bytes; SHA256 {digest}")
    print(output)


if __name__ == "__main__":
    main()
