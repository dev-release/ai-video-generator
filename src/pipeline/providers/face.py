"""Mouth openness per frame: OpenCV YuNet (face box) -> Facemark LBF (68 points) -> lip gap /
mouth width. Local, $0, pip-only (opencv-contrib-python-headless) on any OS. Why not "pixel
change in the mouth area": head or camera motion is not an open mouth (spec sync.md).

mediapipe was rejected: 1.0.1 aborts natively on macOS.
YuNet model (~230 KB): https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
LBF model (~54 MB, OpenCV contrib Facemark): https://github.com/kurnianggoro/GSOC2017
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np

from pipeline.config import SYNC_MAX_YAW
from pipeline.media import write_atomic
from pipeline.obs import console
from pipeline.providers import SpecProvider
from pipeline.registry import ModelSpec

MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
CACHE = Path.home() / ".cache" / "pipeline" / "opencv" / "face_detection_yunet_2023mar.onnx"
LBF_URL = "https://raw.githubusercontent.com/kurnianggoro/GSOC2017/master/data/lbfmodel.yaml"
LBF_CACHE = CACHE.with_name("lbfmodel.yaml")


def _download(url: str, path: Path, min_size: int, what: str) -> Path:
    if path.exists() and path.stat().st_size > min_size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"  [dim]opencv: downloading {what} (once) -> {path}[/]")
    r = httpx.get(url, follow_redirects=True, timeout=120)
    r.raise_for_status()
    write_atomic(path, r.content)
    return path


def ensure_model(path: Path = CACHE) -> Path:
    return _download(MODEL_URL, path, 100_000, "YuNet face detector")


def ensure_lbf(path: Path = LBF_CACHE) -> Path:
    return _download(LBF_URL, path, 50_000_000, "LBF face landmarks (~54 MB)")


@lru_cache
def _facemark(model: Path):
    import cv2

    fm = cv2.face.createFacemarkLBF()
    fm.loadModel(str(model))
    return fm


def head_yaw(face: np.ndarray) -> float:
    """Head yaw from YuNet points: nose offset from the eye midpoint / eye distance. Frontal ≈ 0,
    3/4 ≈ 0.25–0.4, profile is more (spec sync.md)."""
    rx, lx, nx = float(face[4]), float(face[6]), float(face[8])
    return (nx - (rx + lx) / 2) / max(abs(lx - rx), 1.0)


def mouth_open(landmarks: np.ndarray) -> float:
    """Mouth openness from 68 points (iBUG): inner lips 62-66 / mouth width 60-64."""
    p = landmarks.reshape(-1, 68, 2)[0]
    return float(np.linalg.norm(p[66] - p[62]) / (np.linalg.norm(p[64] - p[60]) + 1e-6))


def normalized(raw: list[float | None]) -> list[float | None]:
    """0..1 per shot (for the chart and thresholds; correlation does not depend on scale)."""
    vals = [v for v in raw if v is not None]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
    return [None if v is None else round((v - lo) / (hi - lo), 4) if hi > lo else 0.0 for v in raw]


@lru_cache
def _detector(model: Path, width: int, height: int):
    import cv2

    return cv2.FaceDetectorYN.create(str(model), "", (width, height), 0.7)


class YuNetMouth(SpecProvider):
    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec

    def has_face(self, image: Path) -> bool | None:
        import cv2

        bgr = cv2.imread(str(image))
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        _, faces = _detector(ensure_model(), w, h).detect(bgr)
        return faces is not None and len(faces) > 0

    def mouth(self, frames: list[bytes], width: int, height: int) -> list[float | None]:
        """Mouth openness per frame (0..1 per shot); no face -> None."""
        import cv2

        det = _detector(ensure_model(), width, height)
        fm = _facemark(ensure_lbf())
        raw: list[float | None] = []
        for frame in frames:
            rgb = np.frombuffer(frame, dtype=np.uint8).reshape(height, width, 3)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            _, faces = det.detect(bgr)
            if faces is None or len(faces) == 0:
                raw.append(None)
                continue
            face = max(faces, key=lambda f: f[14])
            if abs(head_yaw(face)) > SYNC_MAX_YAW:
                raw.append(None)  # profile: lip points are unreliable, frame not measured
                continue
            x, y, w, h = face[:4]
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            ok, lm = fm.fit(gray, np.array([[x, y, w, h]], dtype=np.int32))
            raw.append(mouth_open(np.asarray(lm)) if ok else None)
        return normalized(raw)
