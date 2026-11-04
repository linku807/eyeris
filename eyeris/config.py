from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    grid = config.get("grid", {})
    columns = int(grid.get("columns", 3))
    rows = int(grid.get("rows", 3))
    buttons = config.get("buttons", [])
    if not 9 <= len(buttons) <= 16:
        raise ValueError("config.json의 buttons는 9~16개여야 합니다.")
    if not (1 <= columns <= 4 and 1 <= rows <= 4):
        raise ValueError("grid의 columns와 rows는 1~4 범위여야 합니다.")
    if len(buttons) > columns * rows:
        raise ValueError("버튼 개수가 격자 칸 수보다 많습니다.")
    for index, button in enumerate(buttons):
        if not isinstance(button.get("label"), str) or not button["label"].strip():
            raise ValueError(f"{index + 1}번째 버튼에 label이 필요합니다.")
        action = button.get("action", {})
        if action.get("type") not in {"sound", "discord", "script"}:
            raise ValueError(f"'{button['label']}' 버튼의 action.type이 잘못되었습니다.")
    return config
