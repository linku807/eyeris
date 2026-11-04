from __future__ import annotations

import math
import queue
import tkinter as tk
from collections import deque
from tkinter import messagebox
from typing import Any

from .actions import ActionRunner
from .config import load_config
from .gaze import GazeCalibration, GazeTracker


class CommunicationBoard:
    BG = "#0b1220"
    FG = "#f8fafc"
    MUTED = "#cbd5e1"

    def __init__(self, root: tk.Tk, config: dict[str, Any]) -> None:
        self.root = root
        self.config = config
        self.buttons: list[dict[str, Any]] = config["buttons"]
        self.columns = config["grid"]["columns"]
        self.rows = config["grid"]["rows"]
        self.tracker = GazeTracker(config.get("camera_index", 0), config.get("blink_threshold", 0.19))
        self.calibration = GazeCalibration()
        self.selected: int | None = None
        self.last_blink = False
        self.last_click_ms = 0
        self.closing = False
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.cursor_points: deque[tuple[float, float]] = deque(maxlen=5)
        self.calibrating = False
        self.calibration_index = 0
        self.calibration_started = 0.0
        self.calibration_samples: list[tuple[float, float]] = []
        self.calibration_targets = [(0.08, 0.10), (0.92, 0.10), (0.92, 0.90), (0.08, 0.90), (0.50, 0.50)]

        root.title("Eyeris 시선 의사소통 보드")
        root.configure(bg=self.BG)
        root.attributes("-fullscreen", True)
        root.bind("<Escape>", self._exit)
        root.bind("<F11>", self._toggle_fullscreen)
        root.bind("<space>", lambda _event: self._activate_selected())
        root.protocol("WM_DELETE_WINDOW", self._exit)

        header = tk.Frame(root, bg=self.BG, padx=18, pady=9)
        header.pack(fill="x")
        title = tk.Label(header, text="시선 의사소통", bg=self.BG, fg=self.FG, font=("Malgun Gothic", 20, "bold"))
        title.pack(side="left")
        self.status = tk.StringVar(value="카메라 연결 중 · 시선을 버튼에 두고 양쪽 눈을 감아 선택하세요")
        tk.Label(header, textvariable=self.status, bg=self.BG, fg=self.MUTED, font=("Malgun Gothic", 12)).pack(side="left", padx=20)
        controls = tk.Frame(header, bg=self.BG)
        controls.pack(side="right")
        tk.Button(controls, text="시선 보정", command=self.start_calibration, font=("Malgun Gothic", 12), padx=12, pady=7).pack(side="left", padx=5)
        tk.Button(controls, text="종료 (Esc)", command=self._exit, font=("Malgun Gothic", 12), padx=12, pady=7).pack(side="left", padx=5)

        self.canvas = tk.Canvas(root, bg=self.BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        webhook_url = config.get("discord", {}).get("webhook_url", "")
        self.action_runner = ActionRunner(lambda text: self.status_queue.put(text), webhook_url)
        self.root.after(100, self._tick)
        self.tracker.start()

    def _draw(self) -> None:
        canvas = self.canvas
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        canvas.delete("all")
        gap = max(8, min(width, height) // 55)
        cell_w = (width - gap * (self.columns + 1)) / self.columns
        cell_h = (height - gap * (self.rows + 1)) / self.rows
        for index, button in enumerate(self.buttons):
            row, col = divmod(index, self.columns)
            x0, y0 = gap + col * (cell_w + gap), gap + row * (cell_h + gap)
            x1, y1 = x0 + cell_w, y0 + cell_h
            color = button.get("color", "#2563eb")
            is_selected = index == self.selected
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="#fef08a" if is_selected else color,
                                    width=8 if is_selected else 1, tags=(f"button-{index}",))
            font_size = max(16, min(42, int(min(cell_w, cell_h) / max(2.4, len(button["label"]) * 0.09))))
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=button["label"], fill="white",
                               font=("Malgun Gothic", font_size, "bold"), width=cell_w * 0.82,
                               tags=(f"button-{index}",))
        if self.calibrating:
            tx, ty = self.calibration_targets[self.calibration_index]
            cx, cy = tx * width, ty * height
            canvas.create_oval(cx - 22, cy - 22, cx + 22, cy + 22, fill="#facc15", outline="white", width=4)
            canvas.create_text(width / 2, 30, text=f"노란 점을 바라보세요 · {self.calibration_index + 1}/{len(self.calibration_targets)}",
                               fill="white", font=("Malgun Gothic", 18, "bold"))
        if self.cursor_points:
            cx, cy = self.cursor_points[-1]
            canvas.create_oval(cx - 15, cy - 15, cx + 15, cy + 15, fill="#fef08a", outline="#111827", width=3)
            canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#111827", outline="")

    def _tick(self) -> None:
        if self.closing:
            return
        sample = self.tracker.latest()
        if sample.x is not None and sample.y is not None:
            nx, ny = self.calibration.project(sample.x, sample.y)
            if self.status.get().startswith("카메라 연결 중"):
                self.status.set(sample.message)
            width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
            x, y = nx * width, ny * height
            if self.cursor_points:
                old_x, old_y = self.cursor_points[-1]
                x, y = old_x * 0.55 + x * 0.45, old_y * 0.55 + y * 0.45
            self.cursor_points.append((x, y))
            if self.calibrating:
                self._collect_calibration(sample.x, sample.y)
            else:
                self.selected = self._nearest_button(x, y)
                if sample.blink and not self.last_blink:
                    import time
                    current_ms = int(time.monotonic() * 1000)
                    if current_ms - self.last_click_ms >= int(self.config.get("blink_debounce_ms", 1200)):
                        self.last_click_ms = current_ms
                        self._activate_selected()
            self.last_blink = sample.blink
        else:
            self.last_blink = False
            self.cursor_points.clear()
            self.selected = None
            self.status.set(sample.message)
        while True:
            try:
                self.status.set(self.status_queue.get_nowait())
            except queue.Empty:
                break
        self._draw()
        self.root.after(50, self._tick)

    def _nearest_button(self, x: float, y: float) -> int | None:
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        col = min(self.columns - 1, max(0, int(x / width * self.columns)))
        row = min(self.rows - 1, max(0, int(y / height * self.rows)))
        index = row * self.columns + col
        if index < len(self.buttons):
            return index
        # Empty grid slots should focus the closest real button instead of selecting nothing.
        target_x, target_y = x / width, y / height
        return min(range(len(self.buttons)), key=lambda i: self._distance_to_cell(i, target_x, target_y))

    def _distance_to_cell(self, index: int, x: float, y: float) -> float:
        row, col = divmod(index, self.columns)
        return math.hypot(x - (col + 0.5) / self.columns, y - (row + 0.5) / self.rows)

    def _activate_selected(self) -> None:
        if self.selected is not None and not self.calibrating:
            self.action_runner.run(self.buttons[self.selected])

    def start_calibration(self) -> None:
        self.calibration = GazeCalibration()
        self.calibrating = True
        self.calibration_index = 0
        self.calibration_samples.clear()
        import time
        self.calibration_started = time.monotonic()
        self.status.set("시선 보정을 시작했습니다")

    def _collect_calibration(self, gx: float, gy: float) -> None:
        import time
        elapsed = time.monotonic() - self.calibration_started
        if elapsed < 0.5:
            return
        self.calibration_samples.append((gx, gy))
        if elapsed < 1.5:
            return
        if self.calibration_samples:
            sx = sum(point[0] for point in self.calibration_samples) / len(self.calibration_samples)
            sy = sum(point[1] for point in self.calibration_samples) / len(self.calibration_samples)
            tx, ty = self.calibration_targets[self.calibration_index]
            self.calibration.add(sx, sy, tx, ty)
        self.calibration_index += 1
        self.calibration_samples.clear()
        if self.calibration_index >= len(self.calibration_targets):
            self.calibrating = False
            self.status.set("시선 보정 완료")
            return
        self.calibration_started = time.monotonic()

    def _toggle_fullscreen(self, _event=None) -> None:
        self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))

    def _exit(self, _event=None) -> None:
        if self.closing:
            return
        self.closing = True
        self.tracker.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        config = load_config()
    except Exception as exc:
        messagebox.showerror("설정 오류", str(exc))
        root.destroy()
        return
    CommunicationBoard(root, config)
    root.mainloop()
