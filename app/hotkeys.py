"""Validation shared by the UI and Windows hotkey boundary."""
from dataclasses import dataclass

MODIFIERS = {"CTRL": (2, "Ctrl"), "ALT": (1, "Alt"), "SHIFT": (4, "Shift"), "WIN": (8, "Win")}
SPECIAL = {"SPACE": (32, "Space"), "HOME": (36, "Home"), "END": (35, "End"),
           "PAGEUP": (33, "PageUp"), "PAGEDOWN": (34, "PageDown"), "INSERT": (45, "Insert")}
DEFAULT_HOTKEYS = {"read": "F4", "stop": "F6", "pause": "F7"}
ACTION_NAMES = {"read": "朗读选字", "stop": "停止", "pause": "暂停 / 继续"}


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    vk: int
    label: str


def parse_hotkey(value):
    parts = [p.strip().upper() for p in value.split("+")]
    if not parts or not parts[-1]:
        raise ValueError("请指定快捷键，例如 F4 或 Ctrl+Alt+R。")
    mods = 0
    for part in parts[:-1]:
        if part not in MODIFIERS or mods & MODIFIERS[part][0]:
            raise ValueError("快捷键中的修饰键无效或重复。")
        mods |= MODIFIERS[part][0]
    key = parts[-1]
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk, label = 111 + int(key[1:]), f"F{int(key[1:])}"
    elif key in SPECIAL:
        vk, label = SPECIAL[key]
    elif len(key) == 1 and key.isascii() and key.isalnum() and mods:
        vk, label = ord(key), key
    else:
        raise ValueError("支持 F1–F24，或修饰键加字母、数字、Space、Home、End 等。")
    if not mods and not label.startswith("F"):
        raise ValueError("普通按键需要组合 Ctrl、Alt、Shift 或 Win，避免影响输入。")
    prefix = [name for bit, name in MODIFIERS.values() if mods & bit]
    canonical = "+".join(prefix + [label])
    if canonical in {"Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+A", "Alt+F4", "Win+L"}:
        raise ValueError("这个快捷键是常用系统操作，请换一个组合。")
    return Hotkey(mods, vk, canonical)


def validate_bindings(values):
    if set(values) != set(DEFAULT_HOTKEYS):
        raise ValueError("需要分别设置朗读、停止与暂停快捷键。")
    if not all(isinstance(value, str) for value in values.values()):
        raise ValueError("快捷键需填写组合名称，或留空禁用。")
    parsed = {action: parse_hotkey(values[action]) for action in DEFAULT_HOTKEYS if values[action].strip()}
    if len({(key.modifiers, key.vk) for key in parsed.values()}) != len(parsed):
        raise ValueError("三个操作不能使用相同的快捷键。")
    return {action: parsed[action].label if action in parsed else "" for action in DEFAULT_HOTKEYS}


class HotkeyMatcher:
    """Match exact chords; retain only modifier state and intercepted trigger keys."""
    MODIFIER_KEYS = {0x10: 4, 0xA0: 4, 0xA1: 4,
                     0x11: 2, 0xA2: 2, 0xA3: 2,
                     0x12: 1, 0xA4: 1, 0xA5: 1, 0x5B: 8, 0x5C: 8}

    def __init__(self, bindings, held_modifiers=()):
        values = validate_bindings(bindings)
        self.chords = {(key.modifiers, key.vk): action
                       for action, value in values.items() if value
                       for key in [parse_hotkey(value)]}
        self.modifiers = set(held_modifiers) & self.MODIFIER_KEYS.keys()
        self.intercepted = set()
        self.trigger_keys = {vk for _, vk in self.chords}
        self.down = set()

    def process(self, vk, down):
        if vk in self.MODIFIER_KEYS:
            if down:
                self.modifiers.add(vk)
            else:
                self.modifiers.discard(vk)
            return False, None
        if not down:
            self.down.discard(vk)
            if vk in self.intercepted:
                self.intercepted.remove(vk)
                return True, None
            return False, None
        if vk in self.intercepted:
            return True, None
        if vk in self.trigger_keys and vk not in self.down:
            self.down.add(vk)
            modifiers = 0
            for key in self.modifiers:
                modifiers |= self.MODIFIER_KEYS[key]
            action = self.chords.get((modifiers, vk))
            if action:
                self.intercepted.add(vk)
                return True, action
        return False, None
