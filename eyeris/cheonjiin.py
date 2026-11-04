"""Reusable Korean Cheonjiin input model, independent of Tk and camera hardware."""
from __future__ import annotations

import time

INITIALS = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
MEDIALS = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
FINALS = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
FINAL_INDEX = {char: i for i, char in enumerate(FINALS) if char}
FINAL_SPLIT = {"ㄳ": ("ㄱ", "ㅅ"), "ㄵ": ("ㄴ", "ㅈ"), "ㄶ": ("ㄴ", "ㅎ"), "ㄺ": ("ㄹ", "ㄱ"), "ㄻ": ("ㄹ", "ㅁ"), "ㄼ": ("ㄹ", "ㅂ"), "ㄽ": ("ㄹ", "ㅅ"), "ㄾ": ("ㄹ", "ㅌ"), "ㄿ": ("ㄹ", "ㅍ"), "ㅀ": ("ㄹ", "ㅎ"), "ㅄ": ("ㅂ", "ㅅ")}
FINAL_COMBINE = {parts: whole for whole, parts in FINAL_SPLIT.items()}
FINAL_COMBINE.update({("ㄱ", "ㄱ"): "ㄲ", ("ㅅ", "ㅅ"): "ㅆ"})
VOWEL_COMBINE = {("ㅗ", "ㅏ"): "ㅘ", ("ㅗ", "ㅐ"): "ㅙ", ("ㅗ", "ㅣ"): "ㅚ", ("ㅜ", "ㅓ"): "ㅝ", ("ㅜ", "ㅔ"): "ㅞ", ("ㅜ", "ㅣ"): "ㅟ", ("ㅡ", "ㅣ"): "ㅢ"}
INITIAL_TO_FINAL = {c: c for c in "ㄱㄲㄴㄷㄹㅁㅂㅅㅆㅇㅈㅊㅋㅌㅍㅎ"}


class HangulComposer:
    """Incremental composer with committed text and a single editable syllable."""
    def __init__(self) -> None:
        self._committed = ""
        self.initial: str | None = None
        self.medial: str | None = None
        self.final: str | None = None

    @property
    def text(self) -> str:
        return self._committed + self._render()

    def _render(self) -> str:
        if self.initial is None:
            return ""
        if self.medial is None:
            return self.initial
        li, vi = INITIALS.index(self.initial), MEDIALS.index(self.medial)
        ti = FINAL_INDEX.get(self.final or "", 0)
        return chr(0xAC00 + (li * 21 + vi) * 28 + ti)

    def _commit_current(self) -> None:
        self._committed += self._render()
        self.initial = self.medial = self.final = None

    def press_consonant(self, consonant: str) -> None:
        if consonant not in INITIALS:
            raise ValueError(f"잘못된 초성: {consonant}")
        if self.initial is None:
            self.initial = consonant
        elif self.medial is None:
            self._commit_current()
            self.initial = consonant
        elif self.final is None:
            final = INITIAL_TO_FINAL.get(consonant)
            if final:
                self.final = final
            else:
                self._commit_current()
                self.initial = consonant
        else:
            combined = FINAL_COMBINE.get((self.final, consonant))
            if combined:
                self.final = combined
            else:
                next_initial = consonant
                if self.final in FINAL_SPLIT:
                    self.final, next_initial = FINAL_SPLIT[self.final]
                self._commit_current()
                self.initial = next_initial

    def set_cheonjiin_vowel(self, vowel: str) -> None:
        if vowel not in MEDIALS:
            raise ValueError(f"잘못된 중성: {vowel}")
        if self.initial is None:
            self.initial, self.medial = "ㅇ", vowel
        elif self.medial is None:
            self.medial = vowel
        elif self.final is None:
            self.medial = VOWEL_COMBINE.get((self.medial, vowel), vowel)
        else:
            next_initial = self.final
            if self.final in FINAL_SPLIT:
                self.final, next_initial = FINAL_SPLIT[self.final]
            else:
                self.final = None
            self._commit_current()
            self.initial = next_initial if next_initial in INITIALS else "ㅇ"
            self.medial = vowel

    def space(self) -> None:
        self._commit_current()
        self._committed += " "

    def commit(self) -> str:
        self._commit_current()
        result, self._committed = self._committed, ""
        return result

    def clear(self) -> None:
        self._committed = ""
        self.initial = self.medial = self.final = None

    def backspace(self) -> None:
        if self.final:
            self.final = FINAL_SPLIT[self.final][0] if self.final in FINAL_SPLIT else None
        elif self.medial:
            self.medial = None
        elif self.initial:
            self.initial = None
        elif self._committed:
            self._committed = self._committed[:-1]


VOWEL_SEQUENCES = {
    "ㅣ": "ㅣ", "ㅣㆍ": "ㅏ", "ㅣㆍㅣ": "ㅐ", "ㅣㆍㆍ": "ㅑ", "ㅣㆍㆍㅣ": "ㅒ",
    "ㆍㅣ": "ㅓ", "ㆍㅣㅣ": "ㅔ", "ㆍㆍㅣ": "ㅕ", "ㆍㆍㅣㅣ": "ㅖ",
    "ㅡ": "ㅡ", "ㅡㆍ": "ㅗ", "ㅡㆍㅣ": "ㅚ", "ㅡㆍㅣㆍ": "ㅘ", "ㅡㆍㅣㆍㅣ": "ㅙ", "ㅡㆍㆍ": "ㅛ",
    "ㆍㅡ": "ㅜ", "ㆍㅡㆍㅣ": "ㅝ", "ㆍㅡㆍㅣㅣ": "ㅞ", "ㆍㅡㅣ": "ㅟ", "ㆍㆍㅡ": "ㅠ", "ㅡㅣ": "ㅢ",
}
VOWEL_PREFIXES = {seq[:i] for seq in VOWEL_SEQUENCES for i in range(1, len(seq))}
CONSONANT_GROUPS = {"ㄱㅋ": ("ㄱ", "ㅋ", "ㄲ"), "ㄴㄹ": ("ㄴ", "ㄹ"), "ㄷㅌ": ("ㄷ", "ㅌ", "ㄸ"), "ㅂㅍ": ("ㅂ", "ㅍ", "ㅃ"), "ㅅㅎ": ("ㅅ", "ㅎ", "ㅆ"), "ㅈㅊ": ("ㅈ", "ㅊ", "ㅉ"), "ㅇㅁ": ("ㅇ", "ㅁ")}


class CheonjiinInput:
    """Cheonjiin key map and multi-tap state, usable by any UI front end."""
    def __init__(self, multi_tap_timeout_ms: int = 5000) -> None:
        self.composer = HangulComposer()
        self.multi_tap_timeout_ms = multi_tap_timeout_ms
        self._last_group: str | None = None
        self._last_group_ms = 0
        self._last_group_index = -1
        self._group_snapshot: tuple[str, str | None, str | None, str | None] | None = None
        self._vowel_tokens = ""

    @property
    def text(self) -> str:
        return self.composer.text

    @property
    def vowel_progress(self) -> str:
        return self._vowel_tokens

    def press_consonant_group(self, group: str, now_ms: int | None = None) -> str:
        options = CONSONANT_GROUPS[group]
        now = int(time.monotonic() * 1000) if now_ms is None else now_ms
        same_group = group == self._last_group and now - self._last_group_ms <= self.multi_tap_timeout_ms
        if same_group:
            self._last_group_index = (self._last_group_index + 1) % len(options)
            if self._group_snapshot is not None:
                (self.composer._committed, self.composer.initial,
                 self.composer.medial, self.composer.final) = self._group_snapshot
        else:
            self._last_group_index = 0
            self._group_snapshot = (
                self.composer._committed, self.composer.initial,
                self.composer.medial, self.composer.final,
            )
        self._last_group, self._last_group_ms = group, now
        self._vowel_tokens = ""
        value = options[self._last_group_index]
        self.composer.press_consonant(value)
        return value

    def press_vowel_primitive(self, primitive: str) -> str:
        if primitive not in ("ㅣ", "ㆍ", "ㅡ"):
            raise ValueError(f"잘못된 천지인 키: {primitive}")
        self._last_group = None
        self._group_snapshot = None
        candidate = self._vowel_tokens + primitive
        if candidate not in VOWEL_SEQUENCES and candidate not in VOWEL_PREFIXES:
            candidate = primitive
        self._vowel_tokens = candidate
        vowel = VOWEL_SEQUENCES.get(candidate)
        if vowel:
            self.composer.set_cheonjiin_vowel(vowel)
        return vowel or candidate

    def press_space(self) -> None:
        self._last_group = None
        self._group_snapshot = None
        self._vowel_tokens = ""
        self.composer.space()

    def backspace(self) -> None:
        self._last_group = None
        self._group_snapshot = None
        if self._vowel_tokens:
            self._vowel_tokens = self._vowel_tokens[:-1]
            vowel = VOWEL_SEQUENCES.get(self._vowel_tokens)
            if vowel and self.composer.initial is not None:
                self.composer.set_cheonjiin_vowel(vowel)
            elif self.composer.medial is not None:
                self.composer.medial = None
            return
        self.composer.backspace()

    def clear(self) -> None:
        self._last_group = None
        self._last_group_index = -1
        self._group_snapshot = None
        self._vowel_tokens = ""
        self.composer.clear()

    def take_text(self) -> str:
        self._last_group = None
        self._group_snapshot = None
        self._vowel_tokens = ""
        return self.composer.commit()
