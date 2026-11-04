from __future__ import annotations

import base64
import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median


logger = logging.getLogger(__name__)
MODEL_PATH = Path(__file__).resolve().parent / "assets" / "face_landmarker.task"


@dataclass
class GazeSample:
    x: float | None = None
    y: float | None = None
    wink: bool = False
    message: str = "카메라 연결 대기 중"
    head_yaw: float | None = None
    head_pitch: float | None = None


class GazeTracker:
    """Reads face/iris landmarks in a worker thread; never opens a camera in tests/UI setup."""

    LEFT_EYE = (33, 160, 158, 133, 153, 144)
    RIGHT_EYE = (362, 385, 387, 263, 373, 380)
    LEFT_IRIS = (468, 469, 470, 471, 472)
    RIGHT_IRIS = (473, 474, 475, 476, 477)
    WINK_RELEASE_MARGIN = 0.04
    WINK_HOLD_SECONDS = 0.12

    def __init__(self, camera_index: int = 0, blink_threshold: float = 0.19,
                 debug_enabled: bool = True) -> None:
        self.camera_index = camera_index
        self.blink_threshold = blink_threshold
        self.debug_enabled = debug_enabled
        self._lock = threading.Lock()
        self._sample = GazeSample()
        self._debug_image: str | None = None
        self._last_debug_frame_time = 0.0
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

    def latest_debug_image(self) -> str | None:
        with self._lock:
            return self._debug_image

    @staticmethod
    def _eye_gaze(eye, iris) -> tuple[float, float]:
        """Return iris position in an eye-local coordinate system."""
        start, end = eye[0], eye[3]
        horizontal = (end[0] - start[0], end[1] - start[1])
        horizontal_length = horizontal[0] ** 2 + horizontal[1] ** 2
        horizontal_ratio = 0.5
        if horizontal_length > 1e-6:
            offset = (iris[0] - start[0], iris[1] - start[1])
            horizontal_ratio = (offset[0] * horizontal[0] + offset[1] * horizontal[1]) / horizontal_length

        top = ((eye[1][0] + eye[2][0]) / 2, (eye[1][1] + eye[2][1]) / 2)
        bottom = ((eye[5][0] + eye[4][0]) / 2, (eye[5][1] + eye[4][1]) / 2)
        vertical = (bottom[0] - top[0], bottom[1] - top[1])
        vertical_length = vertical[0] ** 2 + vertical[1] ** 2
        vertical_ratio = 0.5
        if vertical_length > 1e-6:
            offset = (iris[0] - top[0], iris[1] - top[1])
            vertical_ratio = (offset[0] * vertical[0] + offset[1] * vertical[1]) / vertical_length
        return horizontal_ratio, vertical_ratio

    @staticmethod
    def _head_pose(face_transform) -> tuple[float, float] | None:
        """Extract yaw and pitch (radians) from MediaPipe's face rotation matrix."""
        try:
            fx = float(face_transform[0][2])
            fy = float(face_transform[1][2])
            fz = float(face_transform[2][2])
            if not all(math.isfinite(value) for value in (fx, fy, fz)):
                raise ValueError("Non-finite face rotation")
            return math.atan2(fx, fz), math.atan2(fy, math.hypot(fx, fz))
        except (IndexError, TypeError, ValueError):
            logger.debug("Invalid face transformation matrix", exc_info=True)
            return None

    @staticmethod
    def _ear(eye) -> float:
        import math

        def dist(a, b):
            return math.hypot(a[0] - b[0], a[1] - b[1])

        horizontal = dist(eye[0], eye[3])
        if horizontal < 1e-6:
            return 0.3
        return (dist(eye[1], eye[5]) + dist(eye[2], eye[4])) / (2 * horizontal)

    @classmethod
    def _update_wink(cls, left_ear: float, right_ear: float, threshold: float,
                     wink_eye: str | None, started_at: float | None, now: float
                     ) -> tuple[str | None, float | None, bool]:
        """Track a one-eye closure with hysteresis so gaze does not switch eyes mid-wink."""
        if wink_eye == "left":
            if left_ear >= threshold + cls.WINK_RELEASE_MARGIN or right_ear < threshold:
                wink_eye = None
        elif wink_eye == "right":
            if right_ear >= threshold + cls.WINK_RELEASE_MARGIN or left_ear < threshold:
                wink_eye = None
        elif left_ear < threshold <= right_ear:
            wink_eye = "left"
        elif right_ear < threshold <= left_ear:
            wink_eye = "right"

        if wink_eye is None:
            return None, None, False
        if started_at is None:
            started_at = now
        return wink_eye, started_at, now - started_at >= cls.WINK_HOLD_SECONDS

    def _run(self) -> None:
        try:
            import cv2
            import mediapipe as mp
        except ImportError as exc:
            logger.exception("Gaze tracking dependency import failed")
            self._publish(GazeSample(message=f"필요한 패키지를 설치하세요: {exc.name}"))
            return

        if not MODEL_PATH.is_file():
            message = f"Face Landmarker 모델 파일을 찾을 수 없습니다: {MODEL_PATH}"
            logger.error(message)
            self._publish(GazeSample(message=message))
            return

        camera = None
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            camera = cv2.VideoCapture(self.camera_index)
            if not camera.isOpened():
                logger.error("Camera %s could not be opened", self.camera_index)
                self._publish(GazeSample(message=f"카메라 {self.camera_index}를 열 수 없습니다"))
                return
            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(MODEL_PATH)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.55,
                min_face_presence_confidence=0.55,
                min_tracking_confidence=0.55,
                output_facial_transformation_matrixes=True,
            )
            with vision.FaceLandmarker.create_from_options(options) as landmarker:
                frame_read_failed = False
                last_timestamp_ms = -1
                wink_candidate_since: float | None = None
                wink_eye: str | None = None
                last_gaze: tuple[float, float] | None = None
                while not self._stop.is_set():
                    ok, frame = camera.read()
                    if not ok:
                        wink_candidate_since = None
                        wink_eye = None
                        last_gaze = None
                        if not frame_read_failed:
                            logger.warning("Camera %s stopped returning frames", self.camera_index)
                            frame_read_failed = True
                        self._publish(GazeSample(message="카메라 영상을 읽을 수 없습니다"))
                        time.sleep(0.1)
                        continue
                    frame_read_failed = False
                    height, width = frame.shape[:2]
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                    timestamp_ms = max(last_timestamp_ms + 1, int(time.monotonic() * 1000))
                    last_timestamp_ms = timestamp_ms
                    result = landmarker.detect_for_video(mp_image, timestamp_ms)
                    if not result.face_landmarks:
                        wink_candidate_since = None
                        wink_eye = None
                        last_gaze = None
                        self._publish_debug_frame(cv2, frame, "얼굴을 찾는 중")
                        self._publish(GazeSample(message="얼굴을 찾는 중"))
                        continue
                    landmarks = result.face_landmarks[0]
                    face_transform = (
                        result.facial_transformation_matrixes[0]
                        if result.facial_transformation_matrixes else None
                    )
                    head_pose = self._head_pose(face_transform) if face_transform is not None else None
                    point = lambda index: (landmarks[index].x * width, landmarks[index].y * height)
                    left_eye = [point(i) for i in self.LEFT_EYE]
                    right_eye = [point(i) for i in self.RIGHT_EYE]
                    left_iris = [point(i) for i in self.LEFT_IRIS]
                    right_iris = [point(i) for i in self.RIGHT_IRIS]
                    iris_left = (sum(p[0] for p in left_iris) / len(left_iris), sum(p[1] for p in left_iris) / len(left_iris))
                    iris_right = (sum(p[0] for p in right_iris) / len(right_iris), sum(p[1] for p in right_iris) / len(right_iris))
                    left_gaze = self._eye_gaze(left_eye, iris_left)
                    # Both eye groups are ordered from image-left to image-right. Keep
                    # that common axis so the two eyes reinforce instead of canceling.
                    right_gaze = self._eye_gaze(right_eye, iris_right)
                    left_ear, right_ear = self._ear(left_eye), self._ear(right_eye)
                    threshold = self.blink_threshold
                    wink_eye, wink_candidate_since, wink = self._update_wink(
                        left_ear, right_ear, threshold, wink_eye, wink_candidate_since, time.monotonic()
                    )
                    if wink_eye is not None:
                        # Keep the selected target stable while one eye is closing.
                        if last_gaze is not None:
                            gaze_x, gaze_y = last_gaze
                        else:
                            wink_gaze = right_gaze if wink_eye == "left" else left_gaze
                            gaze_x, gaze_y = 1.0 - wink_gaze[0], wink_gaze[1]
                    else:
                        left_open = left_ear >= threshold
                        right_open = right_ear >= threshold
                        if left_open and right_open:
                            eye_gaze = ((left_gaze[0] + right_gaze[0]) / 2,
                                        (left_gaze[1] + right_gaze[1]) / 2)
                        elif left_open:
                            eye_gaze = left_gaze
                        elif right_open:
                            eye_gaze = right_gaze
                        else:
                            last_gaze = None
                            self._publish(GazeSample(message="눈을 뜨고 화면을 바라보세요"))
                            continue
                        gaze_x, gaze_y = 1.0 - eye_gaze[0], eye_gaze[1]
                        last_gaze = (gaze_x, gaze_y)
                    gaze_x = min(1.25, max(-0.25, gaze_x))
                    gaze_y = min(1.25, max(-0.25, gaze_y))
                    debug_text = (
                        f"gaze=({gaze_x:.3f}, {gaze_y:.3f})  "
                        f"EAR L/R=({left_ear:.3f}, {right_ear:.3f})  "
                        f"wink={'YES' if wink else 'no'}  "
                        + (f"head(deg)=({math.degrees(head_pose[0]):.1f}, {math.degrees(head_pose[1]):.1f})"
                        if head_pose is not None else "head pose unavailable")
                    )
                    self._publish_debug_frame(
                        cv2, frame, debug_text, left_eye, right_eye, left_iris, right_iris,
                    )
                    self._publish(GazeSample(
                        gaze_x, gaze_y, wink,
                        head_yaw=head_pose[0] if head_pose else None,
                        head_pitch=head_pose[1] if head_pose else None,
                    ))
        except Exception as exc:  # Keep camera failures visible in the app instead of crashing Tk.
            logger.exception("Gaze tracking failed")
            self._publish(GazeSample(message=f"시선 추적 오류: {exc}"))
        finally:
            if camera is not None:
                camera.release()

    def _publish_debug_frame(self, cv2, frame, label: str,
                             left_eye=None, right_eye=None, left_iris=None, right_iris=None) -> None:
        if not self.debug_enabled:
            return
        now = time.monotonic()
        if now - self._last_debug_frame_time < 0.1:
            return
        self._last_debug_frame_time = now
        annotated = frame.copy()
        if left_eye is not None and right_eye is not None:
            for eye, color, name in ((left_eye, (0, 220, 255), "L"),
                                     (right_eye, (255, 180, 0), "R")):
                eye_points = [(round(x), round(y)) for x, y in eye]
                for index, point in enumerate(eye_points):
                    cv2.line(annotated, point, eye_points[(index + 1) % len(eye_points)],
                             color, 2, cv2.LINE_AA)
                cv2.putText(annotated, name, eye_points[0], cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, color, 2, cv2.LINE_AA)
            for iris in (left_iris, right_iris):
                center = (round(sum(p[0] for p in iris) / len(iris)),
                          round(sum(p[1] for p in iris) / len(iris)))
                radius = max(4, round(sum(((p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2) ** 0.5
                                          for p in iris) / len(iris)))
                cv2.circle(annotated, center, radius, (255, 0, 255), 2, cv2.LINE_AA)
                cv2.circle(annotated, center, 3, (0, 255, 0), -1, cv2.LINE_AA)
                cv2.drawMarker(annotated, center, (255, 255, 255), cv2.MARKER_CROSS, 14, 1)
        cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 34), (15, 18, 30), -1)
        cv2.putText(annotated, label, (10, 23), cv2.FONT_HERSHEY_SIMPLEX,
                    0.58, (255, 255, 255), 1, cv2.LINE_AA)
        try:
            height, width = annotated.shape[:2]
            scale = min(1.0, 800 / width, 450 / height)
            if scale < 1.0:
                size = (round(width * scale), round(height * scale))
                annotated = cv2.resize(annotated, size, interpolation=cv2.INTER_AREA)
            ok, encoded_frame = cv2.imencode(".png", annotated)
            if not ok:
                raise RuntimeError("Could not encode gaze debug frame")
            encoded = base64.b64encode(encoded_frame.tobytes()).decode("ascii")
            with self._lock:
                self._debug_image = encoded
        except Exception:
            logger.exception("Could not prepare gaze debug frame")

    def _publish(self, sample: GazeSample) -> None:
        with self._lock:
            self._sample = sample


class GazeCalibration:
    """Map eye-local gaze to screen coordinates with learned head-motion compensation."""

    def __init__(self) -> None:
        self.points: list[tuple[float, float, float, float, float, float]] = []
        self.mapping: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
        self.head_reference = (0.0, 0.0)
        self.head_coefficients_x = (0.0, 0.0)
        self.head_coefficients_y = (0.0, 0.0)
        self.head_compensated = False

    def add(self, gaze_x: float, gaze_y: float, screen_x: float, screen_y: float,
            head_yaw: float | None = None, head_pitch: float | None = None) -> bool:
        self.points.append((gaze_x, gaze_y, head_yaw or 0.0, head_pitch or 0.0,
                            screen_x, screen_y))
        return self._refit()

    @staticmethod
    def _solve(matrix: list[list[float]]) -> tuple[float, float, float] | None:
        matrix = [row[:] for row in matrix]
        for pivot in range(3):
            best = max(range(pivot, 3), key=lambda i: abs(matrix[i][pivot]))
            if abs(matrix[best][pivot]) < 1e-8:
                return None
            matrix[pivot], matrix[best] = matrix[best], matrix[pivot]
            scale = matrix[pivot][pivot]
            matrix[pivot] = [value / scale for value in matrix[pivot]]
            for i in range(3):
                if i == pivot:
                    continue
                factor = matrix[i][pivot]
                matrix[i] = [matrix[i][j] - factor * matrix[pivot][j] for j in range(4)]
        return matrix[0][3], matrix[1][3], matrix[2][3]

    def _correct(self, gaze_x: float, gaze_y: float, yaw: float, pitch: float
                 ) -> tuple[float, float]:
        dyaw, dpitch = yaw - self.head_reference[0], pitch - self.head_reference[1]
        return (gaze_x - self.head_coefficients_x[0] * dyaw - self.head_coefficients_x[1] * dpitch,
                gaze_y - self.head_coefficients_y[0] * dyaw - self.head_coefficients_y[1] * dpitch)

    def _refit(self) -> bool:
        if len(self.points) < 3:
            self.mapping = None
            return False
        corrected = [self._correct(gx, gy, yaw, pitch)
                     for gx, gy, yaw, pitch, _sx, _sy in self.points]
        if (max(point[0] for point in corrected) - min(point[0] for point in corrected) < 0.015
                or max(point[1] for point in corrected) - min(point[1] for point in corrected) < 0.015):
            self.mapping = None
            return False
        fits = []
        for target_index in (4, 5):
            matrix = [[0.0] * 4 for _ in range(3)]
            for row, (gx, gy) in zip(self.points, corrected):
                v = [gx, gy, 1.0]
                target = row[target_index]
                for i in range(3):
                    for j in range(3):
                        matrix[i][j] += v[i] * v[j]
                    matrix[i][3] += v[i] * target
            fit = self._solve(matrix)
            if fit is None:
                self.mapping = None
                return False
            fits.append(fit)
        self.mapping = (fits[0], fits[1])
        return True

    @staticmethod
    def fit_head_compensation(baseline_samples: list[tuple[float, float, float, float]],
                              movement_samples: list[tuple[float, float, float, float]]):
        """Fit eye-coordinate drift against head yaw/pitch while the user fixates center."""
        if len(baseline_samples) < 8 or len(movement_samples) < 20:
            return None
        samples = baseline_samples + movement_samples
        yaw_values = [row[2] for row in movement_samples]
        pitch_values = [row[3] for row in movement_samples]
        yaw_span = max(yaw_values) - min(yaw_values)
        pitch_span = max(pitch_values) - min(pitch_values)
        if max(yaw_span, pitch_span) < math.radians(8):
            return None
        ref_yaw = median(row[2] for row in baseline_samples)
        ref_pitch = median(row[3] for row in baseline_samples)
        predictors = [(row[2] - ref_yaw, row[3] - ref_pitch) for row in samples]
        mean_yaw = sum(row[0] for row in predictors) / len(predictors)
        mean_pitch = sum(row[1] for row in predictors) / len(predictors)
        centered = [(a - mean_yaw, b - mean_pitch) for a, b in predictors]
        var_yaw = sum(a * a for a, _ in centered)
        var_pitch = sum(b * b for _, b in centered)
        cov_pose = sum(a * b for a, b in centered)
        determinant = var_yaw * var_pitch - cov_pose * cov_pose
        two_axis = (var_yaw > 1e-8 and var_pitch > 1e-8
                    and determinant > 0.08 * var_yaw * var_pitch)

        def fit_axis(feature_index: int) -> tuple[float, float]:
            values = [row[feature_index] for row in samples]
            mean_value = sum(values) / len(values)
            cov_yaw = sum(centered[i][0] * (values[i] - mean_value) for i in range(len(samples)))
            cov_pitch = sum(centered[i][1] * (values[i] - mean_value) for i in range(len(samples)))
            if two_axis:
                beta_yaw = (cov_yaw * var_pitch - cov_pitch * cov_pose) / determinant
                beta_pitch = (cov_pitch * var_yaw - cov_yaw * cov_pose) / determinant
            elif var_yaw >= var_pitch:
                beta_yaw, beta_pitch = cov_yaw / max(var_yaw, 1e-8), 0.0
            else:
                beta_yaw, beta_pitch = 0.0, cov_pitch / max(var_pitch, 1e-8)
            return max(-2.5, min(2.5, beta_yaw)), max(-2.5, min(2.5, beta_pitch))

        return (ref_yaw, ref_pitch, fit_axis(0), fit_axis(1))

    def set_head_compensation(self, reference: tuple[float, float],
                              coefficients_x: tuple[float, float],
                              coefficients_y: tuple[float, float]) -> bool:
        self.head_reference = reference
        self.head_coefficients_x = coefficients_x
        self.head_coefficients_y = coefficients_y
        self.head_compensated = True
        return self._refit()

    def project(self, x: float, y: float, head_yaw: float | None = None,
                head_pitch: float | None = None) -> tuple[float, float]:
        if not self.mapping:
            return (min(1.0, max(0.0, (x - 0.2) / 0.6)),
                    min(1.0, max(0.0, (y - 0.2) / 0.6)))
        yaw = self.head_reference[0] if head_yaw is None else head_yaw
        pitch = self.head_reference[1] if head_pitch is None else head_pitch
        x, y = self._correct(x, y, yaw, pitch)
        xfit, yfit = self.mapping
        return (min(1.0, max(0.0, xfit[0] * x + xfit[1] * y + xfit[2])),
                min(1.0, max(0.0, yfit[0] * x + yfit[1] * y + yfit[2])))
