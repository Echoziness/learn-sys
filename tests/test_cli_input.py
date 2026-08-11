"""cli_input 输入层：净化、非 TTY 回退、选择映射。"""

from scripts.cli_input import Choice, _sanitize, ask_choice, ask_text, is_interactive


def test_sanitize_removes_surrogates():
    assert _sanitize("正常\ud800\udfff文本") == "正常\ufffd\ufffd文本"


def test_sanitize_removes_zero_width():
    assert _sanitize("主键\u200b外键") == "主键外键"
    assert _sanitize("\ufeffA") == "A"


def test_sanitize_trims():
    assert _sanitize("  回答内容  ") == "回答内容"


def test_non_tty_choice_maps_label(monkeypatch):
    """非 TTY 回退 input()：输入标签映射到 value，大小写不敏感。"""
    assert not is_interactive()
    monkeypatch.setattr("builtins.input", lambda _: "B")
    result = ask_choice("题目", [Choice(label="A. 选项一", value="A"), Choice(label="B. 选项二", value="B")])
    assert result == "B"


def test_non_tty_choice_default_on_unmatched(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "乱七八糟")
    result = ask_choice("题目", [Choice(label="A. 选项一", value="A"), Choice(label="B. 选项二", value="B")])
    assert result == "A"  # 匹配不上回退默认项（脚本场景宽容）


def test_non_tty_choice_eof_returns_none(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
    assert ask_choice("题目", [Choice(label="A. 选项一", value="A")]) is None


def test_non_tty_text_sanitized(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "答案\u200b带零宽")
    assert ask_text("作答：") == "答案带零宽"
