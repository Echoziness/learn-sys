"""CLI 交互输入层——学生作答的唯一边界。

设计（2026-08-11）：内置 input() 不支持行内编辑，左右键转义序列会变成
输出；打字作答还会引入全角/零宽/surrogate 等脏字符。输入层用
prompt_toolkit（IPython 同款）根治：

- 选择题：方向键 ↑↓ 选择 + Enter 确认（单选列表）——不再打字，
  打字作答引入的一整类判分问题（全角字母、BOM、拼写）从根上消失；
- 回答题：行内编辑（左右键移动光标、可删改、可粘贴）；
- 输入净化在此边界完成：孤立 surrogate → U+FFFD、去零宽/BOM、trim——
  进入系统的第一道门就干净，判分层无需各种归一化补丁；
- 非 TTY（管道/CI/自动化脚本）：回退内置 input()，保持可脚本化。
"""

from __future__ import annotations

import sys
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


def is_interactive() -> bool:
    """仅 TTY 且 stdin 未重定向时启用 prompt_toolkit（对齐业界惯例）。"""
    return sys.stdin.isatty()


def ask_choice(prompt: str, choices: list[Choice], default_index: int = 0) -> str | None:
    """方向键选择（↑↓）+ Enter 确认。返回选中项的 value；中断返回 None。"""
    if not is_interactive():
        labels = "/".join(c.label for c in choices)
        try:
            raw = input(f"{prompt}（输入 {labels}）：").strip()
        except EOFError:
            return None
        norm = raw.upper()
        for c in choices:
            if c.label.upper() == norm or c.value.upper() == norm:
                return c.value
        # 输入与标签对不上时回退默认项（脚本场景的宽容行为）。
        return choices[default_index].value

    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    bindings = KeyBindings()
    selected = [default_index]

    @bindings.add("up")
    def _up(event) -> None:
        selected[0] = (selected[0] - 1) % len(choices)
        _render()

    @bindings.add("down")
    def _down(event) -> None:
        selected[0] = (selected[0] + 1) % len(choices)
        _render()

    def _confirm(event) -> None:
        event.app.exit(result=choices[selected[0]].value)

    bindings.add("enter")(_confirm)
    bindings.add("c-c")(lambda event: event.app.exit(result=None))

    def _render() -> None:
        lines = [prompt, ""]
        for idx, choice in enumerate(choices):
            marker = "▶" if idx == selected[0] else " "
            lines.append(f" {marker} {choice.label}")
        control.text = "\n".join(lines)

    control = FormattedTextControl(text="")
    _render()
    app = Application(
        layout=Layout(Window(control, wrap_lines=False)),
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    try:
        return app.run()
    except KeyboardInterrupt:
        return None


def ask_text(prompt: str) -> str:
    """回答题文本输入：行内编辑 + 边界净化。中断/EOF 返回空串。"""
    if not is_interactive():
        try:
            raw = input(f"{prompt}")
        except EOFError:
            return ""
        return _sanitize(raw)

    from prompt_toolkit.shortcuts import prompt as pt_prompt

    try:
        return _sanitize(pt_prompt(f"{prompt}"))
    except (KeyboardInterrupt, EOFError):
        return ""


__all__ = ["Choice", "ask_choice", "ask_text", "is_interactive", "_sanitize"]
