"""Portable keyboard chord names and Windows virtual-key codes."""
from __future__ import annotations

MODIFIERS = {"Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10, "Win": 0x5B}
KEYS = {**{chr(i): i for i in range(65, 91)},
        **{str(i): 0x30 + i for i in range(10)},
        **{f"F{i}": 0x6F + i for i in range(1, 25)},
        "Space": 0x20, "Tab": 0x09, "Enter": 0x0D, "Esc": 0x1B,
        "Backspace": 0x08, "Delete": 0x2E, "Insert": 0x2D,
        "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
        "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
        "Pause": 0x13, "PrintScreen": 0x2C, "CapsLock": 0x14,
        "NumLock": 0x90, "ScrollLock": 0x91, "Menu": 0x5D,
        ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE,
        "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}
EXTENDED_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
                 0x2C, 0x2D, 0x2E, 0x5B, 0x5D, 0x90, 0x6F}
ALIASES = {name.casefold(): name for name in KEYS | MODIFIERS}
ALIASES.update({"control": "Ctrl", "meta": "Win", "windows": "Win",
                "return": "Enter", "escape": "Esc", "del": "Delete",
                "ins": "Insert", "pgup": "PageUp", "pgdown": "PageDown",
                "pgdn": "PageDown", "print": "PrintScreen"})
SHIFTED_KEYS = dict(zip('!@#$%^&*():+<_>?~{|}"', '1234567890;=,-./`[\\]\''))


def parse_hotkey(text: str) -> tuple[str, tuple[int, ...]]:
    """One main key, optionally preceded by modifiers; never a timed sequence."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请设置通话快捷键，例如 K、Space 或 Ctrl+Alt+K")
    # QKeySequence uses Ctrl++ for the plus key. On a standard keyboard it is Shift+=.
    value = text.strip()
    plus = value.endswith("+")
    parts = (value[:-1].rstrip("+").split("+") if plus else value.split("+"))
    if plus:
        parts = [p for p in parts if p] + ["+"]
    if parts[-1].strip() in SHIFTED_KEYS:
        parts[-1] = SHIFTED_KEYS[parts[-1].strip()]
        if not any(p.strip().casefold() == "shift" for p in parts[:-1]):
            parts.insert(0, "Shift")
    names = [ALIASES.get(p.strip().casefold()) for p in parts]
    if not names or any(n is None for n in names) or names[-1] not in KEYS:
        raise ValueError("不支持此快捷键；请使用字母、数字、F 键、空格、方向键或常用键，可加 Ctrl/Alt/Shift/Win")
    modifiers = names[:-1]
    if any(n not in MODIFIERS for n in modifiers) or len(set(modifiers)) != len(modifiers):
        raise ValueError("快捷键需要一个主键，可搭配 Ctrl、Alt、Shift、Win；不支持连续多组按键")
    ordered = [name for name in MODIFIERS if name in modifiers]
    return "+".join(ordered + [names[-1]]), tuple(MODIFIERS[n] for n in ordered) + (KEYS[names[-1]],)
