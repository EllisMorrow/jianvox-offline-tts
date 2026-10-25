"""Portable paths and validated preferences. No text or clipboard persistence."""
from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
from hotkeys import DEFAULT_HOTKEYS, validate_bindings

PROJECT = Path(__file__).resolve().parent.parent
LOCAL = PROJECT / ".local"
USER_SETTINGS = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "QwenOfflineReader"
DEFAULT_STYLE = "像与朋友交流那样自然地朗读，语速适中，停顿自然，语气温和。"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    if path.is_file() and path.read_text(encoding="utf-8") == encoded:
        return
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(encoded, encoding="utf-8")
    os.replace(temp, path)


def resolve_install_path(value, project=PROJECT):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("安装路径无效，请重新配置本地安装位置。")
    path = Path(value)
    return (path if path.is_absolute() else Path(project) / path).resolve()


def stored_install_path(path, project=PROJECT):
    path, project = Path(path).resolve(), Path(project).resolve()
    return path.relative_to(project).as_posix() if path.is_relative_to(project) else str(path)


def model_directory():
    install = LOCAL / "install.json"
    if install.exists():
        data = json.loads(install.read_text(encoding="utf-8-sig"))
        if data.get("model_path"):
            return resolve_install_path(data["model_path"])
    return PROJECT / ".downloads" / "模型" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"


@dataclass
class Preferences:
    voice: str = "serena"
    style: str = DEFAULT_STYLE
    theme: str = "system"
    speed: float = 1.0
    volume: float = 0.85
    hotkeys: dict = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    priority_hotkeys: bool = False
    export_directory: str = ""

    def validate(self):
        if self.voice not in {"serena", "vivian", "uncle_fu", "dylan", "eric", "ryan", "aiden", "ono_anna", "sohee"}:
            raise ValueError("请选择有效的声音。")
        if not isinstance(self.theme, str) or self.theme not in {"light", "dark", "system"}:
            raise ValueError("请选择有效的界面主题。")
        if not isinstance(self.style, str) or len(self.style) > 500:
            raise ValueError("语气说明最多 500 字。")
        if not isinstance(self.speed, (float, int)) or not .75 <= self.speed <= 1.5:
            raise ValueError("语速范围为 0.75–1.50 倍。")
        if not isinstance(self.volume, (float, int)) or not 0 <= self.volume <= 1:
            raise ValueError("音量范围为 0–100%。")
        self.hotkeys = validate_bindings(self.hotkeys)
        if not isinstance(self.priority_hotkeys, bool):
            raise ValueError("快捷键优先接管设置无效。")
        if not isinstance(self.export_directory, str) or "\0" in self.export_directory:
            raise ValueError("保存目录无效。")
        return self

    @classmethod
    def load(cls, path=None):
        migrate = path is None
        path = path or USER_SETTINGS / "settings.json"
        legacy = LOCAL / "settings.json"
        try:
            if migrate and not path.exists() and legacy.is_file():
                data = json.loads(legacy.read_text(encoding="utf-8"))
                prefs = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__}).validate()
                prefs.save(path)
                legacy.unlink()  # Remove only the exact, successfully migrated preference file.
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid settings object")
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__}).validate()
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError, AttributeError):
            logging.exception("Preferences unavailable; using defaults without overwriting the file")
            return cls()

    def save(self, path=None):
        self.validate()
        write_json(path or USER_SETTINGS / "settings.json", asdict(self))
