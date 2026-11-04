"""Non-visual Cheonjiin keyboard model and declarative key layout."""
from __future__ import annotations

from dataclasses import dataclass

from .cheonjiin import CheonjiinInput


@dataclass(frozen=True)
class Key:
    id: str
    label: str
    kind: str
    value: str = ""


KEYS = (
    Key("c:ㄱㅋ", "ㄱ · ㅋ · ㄲ", "consonant", "ㄱㅋ"),
    Key("c:ㄴㄹ", "ㄴ · ㄹ", "consonant", "ㄴㄹ"),
    Key("c:ㄷㅌ", "ㄷ · ㅌ · ㄸ", "consonant", "ㄷㅌ"),
    Key("c:ㅂㅍ", "ㅂ · ㅍ · ㅃ", "consonant", "ㅂㅍ"),
    Key("c:ㅅㅎ", "ㅅ · ㅎ · ㅆ", "consonant", "ㅅㅎ"),
    Key("c:ㅈㅊ", "ㅈ · ㅊ · ㅉ", "consonant", "ㅈㅊ"),
    Key("c:ㅇㅁ", "ㅇ · ㅁ", "consonant", "ㅇㅁ"),
    Key("v:ㅣ", "ㅣ", "vowel", "ㅣ"),
    Key("v:ㆍ", "ㆍ", "vowel", "ㆍ"),
    Key("v:ㅡ", "ㅡ", "vowel", "ㅡ"),
    Key("space", "띄어쓰기", "space"),
    Key("backspace", "한 글자 지우기", "backspace"),
    Key("clear", "전체 지우기", "clear"),
    Key("speak", "말하기", "speak"),
    Key("discord", "Discord 전송", "discord"),
    Key("board", "의사소통 보드", "board"),
)
KEY_BY_ID = {key.id: key for key in KEYS}


class CheonjiinKeyboard:
    """Application-facing keyboard state; rendering and gaze sensors stay outside."""
    def __init__(self, multi_tap_timeout_ms: int = 5000) -> None:
        self.input = CheonjiinInput(multi_tap_timeout_ms)

    @property
    def text(self) -> str:
        return self.input.text

    @property
    def vowel_progress(self) -> str:
        return self.input.vowel_progress

    def activate(self, key_id: str, now_ms: int | None = None) -> tuple[str, str]:
        key = KEY_BY_ID[key_id]
        if key.kind == "consonant":
            value = self.input.press_consonant_group(key.value, now_ms)
            return "input", value
        if key.kind == "vowel":
            return "input", self.input.press_vowel_primitive(key.value)
        if key.kind == "space":
            self.input.press_space()
            return "input", "공백"
        if key.kind == "backspace":
            self.input.backspace()
            return "input", "지움"
        if key.kind == "clear":
            self.input.clear()
            return "input", "전체 삭제"
        return key.kind, self.input.text

    def take_text(self) -> str:
        return self.input.take_text()
