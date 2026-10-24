"""Explicit legacy-artifact allowlist. Never sweep arbitrary user output folders."""
from pathlib import Path
import logging
import os
import sys
from settings import LOCAL

LEGACY_FILES = (
    "state.json", "运行日志.txt", "运行日志.txt.1", "运行日志.txt.2", "启动诊断.txt",
    "GPU集成验证.json", "GPU集成验证日志.txt", "安装接入验证.txt", "单元验证日志.txt", "离线依赖预检.txt",
)


def clear_legacy_artifacts(folder=LOCAL):
    folder = Path(folder).resolve()
    for name in LEGACY_FILES:
        path = folder / name
        if path.is_file() and not path.is_symlink() and path.resolve().parent == folder:
            path.unlink()


def quiet_runtime():
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    logging.basicConfig(handlers=[logging.NullHandler()], force=True)
    stream = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr = stream
    return stream
