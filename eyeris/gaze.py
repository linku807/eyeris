from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class GazeSample:
    x: float | None = None
    y: float | None = None
    blink: bool = False
    message: str = "카메라 연결 대기 중"


class GazeTracker:
    """Reads face/iris landmarks in a worker thread; never opens a camera in tests/UI setup."""

    LEFT_EYE = (33, 160, 158, 133, 153, 144)
    RIGHT_EYE = (362, 385, 387, 263, 373, 380)
    LEFT_IRIS = (468, 469, 470, 471, 472)
    RIGHT_IRIS = (473, 474, 475, 476, 477)

    def __init__(self, camera_index: int = 0, blink_threshold: float = 0.19) -> None:
        self.camera_index = camera_index
        self.blink_threshold = blink_threshold
        self._lock = threading.Lock()
        self._sample = GazeSample()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gaze-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def latest(self) -> GazeSample:
        with self._lock:
            return GazeSample(**self._sample.__dict__)

    @staticmethod
    def _ratio(point, start, end) -> float:
        span = end[0] - start[0]
        return 0.5 if abs(span) < 1e-6 else (point[0] - start[0]) / span

    @staticmethod
    def _ear(eye) -> float:
        import math

        def dist(a, b):
            return math.hypot(a[0] - b[0], a[1] - b[1])

        horizontal = dist(eye[0], eye[3])
        if horizontal < 1e-6:
            return 0.3
        return (dist(eye[1], eye[5]) + dist(eye[2], eye[4])) / (2 * horizontal)

    def _run(self) -> None:
        try:
            import cv2
            import mediapipe as mp
        except ImportError as exc:
            self._publish(GazeSample(message=f"필요한 패키지를 설치하세요: {exc.name}"))
            return

        camera = cv2.VideoCapture(self.camera_index)
        if not camera.isOpened():
            self._publish(GazeSample(message=f"카메라 {self.camera_index}를 열 수 없습니다"))
            return
        try:
            with mp.solutions.face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.55,
                min_tracking_confidence=0.55,
            ) as mesh:
                while not self._stop.is_set():
                    ok, frame = camera.read()
                    if not ok:
                        self._publish(GazeSample(message="카메라 영상을 읽을 수 없습니다"))
                        time.sleep(0.1)
                        continue
                    height, width = frame.shape[:2]
                    result = mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    if not result.multi_face_landmarks:
                        self._publish(GazeSample(message="얼굴을 찾는 중"))
                        continue
                    landmarks = result.multi_face_landmarks[0].landmark
                    point = lambda index: (landmarks[index].x * width, landmarks[index].y * height)
                    left_eye = [point(i) for i in self.LEFT_EYE]
                    right_eye = [point(i) for i in self.RIGHT_EYE]
                    left_iris = [point(i) for i in self.LEFT_IRIS]
                    right_iris = [point(i) for i in self.RIGHT_IRIS]
                    iris_left = (sum(p[0] for p in left_iris) / len(left_iris), sum(p[1] for p in left_iris) / len(left_iris))
                    iris_right = (sum(p[0] for p in right_iris) / len(right_iris), sum(p[1] for p in right_iris) / len(right_iris))
                    # Camera coordinates are mirrored for a natural left/right gaze mapping.
                    # The right eye's landmarks run in the opposite direction; normalize both
                    # ratios to the same camera-left-to-right axis before averaging them.
                    gx = 1.0 - (self._ratio(iris_left, left_eye[0], left_eye[3]) +
                                self._ratio(iris_right, right_eye[3], right_eye[0])) / 2
                    gy = ((iris_left[1] - left_eye[1][1]) / max(1.0, left_eye[5][1] - left_eye[1][1]) +
                          (iris_right[1] - right_eye[1][1]) / max(1.0, right_eye[5][1] - right_eye[1][1])) / 2
                    closed = self._ear(left_eye) < self.blink_threshold and self._ear(right_eye) < self.blink_threshold
                    self._publish(GazeSample(gx, gy, closed, "시선 추적 중"))
        except Exception as exc:  # Keep camera failures visible in the app instead of crashing Tk.
            self._publish(GazeSample(message=f"시선 추적 오류: {exc}"))
        finally:
            camera.release()

    def _publish(self, sample: GazeSample) -> None:
        with self._lock:
            self._sample = sample


class GazeCalibration:
    """Linear mapping from normalized iris coordinates to screen coordinates."""

    def __init__(self) -> None:
        self.points: list[tuple[float, float, float, float]] = []
        self.mapping: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None

    def add(self, gaze_x: float, gaze_y: float, screen_x: float, screen_y: float) -> bool:
        self.points.append((gaze_x, gaze_y, screen_x, screen_y))
        if len(self.points) < 3:
            return False
        self.mapping = (self._fit(2), self._fit(3))
        return True

    def _fit(self, target_index: int) -> tuple[float, float, float]:
        # Least-squares fit for target = a*x + b*y + c, solved by Gaussian elimination.
        matrix = [[0.0] * 4 for _ in range(3)]
        for row in self.points:
            v = [row[0], row[1], 1.0]
            target = row[target_index]
            for i in range(3):
                for j in range(3):
                    matrix[i][j] += v[i] * v[j]
                matrix[i][3] += v[i] * target
        for pivot in range(3):
            best = max(range(pivot, 3), key=lambda i: abs(matrix[i][pivot]))
            if abs(matrix[best][pivot]) < 1e-8:
                return (0.0, 0.0, 0.5)
            matrix[pivot], matrix[best] = matrix[best], matrix[pivot]
            scale = matrix[pivot][pivot]
            matrix[pivot] = [value / scale for value in matrix[pivot]]
            for i in range(3):
                if i == pivot:
                    continue
                factor = matrix[i][pivot]
                matrix[i] = [matrix[i][j] - factor * matrix[pivot][j] for j in range(4)]
        return matrix[0][3], matrix[1][3], matrix[2][3]

    def project(self, x: float, y: float) -> tuple[float, float]:
        if not self.mapping:
            # Initial approximate mapping; calibration is recommended for each user.
            return (min(1.0, max(0.0, (x - 0.2) / 0.6)), min(1.0, max(0.0, (y - 0.2) / 0.6)))
        xfit, yfit = self.mapping
        return (min(1.0, max(0.0, xfit[0] * x + xfit[1] * y + xfit[2])),
                min(1.0, max(0.0, yfit[0] * x + yfit[1] * y + yfit[2])))
