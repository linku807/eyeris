from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import ROOT


class ActionRunner:
    """Runs button actions off the UI thread so slow network/scripts do not freeze gaze input."""

    def __init__(self, on_status: Callable[[str], None], discord_webhook_url: str = "") -> None:
        self.on_status = on_status
        self.discord_webhook_url = discord_webhook_url

    def run(self, button: dict[str, Any]) -> None:
        threading.Thread(target=self._run, args=(button,), daemon=True).start()

    def _run(self, button: dict[str, Any]) -> None:
        label = button.get("label", "버튼")
        action = button.get("action", {})
        kind = action.get("type")
        try:
            if kind == "discord":
                self._discord(action, self.discord_webhook_url)
            elif kind == "sound":
                self._sound(action)
            elif kind == "script":
                self._script(button, action)
            else:
                raise ValueError(f"지원하지 않는 동작: {kind}")
            self.on_status(f"'{label}' 실행 완료")
        except Exception as exc:
            self.on_status(f"'{label}' 실행 실패: {exc}")

    @staticmethod
    def _discord(action: dict[str, Any], default_webhook: str = "") -> None:
        webhook = action.get("webhook_url") or default_webhook or os.environ.get("EYE_DISCORD_WEBHOOK")
        if not webhook:
            raise ValueError("config.json의 discord.webhook_url에 웹훅 주소를 설정하세요")
        body = json.dumps({"content": action.get("message", "")}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            webhook, data=body, headers={"Content-Type": "application/json", "User-Agent": "Eyeris/1.0"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord 응답 코드 {response.status}")

    @staticmethod
    def _sound(action: dict[str, Any]) -> None:
        audio_file = action.get("file")
        if audio_file:
            path = Path(audio_file)
            if not path.is_absolute():
                path = ROOT / path
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
            return
        text = action.get("text", "알림")
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception as exc:
            raise RuntimeError(f"음성 출력에 실패했습니다. 음성 엔진/OS 음성을 확인하세요 ({exc})") from exc

    @staticmethod
    def _script(button: dict[str, Any], action: dict[str, Any]) -> None:
        script = Path(action.get("path", ""))
        if not script.is_absolute():
            script = ROOT / script
        if not script.is_file():
            raise FileNotFoundError(f"스크립트 파일을 찾을 수 없습니다: {script}")
        payload = json.dumps({"label": button.get("label"), "action": action}, ensure_ascii=False)
        completed = subprocess.run(
            [sys.executable, str(script), "--payload", payload],
            cwd=str(ROOT), capture_output=True, text=True, timeout=20, check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or f"종료 코드 {completed.returncode}"
            raise RuntimeError(detail[-400:])
