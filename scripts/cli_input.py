"""CLI 交互输入层——开发自测用，刻意保持轻量（用户主战场在 Web）。

原则（2026-08-11）：CLI 只服务于开发自测与 E2E 排查，不做深度交互投资。
- 行编辑：stdlib readline 一行启用（Unix），左右键可移动光标；
- 输入边界净化保留：surrogate → U+FFFD、去零宽/BOM、trim——这是
  判分层的前提，未来 Web 端的输入也要过同一道净化；
- 选择题：打字输入选项标签（判分层已归一化全角/大小写）；
- 非 TTY（管道/CI）：直接 input()，保持脚本化。
"""

from __future__ import annotations

import contextlib

with contextlib.suppress(ImportError):
    import readline  # noqa: F401  启用行编辑：input() 获得左右键移动光标能力（仅 Unix）
from dataclasses import dataclass


def _sanitize(text: str) -> str:
    """输入边界净化：孤立 surrogate → U+FFFD；去零宽/BOM；两端空白。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            out.append("\ufffd")
        elif code in (0xFEFF, 0x200B, 0x200C, 0x200D):
            continue
        else:
            out.append(ch)
    return "".join(out).strip()


@dataclass(frozen=True)
class Choice:
    """一个可选项：显示标签（label）+ 返回给判分器的值（value）。"""

    label: str
    value: str


def ask_choice(prompt: str, choices: list[Choice], default_index: int = 0) -> str | None:
    """选择题作答：输入选项标签（如 A），映射到 value。中断返回 None。"""
    labels = "/".join(c.label for c in choices)
    try:
        raw = input(f"{prompt}（输入 {labels}）：").strip()
    except EOFError:
        return None
    if not raw:
        return choices[default_index].value
    norm = raw.upper()
    for c in choices:
        if c.label.upper() == norm or c.value.upper() == norm:
            return c.value
    # 匹配不上（乱输入）回退默认项——脚本/误触的宽容行为。
    return choices[default_index].value


def ask_text(prompt: str) -> str:
    """回答题文本输入：行内编辑（readline）+ 边界净化。中断/EOF 返回空串。"""
    try:
        return _sanitize(input(f"{prompt}"))
    except (KeyboardInterrupt, EOFError):
        return ""


__all__ = ["Choice", "ask_choice", "ask_text", "_sanitize"]
