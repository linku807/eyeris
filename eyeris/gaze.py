from __future__ import annotations

import base64
import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path


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
    features: tuple[float, ...] | None = None


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
    def _eye_gaze_3d(eye, iris) -> tuple[float, float]:
        """Measure iris position in a 3D eye-local frame to reduce head-roll/perspective drift."""
        start, end = eye[0], eye[3]
        horizontal = tuple(end[axis] - start[axis] for axis in range(3))
        width = math.sqrt(sum(value * value for value in horizontal))
        if width < 1e-7:
            return 0.5, 0.5
        horizontal_axis = tuple(value / width for value in horizontal)
        top = tuple((eye[1][axis] + eye[2][axis]) / 2 for axis in range(3))
        bottom = tuple((eye[5][axis] + eye[4][axis]) / 2 for axis in range(3))
        vertical = tuple(bottom[axis] - top[axis] for axis in range(3))
        projection = sum(vertical[axis] * horizontal_axis[axis] for axis in range(3))
        vertical = tuple(vertical[axis] - projection * horizontal_axis[axis] for axis in range(3))
        vertical_length_sq = sum(value * value for value in vertical)
        if vertical_length_sq < 1e-9:
            return 0.5, 0.5
        horizontal_ratio = sum((iris[i] - start[i]) * horizontal_axis[i] for i in range(3)) / width
        vertical_ratio = sum((iris[i] - top[i]) * vertical[i] for i in range(3)) / vertical_length_sq
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
                last_features: tuple[float, ...] | None = None
                while not self._stop.is_set():
                    ok, frame = camera.read()
                    if not ok:
                        wink_candidate_since = None
                        wink_eye = None
                        last_gaze = None
                        last_features = None
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
                        last_features = None
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
                    point3d = lambda index: (landmarks[index].x, landmarks[index].y, landmarks[index].z)
                    left_eye_3d = [point3d(i) for i in self.LEFT_EYE]
                    right_eye_3d = [point3d(i) for i in self.RIGHT_EYE]
                    left_iris_3d = tuple(sum(point3d(i)[axis] for i in self.LEFT_IRIS) / len(self.LEFT_IRIS)
                                         for axis in range(3))
                    right_iris_3d = tuple(sum(point3d(i)[axis] for i in self.RIGHT_IRIS) / len(self.RIGHT_IRIS)
                                          for axis in range(3))
                    left_gaze = self._eye_gaze_3d(left_eye_3d, left_iris_3d)
                    # Both eye groups are ordered from image-left to image-right. Keep
                    # that common axis so the two eyes reinforce instead of canceling.
                    right_gaze = self._eye_gaze_3d(right_eye_3d, right_iris_3d)
                    left_ear, right_ear = self._ear(left_eye), self._ear(right_eye)
                    threshold = self.blink_threshold
                    previous_wink_eye = wink_eye
                    wink_eye, wink_candidate_since, wink = self._update_wink(
                        left_ear, right_ear, threshold, wink_eye, wink_candidate_since, time.monotonic()
                    )
                    gaze_features = None
                    wink_transition = previous_wink_eye is not None and wink_eye is None
                    if wink_eye is not None or wink_transition:
                        # Keep both the gaze and model inputs fixed across closure/release frames.
                        if last_gaze is not None:
                            gaze_x, gaze_y = last_gaze
                            gaze_features = last_features
                        else:
                            wink_gaze = right_gaze if wink_eye == "left" else left_gaze
                            gaze_x, gaze_y = 1.0 - wink_gaze[0], wink_gaze[1]
                            gaze_features = (*wink_gaze, *wink_gaze)
                            last_gaze = (gaze_x, gaze_y)
                            last_features = gaze_features
                    else:
                        left_open = left_ear >= threshold
                        right_open = right_ear >= threshold
                        if left_open and right_open:
                            eye_gaze = ((left_gaze[0] + right_gaze[0]) / 2,
                                        (left_gaze[1] + right_gaze[1]) / 2)
                            gaze_features = (
                                left_gaze[0], left_gaze[1],
                                right_gaze[0], right_gaze[1],
                            )
                        elif left_open:
                            eye_gaze = left_gaze
                            other_eye = last_features[2:4] if last_features else left_gaze
                            gaze_features = (*left_gaze, *other_eye)
                        elif right_open:
                            eye_gaze = right_gaze
                            other_eye = last_features[:2] if last_features else right_gaze
                            gaze_features = (*other_eye, *right_gaze)
                        else:
                            last_gaze = None
                            last_features = None
                            self._publish(GazeSample(message="눈을 뜨고 화면을 바라보세요"))
                            continue
                        gaze_x, gaze_y = 1.0 - eye_gaze[0], eye_gaze[1]
                        last_gaze = (gaze_x, gaze_y)
                        last_features = gaze_features
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
                        features=gaze_features,
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
    """Map separate left/right eye features to screen coordinates."""

    def __init__(self) -> None:
        self.points: list[tuple[float, float, float, float]] = []
        self.mapping: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
        self.feature_samples: list[tuple[tuple[float, ...], float, float]] = []
        self.feature_mean: tuple[float, ...] = ()
        self.feature_scale: tuple[float, ...] = ()
        self.feature_mapping: tuple[tuple[float, ...], tuple[float, ...]] | None = None

    def add(self, gaze_x: float, gaze_y: float, screen_x: float, screen_y: float) -> bool:
        self.points.append((gaze_x, gaze_y, screen_x, screen_y))
        return self._refit()

    def add_feature_samples(self, samples: list[tuple[float, ...]],
                            screen_x: float, screen_y: float) -> bool:
        for features in samples:
            if features and all(math.isfinite(value) for value in features):
                self.feature_samples.append((tuple(features), screen_x, screen_y))
        return self._fit_feature_mapping()

    @staticmethod
    def _solve_system(matrix: list[list[float]], target: list[float]) -> tuple[float, ...] | None:
        size = len(target)
        augmented = [matrix[row][:] + [target[row]] for row in range(size)]
        for pivot in range(size):
            best = max(range(pivot, size), key=lambda row: abs(augmented[row][pivot]))
            if abs(augmented[best][pivot]) < 1e-10:
                return None
            augmented[pivot], augmented[best] = augmented[best], augmented[pivot]
            divisor = augmented[pivot][pivot]
            augmented[pivot] = [value / divisor for value in augmented[pivot]]
            for row in range(size):
                if row == pivot:
                    continue
                factor = augmented[row][pivot]
                augmented[row] = [augmented[row][col] - factor * augmented[pivot][col]
                                  for col in range(size + 1)]
        return tuple(augmented[row][-1] for row in range(size))

    def _fit_feature_mapping(self) -> bool:
        if len(self.feature_samples) < 30:
            return False
        dimensions = len(self.feature_samples[0][0])
        if dimensions == 0 or any(len(features) != dimensions
                                  for features, _x, _y in self.feature_samples):
            self.feature_mapping = None
            return False
        rows = [sample[0] for sample in self.feature_samples]
        means = tuple(sum(row[index] for row in rows) / len(rows) for index in range(dimensions))
        scales = tuple(max(1e-4, math.sqrt(sum((row[index] - means[index]) ** 2 for row in rows)
                                           / len(rows))) for index in range(dimensions))
        design = [[(features[index] - means[index]) / scales[index]
                   for index in range(dimensions)] + [1.0]
                  for features, _x, _y in self.feature_samples]
        size = dimensions + 1
        normal = [[sum(row[i] * row[j] for row in design) for j in range(size)]
                  for i in range(size)]
        # Ridge regularization reduces instability between correlated left/right eye features.
        for index in range(dimensions):
            normal[index][index] += 0.02 * len(design)
        fitted = []
        for target_index in (1, 2):
            target = [sample[target_index] for sample in self.feature_samples]
            rhs = [sum(row[index] * value for row, value in zip(design, target))
                   for index in range(size)]
            coefficients = self._solve_system(normal, rhs)
            if coefficients is None:
                self.feature_mapping = None
                return False
            fitted.append(coefficients)
        self.feature_mean = means
        self.feature_scale = scales
        self.feature_mapping = (fitted[0], fitted[1])
        return True

    def _project_features(self, features: tuple[float, ...]) -> tuple[float, float] | None:
        if self.feature_mapping is None or len(features) != len(self.feature_mean):
            return None
        normalized = tuple((value - mean) / scale
                           for value, mean, scale in zip(features, self.feature_mean, self.feature_scale))
        xfit, yfit = self.feature_mapping
        x = sum(value * coefficient for value, coefficient in zip(normalized, xfit)) + xfit[-1]
        y = sum(value * coefficient for value, coefficient in zip(normalized, yfit)) + yfit[-1]
        return min(1.0, max(0.0, x)), min(1.0, max(0.0, y))

    @staticmethod
    def _solve(matrix: list[list[float]]) -> tuple[float, float, float] | None:
        solved = GazeCalibration._solve_system(
            [row[:3] for row in matrix], [row[3] for row in matrix]
        )
        return solved if solved is None else (solved[0], solved[1], solved[2])

    def _refit(self) -> bool:
        if len(self.points) < 3:
            self.mapping = None
            return False
        if (max(point[0] for point in self.points) - min(point[0] for point in self.points) < 0.015
                or max(point[1] for point in self.points) - min(point[1] for point in self.points) < 0.015):
            self.mapping = None
            return False
        fits = []
        for target_index in (2, 3):
            matrix = [[0.0] * 4 for _ in range(3)]
            for row in self.points:
                v = [row[0], row[1], 1.0]
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

    def project(self, x: float, y: float,
                features: tuple[float, ...] | None = None) -> tuple[float, float]:
        if features is not None:
            mapped = self._project_features(features)
            if mapped is not None:
                return mapped
        if not self.mapping:
            return (min(1.0, max(0.0, (x - 0.2) / 0.6)),
                    min(1.0, max(0.0, (y - 0.2) / 0.6)))
        xfit, yfit = self.mapping
        return (min(1.0, max(0.0, xfit[0] * x + xfit[1] * y + xfit[2])),
                min(1.0, max(0.0, yfit[0] * x + yfit[1] * y + yfit[2])))
