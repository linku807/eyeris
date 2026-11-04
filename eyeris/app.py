from __future__ import annotations

import math
import logging
import os
import queue
import sys
import tkinter as tk
from collections import deque
from statistics import median
from tkinter import messagebox
from typing import Any

from .actions import ActionRunner
from .config import load_config
from .gaze import GazeCalibration, GazeTracker
from .keyboard import CheonjiinKeyboard, KEYS


logger = logging.getLogger(__name__)


class CommunicationBoard:
    BG = "#0b1220"
    FG = "#f8fafc"
    MUTED = "#cbd5e1"
    CALIBRATION_SETTLE_SECONDS = 0.5
    CALIBRATION_CAPTURE_SECONDS = 1.5
    MIN_CALIBRATION_SAMPLES = 8
    HEAD_BASELINE_SECONDS = 1.2
    HEAD_CAPTURE_SECONDS = 6.0
    MIN_HEAD_BASELINE_SAMPLES = 8
    MIN_HEAD_MOVEMENT_SAMPLES = 20

    def __init__(self, root: tk.Tk, config: dict[str, Any]) -> None:
        self.root = root
        self.config = config
        self.buttons: list[dict[str, Any]] = config["buttons"]
        self.columns = config["grid"]["columns"]
        self.rows = max(config["grid"]["rows"], math.ceil((len(self.buttons) + 1) / self.columns))
        self.tracker = GazeTracker(
            config.get("camera_index", 0),
            config.get("blink_threshold", 0.19),
            config.get("debug_camera", True),
        )
        self.debug_enabled = config.get("debug_camera", True)
        self.debug_window: tk.Toplevel | None = None
        self.debug_image_label: tk.Label | None = None
        self.debug_photo: tk.PhotoImage | None = None
        self.last_debug_image: str | None = None
        self.calibration = GazeCalibration()
        self.selected: int | None = None
        self.mode = "board"
        self.keyboard = CheonjiinKeyboard()
        self.keyboard_target: str | None = None
        self.last_wink = False
        self.last_click_ms = 0
        self.closing = False
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.gaze_window: deque[tuple[float, float]] = deque(maxlen=5)
        self.cursor_points: deque[tuple[float, float]] = deque(maxlen=5)
        self.calibrating = False
        self.calibration_index = 0
        self.calibration_started = 0.0
        self.calibration_samples: list[tuple[float, float, float, float]] = []
        self.calibration_phase = "points"
        self.head_baseline_samples: list[tuple[float, float, float, float]] = []
        self.head_movement_samples: list[tuple[float, float, float, float]] = []
        self.calibration_targets = [
            (0.08, 0.08), (0.50, 0.08), (0.92, 0.08),
            (0.92, 0.50), (0.92, 0.92), (0.50, 0.92),
            (0.08, 0.92), (0.08, 0.50), (0.50, 0.50),
        ]

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
        self.status = tk.StringVar(value="카메라 연결 중 · 버튼을 바라본 뒤 한쪽 눈을 윙크해 선택하세요")
        tk.Label(header, textvariable=self.status, bg=self.BG, fg=self.MUTED, font=("Malgun Gothic", 12)).pack(side="left", padx=20)
        controls = tk.Frame(header, bg=self.BG)
        controls.pack(side="right")
        tk.Button(controls, text="천지인 키보드", command=self._open_keyboard, font=("Malgun Gothic", 12), padx=12, pady=7).pack(side="left", padx=5)
        tk.Button(controls, text="시선 보정", command=self.start_calibration, font=("Malgun Gothic", 12), padx=12, pady=7).pack(side="left", padx=5)
        tk.Button(controls, text="종료 (Esc)", command=self._exit, font=("Malgun Gothic", 12), padx=12, pady=7).pack(side="left", padx=5)

        self.canvas = tk.Canvas(root, bg=self.BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        if self.debug_enabled:
            self._open_debug_window()
        webhook_url = config.get("discord", {}).get("webhook_url", "")
        self.action_runner = ActionRunner(lambda text: self.status_queue.put(text), webhook_url)
        self.root.after(100, self._tick)
        self.tracker.start()

    def _draw(self) -> None:
        canvas = self.canvas
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        canvas.delete("all")
        if self.mode == "keyboard":
            self._draw_keyboard(width, height)
            return
        gap = max(8, min(width, height) // 55)
        cell_w = (width - gap * (self.columns + 1)) / self.columns
        cell_h = (height - gap * (self.rows + 1)) / self.rows
        board_items = self.buttons + [{"label": "천지인 키보드\n시선으로 열기", "color": "#0f766e", "action": {"type": "keyboard"}}]
        for index, button in enumerate(board_items):
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
            if self.calibration_phase == "points":
                tx, ty = self.calibration_targets[self.calibration_index]
                cx, cy = tx * width, ty * height
                caption = f"노란 점을 바라보세요 · {self.calibration_index + 1}/{len(self.calibration_targets)}"
            else:
                cx, cy = width / 2, height / 2
                caption = "가운데 점을 계속 보면서 머리를 천천히 좌우·위아래로 움직이세요"
            canvas.create_oval(cx - 22, cy - 22, cx + 22, cy + 22, fill="#facc15", outline="white", width=4)
            canvas.create_text(width / 2, 30, text=caption, fill="white", font=("Malgun Gothic", 18, "bold"))
        if self.cursor_points:
            cx, cy = self.cursor_points[-1]
            canvas.create_oval(cx - 15, cy - 15, cx + 15, cy + 15, fill="#fef08a", outline="#111827", width=3)
            canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#111827", outline="")

    def _draw_keyboard(self, width: int, height: int) -> None:
        canvas = self.canvas
        preview_h = height * 0.25
        canvas.create_rectangle(0, 0, width, preview_h, fill="#111c2e", outline="")
        current_text = self.keyboard.text or "입력할 문장을 시선으로 작성하세요"
        canvas.create_text(width * 0.04, preview_h * 0.43, anchor="w", text=current_text,
                           fill=self.FG, font=("Malgun Gothic", max(22, min(48, int(preview_h * 0.22))), "bold"),
                           width=width * 0.92)
        progress = self.keyboard.vowel_progress
        guide = f"모음 입력: {progress}" if progress else "키를 바라본 뒤 한쪽 눈을 윙크하세요 · 자음 순환은 같은 키를 다시 윙크하세요"
        canvas.create_text(width * 0.04, preview_h * 0.82, anchor="w", text=guide,
                           fill=self.MUTED, font=("Malgun Gothic", 14))
        gap = max(7, min(width, height) // 70)
        grid_h = height - preview_h
        cell_w = (width - gap * 5) / 4
        cell_h = (grid_h - gap * 5) / 4
        for index, key in enumerate(KEYS):
            row, col = divmod(index, 4)
            x0, y0 = gap + col * (cell_w + gap), preview_h + gap + row * (cell_h + gap)
            x1, y1 = x0 + cell_w, y0 + cell_h
            focused = key.id == self.keyboard_target
            color = "#155e75" if key.kind in ("consonant", "vowel") else "#334155"
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="#fef08a" if focused else color,
                                    width=7 if focused else 1)
            font_size = max(16, min(36, int(cell_h / max(2.2, len(key.label) * 0.12))))
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=key.label, fill="white",
                               font=("Malgun Gothic", font_size, "bold"), width=cell_w * 0.88)
        if self.calibrating:
            tx, ty = self.calibration_targets[self.calibration_index]
            canvas.create_oval(tx * width - 20, ty * height - 20, tx * width + 20, ty * height + 20,
                               fill="#facc15", outline="white", width=4)
        if self.cursor_points:
            cx, cy = self.cursor_points[-1]
            canvas.create_oval(cx - 15, cy - 15, cx + 15, cy + 15, fill="#fef08a", outline="#111827", width=3)
            canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#111827", outline="")

    def _tick(self) -> None:
        if self.closing:
            return
        sample = self.tracker.latest()
        if sample.x is not None and sample.y is not None:
            import time
            current_ms = int(time.monotonic() * 1000)
            nx, ny = self.calibration.project(
                sample.x, sample.y, sample.head_yaw, sample.head_pitch
            )
            if self.status.get().startswith("카메라 연결 중"):
                self.status.set(sample.message)
            width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
            x, y = nx * width, ny * height
            self.gaze_window.append((x, y))
            x = median(point[0] for point in self.gaze_window)
            y = median(point[1] for point in self.gaze_window)
            if self.cursor_points:
                old_x, old_y = self.cursor_points[-1]
                distance = math.hypot(x - old_x, y - old_y)
                alpha = min(0.72, max(0.16, distance / 90.0))
                x, y = old_x * (1 - alpha) + x * alpha, old_y * (1 - alpha) + y * alpha
            self.cursor_points.append((x, y))
            if self.calibrating:
                if self.calibration_phase == "points":
                    self._collect_calibration(sample)
                else:
                    self._collect_head_calibration(sample)
            elif self.mode == "keyboard":
                self.selected = None
                self.keyboard_target = self._keyboard_key_at(x, y)
            else:
                self.selected = self._nearest_button(x, y)
            if (not self.calibrating and sample.wink and not self.last_wink
                    and current_ms - self.last_click_ms >= int(self.config.get("blink_debounce_ms", 1200))):
                if self.mode == "keyboard" and self.keyboard_target:
                    self.last_click_ms = current_ms
                    self._activate_keyboard_key(self.keyboard_target)
                elif self.mode == "board" and self.selected is not None:
                    self.last_click_ms = current_ms
                    self._activate_selected()
            self.last_wink = sample.wink
        else:
            self.last_wink = False
            self.gaze_window.clear()
            self.cursor_points.clear()
            self.selected = None
            self.keyboard_target = None
            self.status.set(sample.message)
        while True:
            try:
                self.status.set(self.status_queue.get_nowait())
            except queue.Empty:
                break
        self._refresh_debug_window()
        self._draw()
        self.root.after(50, self._tick)

    def _open_debug_window(self) -> None:
        self.debug_window = tk.Toplevel(self.root)
        self.debug_window.title("Eyeris - Gaze Debug")
        self.debug_window.geometry("980x590")
        self.debug_window.configure(bg="#0f172a")
        self.debug_window.protocol("WM_DELETE_WINDOW", self._close_debug_window)
        self.debug_image_label = tk.Label(
            self.debug_window,
            text="카메라 영상 대기 중",
            bg="#0f172a",
            fg="white",
            font=("Malgun Gothic", 16),
        )
        self.debug_image_label.pack(fill="both", expand=True, padx=10, pady=10)

    def _refresh_debug_window(self) -> None:
        if not self.debug_enabled or self.debug_image_label is None:
            return
        image_data = self.tracker.latest_debug_image()
        if not image_data or image_data == self.last_debug_image:
            return
        try:
            photo = tk.PhotoImage(data=image_data, format="png")
            self.debug_image_label.configure(image=photo, text="")
            self.debug_photo = photo
            self.last_debug_image = image_data
        except tk.TclError:
            logger.exception("Could not display the gaze debug frame")
            self._close_debug_window()

    def _close_debug_window(self) -> None:
        self.debug_enabled = False
        self.tracker.debug_enabled = False
        if self.debug_window is not None:
            self.debug_window.destroy()
        self.debug_window = None
        self.debug_image_label = None
        self.debug_photo = None

    def _nearest_button(self, x: float, y: float) -> int | None:
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        col = min(self.columns - 1, max(0, int(x / width * self.columns)))
        row = min(self.rows - 1, max(0, int(y / height * self.rows)))
        index = row * self.columns + col
        if index < len(self.buttons) + 1:
            return index
        # Empty grid slots should focus the closest real button instead of selecting nothing.
        target_x, target_y = x / width, y / height
        return min(range(len(self.buttons)), key=lambda i: self._distance_to_cell(i, target_x, target_y))

    def _distance_to_cell(self, index: int, x: float, y: float) -> float:
        row, col = divmod(index, self.columns)
        return math.hypot(x - (col + 0.5) / self.columns, y - (row + 0.5) / self.rows)

    def _activate_selected(self) -> None:
        if self.selected is None or self.calibrating:
            return
        if self.selected == len(self.buttons):
            self._open_keyboard()
        else:
            self.action_runner.run(self.buttons[self.selected])

    def _open_keyboard(self) -> None:
        self.mode = "keyboard"
        self.keyboard_target = None
        self.status.set("천지인 키보드 · 키를 바라본 뒤 한쪽 눈을 윙크하세요")

    def _keyboard_key_at(self, x: float, y: float) -> str | None:
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        preview_h = height * 0.25
        if y < preview_h:
            return None
        gap = max(7, min(width, height) // 70)
        col = min(3, max(0, int(x / width * 4)))
        row = min(3, max(0, int((y - preview_h) / (height - preview_h) * 4)))
        return KEYS[row * 4 + col].id

    def _activate_keyboard_key(self, key_id: str) -> None:
        event, value = self.keyboard.activate(key_id)
        if event == "input":
            self.status.set(f"입력: {value}")
        elif event == "speak":
            text = self.keyboard.take_text()
            if text.strip():
                self.action_runner.run({"label": "키보드 음성 출력", "action": {"type": "sound", "text": text}})
            else:
                self.status.set("먼저 문장을 입력하세요")
        elif event == "discord":
            text = self.keyboard.take_text()
            if text.strip():
                self.action_runner.run({"label": "키보드 Discord 전송", "action": {"type": "discord", "message": text}})
            else:
                self.status.set("먼저 문장을 입력하세요")
        elif event == "board":
            self.mode = "board"
            self.status.set("의사소통 보드")
        self._draw()

    def start_calibration(self) -> None:
        self.mode = "board"
        self.calibration = GazeCalibration()
        self.gaze_window.clear()
        self.cursor_points.clear()
        self.calibrating = True
        self.calibration_phase = "points"
        self.calibration_index = 0
        self.calibration_samples.clear()
        self.head_baseline_samples.clear()
        self.head_movement_samples.clear()
        import time
        self.calibration_started = time.monotonic()
        self.status.set(f"시선 보정 1/{len(self.calibration_targets)} · 노란 점을 바라보세요")

    def _collect_calibration(self, sample) -> None:
        import time
        elapsed = time.monotonic() - self.calibration_started
        if elapsed < self.CALIBRATION_SETTLE_SECONDS:
            return
        self.calibration_samples.append((
            sample.x, sample.y, sample.head_yaw or 0.0, sample.head_pitch or 0.0
        ))
        if elapsed < self.CALIBRATION_CAPTURE_SECONDS:
            return
        if len(self.calibration_samples) < self.MIN_CALIBRATION_SAMPLES:
            self.calibration_samples.clear()
            self.calibration_started = time.monotonic()
            self.status.set(
                f"시선 보정 {self.calibration_index + 1}/{len(self.calibration_targets)} · "
                "시선을 유지하고 다시 바라봐 주세요"
            )
            return

        gx = median(point[0] for point in self.calibration_samples)
        gy = median(point[1] for point in self.calibration_samples)
        yaw = median(point[2] for point in self.calibration_samples)
        pitch = median(point[3] for point in self.calibration_samples)
        tx, ty = self.calibration_targets[self.calibration_index]
        self.calibration.add(gx, gy, tx, ty, yaw, pitch)
        self.calibration_index += 1
        self.calibration_samples.clear()
        if self.calibration_index >= len(self.calibration_targets):
            if self.calibration.mapping is None:
                self.calibrating = False
                self.status.set("시선 보정 실패 · 점을 정확히 바라보고 다시 보정해 주세요")
                return
            self.calibration_phase = "head"
            self.calibration_started = time.monotonic()
            self.head_baseline_samples.clear()
            self.head_movement_samples.clear()
            self.status.set("가운데 점을 응시하고 머리를 정면으로 잠시 유지해 주세요")
            return
        self.calibration_started = time.monotonic()
        self.status.set(
            f"시선 보정 {self.calibration_index + 1}/{len(self.calibration_targets)} · "
            "노란 점을 바라보세요"
        )

    def _collect_head_calibration(self, sample) -> None:
        import time
        if sample.wink or sample.head_yaw is None or sample.head_pitch is None:
            return
        elapsed = time.monotonic() - self.calibration_started
        row = (sample.x, sample.y, sample.head_yaw, sample.head_pitch)
        if elapsed < self.HEAD_BASELINE_SECONDS:
            self.head_baseline_samples.append(row)
            self.status.set("가운데 점을 보며 머리를 정면으로 유지해 주세요")
            return
        self.head_movement_samples.append(row)
        self.status.set("가운데 점을 계속 보며 머리를 천천히 좌우·위아래로 움직여 주세요")
        if elapsed < self.HEAD_BASELINE_SECONDS + self.HEAD_CAPTURE_SECONDS:
            return

        params = GazeCalibration.fit_head_compensation(
            self.head_baseline_samples, self.head_movement_samples
        )
        self.calibrating = False
        if params is None:
            self.status.set("9점 시선 보정 완료 · 머리 움직임 데이터가 부족해 머리 보정은 적용되지 않았습니다")
            return
        reference = (params[0], params[1])
        fitted = self.calibration.set_head_compensation(reference, params[2], params[3])
        if fitted:
            self.status.set("시선 및 개인별 머리 움직임 보정 완료")
        else:
            self.status.set("9점 시선 보정 완료 · 머리 보정 계산에 실패했습니다")

    def _toggle_fullscreen(self, _event=None) -> None:
        self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))

    def _exit(self, _event=None) -> None:
        if self.closing:
            return
        self.closing = True
        self.tracker.stop()
        self.root.destroy()


def main() -> None:
    log_level_name = os.environ.get("EYE_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        level=log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    root = tk.Tk()

    def report_callback_exception(exc_type, exc_value, exc_traceback) -> None:
        logger.error(
            "Unhandled Tkinter callback exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        root.after(0, lambda: messagebox.showerror("프로그램 오류", str(exc_value)))

    root.report_callback_exception = report_callback_exception
    try:
        config = load_config()
        CommunicationBoard(root, config)
    except Exception as exc:
        logger.exception("Application startup failed")
        messagebox.showerror("설정 오류", str(exc))
        root.destroy()
        return
    root.mainloop()
