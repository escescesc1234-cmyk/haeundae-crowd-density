# ============================================================
# 실시간 안전지도 스트림
#
# 입력: 카메라/YouTube 스트림 + 명/m² 격자
# 사람 인식: 객체탐지로 사람/비사람 구분 → 고확신 person만 밀도에 반영
# 처리: 격자 초록/노랑/빨강(50%) 오버레이
# 출력: 사람만 반영된 안전지도 MJPEG + YOLO/SAHI 모니터
# ============================================================

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:
    # Windows 등에서 tzdata 미설치 시 UTC+9 고정
    KST = timezone(timedelta(hours=9), name="KST")

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request
from ultralytics import YOLO
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction

from homography_density import DST_METERS, SRC_PIXELS, build_homography
from safety_map import (
    MSG_MANAGER,
    MSG_TOURIST,
    OVERLAY_ALPHA,
    THRESH_CAUTION,
    THRESH_DANGER,
    build_density_grid_per_m2,
    build_warning_messages,
    count_danger_cells,
    draw_legend,
    draw_warning_banners,
    make_roi_mask,
    render_safety_map,
)
from sk_congestion import SK_STORE, fetch_sk_congestion, sk_refresh_loop


ROOT = Path(__file__).resolve().parent
DEFAULT_YOUTUBE = "https://www.youtube.com/watch?v=jmVmZlsQIL8"
# 캘리브레이션 기준 해상도 (기존 스크린샷)
REF_W, REF_H = 1024.0, 204.0
# 라이브 광안리 뷰: 하늘·다리 제외
LIVE_ROI = [(0.0, 0.45), (1.0, 0.45), (1.0, 1.0), (0.0, 1.0)]
# 더 작은 직사각 격자 (너비 40, 높이 15)
CELL_W = 40
CELL_H = 15
# 사람/비사람: 저확신은 스케일 합의, 고확신은 단독 통과 + 형태필터
PERSON_PROPOSAL_CONF = 0.08
PERSON_MIN_CONF = 0.35
PERSON_HIGH_CONF = 0.35
PERSON_MIN_BOX_AREA = 12.0
PERSON_MIN_BOX_H = 6.0
PERSON_MAX_ASPECT_W_OVER_H = 1.8  # 우산·파라솔·배(가로형) 강하게 차단
PERSON_MIN_ASPECT_H_OVER_W = 0.70  # 세로형(사람) 선호
PERSON_MAX_AREA_FRAC = 0.05
PERSON_MAX_WIDTH_FRAC = 0.18
# 정확도 맥스 설정
YOLO_IMGSZ = 1280
YOLO_CONF = PERSON_PROPOSAL_CONF
DETECT_UPSCALE = 2.0
DEFAULT_DETECTOR = "sahi"
# 2026 신형 yolo26s: i7-8550U 실측 기준 yolov8m 대비 ~1.9배 빠르고 정확도 동등~↑
# (없으면 ultralytics가 자동 다운로드). PRECISE 기본 모델.
DEFAULT_MODEL = "yolo26s.pt"
SAHI_SLICE = 256
SAHI_OVERLAP = 0.5
SAHI_UPSCALE = 3.0
SAHI_CONF = PERSON_PROPOSAL_CONF
# CPU에서 1080p×고배율 멀티스케일은 슬라이스 수천 개 → 사실상 멈춤
# PRECISE: 긴변 축소 + 단일 스케일로 "완료"를 우선 (FAST와 스케일 단위 락 공유)
SAHI_MULTI_SCALES = (1.6,)
SAHI_MULTI_SLICES = (384,)
SAHI_NMS_IOU = 0.5
SAHI_IMAGE_SIZE = 640
PRECISE_MAX_EDGE = 960
PRECISE_OVERLAP = 0.25
PRECISE_COOLDOWN_SEC = 45.0  # 1회 보정 후 FAST에 양보
# FAST 경로: 경량 SAHI. yolo26s(신형·경량) 우선, 없으면 ultralytics 자동 다운로드.
# 로컬 파인튜닝(models/*_beach_ft.pt)이 있으면 그것을 우선.
FAST_SAHI_MODEL_CANDIDATES = (
    "models/yolo26s_beach_ft.pt",
    "yolo26s.pt",
    "models/yolov8m_beach_ft.pt",
    "yolov8m.pt",
)
# ultralytics가 자동 다운로드하는 기본 이름 (로컬 파일이 없을 때 사용)
FAST_SAHI_DOWNLOAD_NAME = "yolo26s.pt"
FAST_SAHI_UPSCALE = 2.0
FAST_SAHI_SLICE = 384
FAST_SAHI_OVERLAP = 0.22
FAST_SAHI_IMGSZ = 640
FAST_EVERY_SEC = 0.2
FAST_CONF = PERSON_PROPOSAL_CONF
# --detector yolo 일 때만 사용 (호환)
FAST_MODEL_CANDIDATES = FAST_SAHI_MODEL_CANDIDATES
FAST_IMGSZ = 1280
FAST_UPSCALE = 2.8
FAST_SCALES = (2.2, 3.0)
FAST_MAX_DET = 500
FAST_USE_TTA = False
FAST_MIN_SCALE_VOTES = 2
TEMPORAL_HISTORY = 2
TEMPORAL_MIN_HITS = 1
TEMPORAL_IOU = 0.3


class LatestFrameStore:
    """최신 원본/안전지도 프레임을 스레드 안전하게 보관.

    raw   : 카메라 원본 (YOLO 입력 전용 — 오버레이 없음)
    safety: 탐지 후 색칠한 안전지도 (표시용)
    density는 YOLO가 갱신하고, 화면은 최신 raw 위에 바로 합성해 지연을 줄입니다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.raw: Optional[np.ndarray] = None
        self.safety: Optional[np.ndarray] = None
        self.density: Optional[np.ndarray] = None
        self.alerts: dict = build_warning_messages(0)
        self.person_count = 0
        self.updated_at: Optional[str] = None
        self.status = "starting"
        self.cell_w = CELL_W
        self.cell_h = CELL_H
        # YOLO 모니터링용: [(x1,y1,x2,y2,conf), ...]
        self.yolo_boxes: list = []
        self.rejected_boxes: list = []
        self.yolo_infer_ms: float = 0.0
        self.detector_name: str = DEFAULT_DETECTOR
        self.rejected_count: int = 0
        self.pipeline: str = "starting"  # fast | precise
        self.warn_lock = threading.Lock()
        self._last_warn_state = False

    def set_raw(self, frame: np.ndarray):
        # VideoCapture 버퍼 재사용 대비 — 항상 복사본만 보관
        with self._lock:
            self.raw = frame.copy()

    def set_result(
        self,
        safety: np.ndarray,
        density: np.ndarray,
        alerts: dict,
        person_count: int,
        status: str = "ok",
        yolo_boxes: list | None = None,
        yolo_infer_ms: float = 0.0,
        rejected_boxes: list | None = None,
        pipeline: str = "fast",
    ):
        with self._lock:
            self.safety = safety
            self.density = density
            self.alerts = alerts
            self.person_count = person_count
            self.updated_at = datetime.now(timezone.utc).isoformat()
            self.status = status
            self.pipeline = pipeline
            if yolo_boxes is not None:
                self.yolo_boxes = list(yolo_boxes)
            if rejected_boxes is not None:
                self.rejected_boxes = list(rejected_boxes)
                self.rejected_count = len(self.rejected_boxes)
            self.yolo_infer_ms = float(yolo_infer_ms)

    def set_detector_name(self, name: str):
        with self._lock:
            self.detector_name = name

    def get_raw_copy(self) -> Optional[np.ndarray]:
        """YOLO 분석용 원본 프레임 (초록 오버레이 없음)."""
        with self._lock:
            if self.raw is None:
                return None
            return self.raw.copy()

    def compose_display_frame(self) -> Optional[np.ndarray]:
        """최신 원본 + 직전 밀도 격자를 즉시 합성 (YOLO 대기 없이 화면 갱신)."""
        with self._lock:
            if self.raw is None:
                return None
            raw = self.raw.copy()
            dens = None if self.density is None else self.density.copy()
            alerts = dict(self.alerts)
            cw, ch = self.cell_w, self.cell_h

        if dens is None:
            out = draw_bottom_clock(raw)
            cv2.putText(
                out,
                "Waiting YOLO...",
                (8, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            return out

        # 표시용: 격자선은 끄고 빠르게 합성 (체감 지연 감소)
        out = render_safety_map(
            raw,
            dens,
            alpha=OVERLAY_ALPHA,
            cell_w=cw,
            cell_h=ch,
            draw_grid_lines=False,
        )
        out = draw_legend(out)
        cv2.putText(
            out,
            f"grid {cw}x{ch}px",
            (8, out.shape[0] - max(36, out.shape[0] // 28) - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        if alerts.get("hasDanger"):
            out = draw_warning_banners(out, alerts)
        return draw_bottom_clock(out)

    def get_safety_jpeg(self, quality: int = 80) -> Optional[bytes]:
        frame = self.compose_display_frame()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None

    def compose_yolo_frame(self) -> Optional[np.ndarray]:
        """최신 원본 위에 직전 탐지 박스를 그려 모니터링 화면 생성."""
        with self._lock:
            if self.raw is None:
                return None
            raw = self.raw.copy()
            boxes = list(self.yolo_boxes)
            rejected = list(self.rejected_boxes)
            infer_ms = self.yolo_infer_ms
            detector = self.detector_name
        title = "SAHI person-only" if detector == "sahi" else "YOLO person-only"
        out = draw_yolo_boxes(raw, boxes, LIVE_ROI, title=title, rejected=rejected)
        cv2.putText(
            out,
            f"infer {infer_ms:.0f}ms",
            (8, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        return out

    def get_yolo_jpeg(self, quality: int = 80) -> Optional[bytes]:
        frame = self.compose_yolo_frame()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None

    def get_raw_jpeg(self, quality: int = 80) -> Optional[bytes]:
        with self._lock:
            frame = None if self.raw is None else self.raw.copy()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None

    def snapshot(self) -> dict:
        with self._lock:
            dens = self.density
            max_d = (
                float(np.nanmax(dens))
                if dens is not None and np.any(~np.isnan(dens))
                else 0.0
            )
            return {
                "status": self.status,
                "updatedAt": self.updated_at,
                "personCount": self.person_count,
                "maxGridDensityPerM2": max_d,
                "alerts": self.alerts,
                "yoloInferMs": self.yolo_infer_ms,
                "yoloBoxCount": len(self.yolo_boxes),
                "rejectedCount": self.rejected_count,
                "personMinConf": PERSON_MIN_CONF,
                "detector": self.detector_name,
                "pipeline": self.pipeline,
            }


STORE = LatestFrameStore()
# PRECISE 전용 별도 프레임 저장 (FAST와 독립 스트림용)
STORE_PRECISE = LatestFrameStore()

# CPU에서 FAST·PRECISE 동시 추론 시 PyTorch/Ultralytics가 서로 굶김 → 직렬화
INFER_LOCK = threading.Lock()
PRECISE_WANT = threading.Event()  # PRECISE가 락을 원할 때 FAST가 양보
_PRECISE_META_LOCK = threading.Lock()
PRECISE_META: dict = {
    "state": "idle",  # idle | loading | running | ok | error
    "personCount": 0,
    "rejectedCount": 0,
    "inferMs": 0.0,
    "updatedAt": None,
    "lastError": None,
    "progress": None,
}


def set_precise_meta(**kwargs):
    with _PRECISE_META_LOCK:
        PRECISE_META.update(kwargs)


def get_precise_meta() -> dict:
    with _PRECISE_META_LOCK:
        return dict(PRECISE_META)


# ── 군중 카운팅(밀도추정) 병행 ─────────────────────────────
# lwcc(DM-Count/CSRNet 등)로 원거리 밀집 군중을 추정. YOLO 탐지가 놓치는
# 먼 사람까지 density map으로 세어 보조 카운트를 제공한다. (CPU ~9s/프레임)
CROWD_ENABLED = os.environ.get("VISION_CROWD", "1").strip() not in ("0", "false", "")
CROWD_MODEL = os.environ.get("VISION_CROWD_MODEL", "DM-Count")  # DM-Count|CSRNet|Bay|SFANet
CROWD_WEIGHTS = os.environ.get("VISION_CROWD_WEIGHTS", "SHA")   # SHA|SHB|QNRF
CROWD_INTERVAL_SEC = float(os.environ.get("VISION_CROWD_INTERVAL", "30"))
CROWD_ROI_TOP = 0.45   # 상단(하늘·건물) 제외: LIVE_ROI와 동일 비율
CROWD_MAX_EDGE = int(os.environ.get("VISION_CROWD_MAX_EDGE", "1280"))  # 입력 긴 변 제한(연산 축소)
CROWD_INPUT = None     # 지연 초기화(출력 폴더)
_CROWD_META_LOCK = threading.Lock()
CROWD_META: dict = {
    "enabled": CROWD_ENABLED,
    "model": f"{CROWD_MODEL}/{CROWD_WEIGHTS}",
    "state": "idle",   # idle | loading | running | ok | error | disabled
    "count": 0,
    "inferMs": 0.0,
    "updatedAt": None,
    "lastError": None,
}


def set_crowd_meta(**kwargs):
    with _CROWD_META_LOCK:
        CROWD_META.update(kwargs)


def get_crowd_meta() -> dict:
    with _CROWD_META_LOCK:
        return dict(CROWD_META)


def resolve_device() -> str:
    """YOLO/SAHI용 디바이스. VISION_DEVICE 로 강제 가능 (cpu, cuda:0, 0 등)."""
    forced = os.environ.get("VISION_DEVICE", "").strip()
    if forced:
        return forced
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
    except Exception:
        pass
    return "cpu"


def resolve_device_label() -> str:
    d = resolve_device()
    if d.startswith("cuda"):
        try:
            import torch

            return f"cuda ({torch.cuda.get_device_name(0)})"
        except Exception:
            return d
    return d


def resolve_best_model(explicit: str | None = None) -> str:
    """정밀(PRECISE) 경로용: yolo26s.

    GPU 없는 CPU에서는 FAST+PRECISE+군중카운팅이 INFER_LOCK으로 직렬화되므로
    PRECISE를 yolo26m으로 올리면 FAST 경보가 2분 이상 지연된다(경합).
    정확도 보강(원거리 밀집)은 병행 군중카운팅(DM-Count)이 담당하고,
    PRECISE는 yolo26s로 유지해 FAST 응답성을 지킨다.
    로컬 파인튜닝이 있으면 우선.
    """
    if explicit:
        return explicit
    candidates = [
        ROOT / "models" / "yolo26s_beach_ft.pt",
        ROOT / "yolo26s.pt",
        ROOT / "models" / "yolov8l_beach_ft.pt",
        ROOT / "yolov8l.pt",
        ROOT / "models" / "yolov8m_beach_ft.pt",
        ROOT / "yolov8m.pt",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return DEFAULT_MODEL  # "yolo26s.pt" (ultralytics 자동 다운로드)


class TemporalPersonStabilizer:
    """최근 프레임 박스를 모아 깜빡임·일시 미탐을 줄입니다."""

    def __init__(
        self,
        history: int = TEMPORAL_HISTORY,
        min_hits: int = TEMPORAL_MIN_HITS,
        iou_thresh: float = TEMPORAL_IOU,
    ):
        from collections import deque

        self.buf = deque(maxlen=max(1, history))
        self.min_hits = max(1, min_hits)
        self.iou_thresh = iou_thresh

    def update(self, boxes: list) -> list:
        self.buf.append(list(boxes))
        if len(self.buf) == 1:
            return list(boxes)
        pooled: list = []
        for frame_boxes in self.buf:
            pooled.extend(frame_boxes)
        merged = nms_boxes(pooled, iou_thresh=max(0.4, SAHI_NMS_IOU))
        need = min(self.min_hits, len(self.buf))
        kept = []
        for b in merged:
            hits = 0
            for frame_boxes in self.buf:
                if any(_box_iou(b, x) >= self.iou_thresh for x in frame_boxes):
                    hits += 1
            if hits >= need:
                kept.append(b)
        return kept


def eprint(*args):
    print(*args, file=sys.stderr)


def resolve_stream_url(source: str) -> str:
    """YouTube URL이면 yt-dlp로 실제 스트림 URL을 얻습니다."""
    if "youtube.com" in source or "youtu.be" in source:
        eprint(f"[stream] yt-dlp 해석 중: {source}")
        # 4K(2160) 우선, 없으면 1440→1080→720 순으로 폴백
        attempts = [
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--no-warnings",
                "--extractor-args",
                "youtube:player_client=android",
                "-f",
                (
                    "bv*[height<=2160]+ba/b[height<=2160]/"
                    "bv*[height<=1440]+ba/b[height<=1440]/"
                    "bv*[height<=1080]+ba/b[height<=1080]/"
                    "best[height<=2160]/best[height<=1080]/best"
                ),
                "-g",
                source,
            ],
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--no-warnings",
                "-f",
                (
                    "bestvideo[height<=2160]+bestaudio/"
                    "best[height<=2160]/best[height<=1080]/best"
                ),
                "-g",
                source,
            ],
        ]
        last_err = ""
        for cmd in attempts:
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                lines = [
                    ln.strip()
                    for ln in (proc.stdout or "").splitlines()
                    if ln.strip().startswith("http")
                ]
                if lines:
                    url = lines[0]
                    eprint(f"[stream] 획득: {url[:80]}...")
                    return url
                last_err = (proc.stderr or proc.stdout or "").strip()[-400:]
            except Exception as exc:
                last_err = str(exc)
        raise RuntimeError(
            "yt-dlp가 YouTube 스트림 URL을 가져오지 못했습니다. "
            f"상세: {last_err}"
        )
    return source


def open_capture(stream_url: str) -> cv2.VideoCapture:
    # ffmpeg 저지연 옵션 (지원되는 빌드에서만 효과)
    os.environ.setdefault(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS",
        "fflags;nobuffer|flags;low_delay|framedrop;1",
    )
    cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        raise RuntimeError(f"영상 스트림을 열 수 없습니다: {stream_url[:100]}")
    return cap


def read_latest_frame(cap: cv2.VideoCapture, flush: int = 6):
    """버퍼에 쌓인 옛 프레임을 버리고 가능한 한 최신 프레임만 반환."""
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    for _ in range(flush):
        if not cap.grab():
            break
        ok2, newer = cap.retrieve()
        if ok2 and newer is not None:
            frame = newer
    return frame


def scale_homography_for_frame(w: int, h: int):
    """기준 해상도 캘리브레이션을 현재 프레임 크기에 맞게 스케일."""
    sx = w / REF_W
    sy = h / REF_H
    src = SRC_PIXELS.copy()
    src[:, 0] *= sx
    src[:, 1] *= sy
    return build_homography(src, DST_METERS)[0]


def is_confident_person_box(box, frame_hw=None) -> bool:
    """사람으로 '확실히' 인정할지 판정.

    - 클래스 person 인 후보만 전달된다고 가정
    - 확신도·크기·형태가 기준 미달이면 False (밀도 계산 제외)
    """
    x1, y1, x2, y2, score = box
    if score < PERSON_MIN_CONF:
        return False
    bw = max(0.0, float(x2) - float(x1))
    bh = max(0.0, float(y2) - float(y1))
    area = bw * bh
    if area < PERSON_MIN_BOX_AREA or bh < PERSON_MIN_BOX_H:
        return False
    if bh > 1e-6 and (bw / bh) > PERSON_MAX_ASPECT_W_OVER_H:
        return False
    if bw > 1e-6 and (bh / bw) < PERSON_MIN_ASPECT_H_OVER_W:
        return False
    if frame_hw is not None:
        h, w = frame_hw
        # 프레임 밖·거의 점 수준 노이즈 제거
        if x2 <= 0 or y2 <= 0 or x1 >= w or y1 >= h:
            return False
        frame_area = float(max(1, h * w))
        if area / frame_area > PERSON_MAX_AREA_FRAC:
            return False
        # 박스 폭이 프레임의 과도한 비율이면 구조물/배로 간주
        if bw > PERSON_MAX_WIDTH_FRAC * w:
            return False
    return True


def split_person_candidates(boxes: list, frame_hw=None):
    """확정 사람 / 기각(낮은 확신·비정상 박스)으로 분리."""
    confirmed = []
    rejected = []
    for b in boxes:
        if is_confident_person_box(b, frame_hw=frame_hw):
            confirmed.append(b)
        else:
            rejected.append(b)
    return confirmed, rejected


def boxes_to_centers(boxes: list):
    return [((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) for b in boxes]


def _box_center(b):
    return ((b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5)


def _boxes_match(a, b, iou_thresh: float = 0.25, center_frac: float = 0.6) -> bool:
    """IoU 또는 중심점 근접으로 동일 대상으로 간주 (스케일 간 박스 흔들림 보정)."""
    if _box_iou(a, b) >= iou_thresh:
        return True
    acx, acy = _box_center(a)
    bcx, bcy = _box_center(b)
    aw = max(1.0, a[2] - a[0])
    ah = max(1.0, a[3] - a[1])
    bw = max(1.0, b[2] - b[0])
    bh = max(1.0, b[3] - b[1])
    thr = center_frac * 0.5 * (min(aw, bw) + min(ah, bh)) * 0.5
    return abs(acx - bcx) <= thr and abs(acy - bcy) <= thr


def _merge_boxes_with_votes(
    scale_box_lists: list[list],
    nms_iou: float = SAHI_NMS_IOU,
    vote_iou: float = 0.22,
    min_votes: int = FAST_MIN_SCALE_VOTES,
    high_conf: float = PERSON_HIGH_CONF,
) -> tuple[list, list]:
    """고확신은 단독 통과, 저확신은 2스케일 합의. 반환 (kept, dropped)."""
    pooled: list = []
    for boxes in scale_box_lists:
        pooled.extend(boxes)
    if not pooled:
        return [], []
    merged = nms_boxes(pooled, iou_thresh=nms_iou)
    kept, dropped = [], []
    mid_conf = max(PERSON_MIN_CONF + 0.08, 0.22)
    for b in merged:
        score = float(b[4])
        votes = 0
        for boxes in scale_box_lists:
            if any(_boxes_match(b, x, iou_thresh=vote_iou) for x in boxes):
                votes += 1
        if score >= high_conf:
            kept.append(b)
        elif votes >= min_votes and score >= PERSON_MIN_CONF:
            kept.append(b)
        elif votes >= 1 and score >= mid_conf:
            kept.append(b)
        else:
            dropped.append(b)
    return kept, dropped


def detect_people_fast(
    model: YOLO,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    conf: float = YOLO_CONF,
    imgsz: int = YOLO_IMGSZ,
    upscale: float = DETECT_UPSCALE,
    use_tta: bool = False,
    max_det: int = FAST_MAX_DET,
    confirm: bool = True,
    device: str | None = None,
):
    """YOLO 단독: COCO person(class=0)만 제안.

    confirm=False 이면 형태/최소확정 필터 전 후보를 반환 (스케일 투표용).
    """
    if device is None:
        device = resolve_device()
    h, w = roi_mask.shape[:2]
    clean = np.ascontiguousarray(frame_bgr.copy())
    scale = float(upscale) if upscale and upscale > 1.0 else 1.0
    if scale != 1.0:
        infer = cv2.resize(
            clean,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
    else:
        infer = clean

    results = model.predict(
        source=infer,
        classes=[0],  # person only — 우산/의자 등 비사람 제외
        conf=conf,
        verbose=False,
        imgsz=imgsz,
        augment=bool(use_tta),
        max_det=int(max_det),
        iou=0.5,
        device=device,
    )
    boxes = []
    r0 = results[0]
    if r0.boxes is None:
        return [], boxes
    for box in r0.boxes:
        # 이중 확인: class id == 0
        cls_id = int(box.cls[0]) if box.cls is not None else -1
        if cls_id != 0:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        if scale != 1.0:
            x1, y1, x2, y2 = x1 / scale, y1 / scale, x2 / scale, y2 / scale
        score = float(box.conf[0]) if box.conf is not None else 0.0
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        ix, iy = int(round(cx)), int(round(cy))
        if 0 <= ix < w and 0 <= iy < h and roi_mask[iy, ix] > 0:
            boxes.append((float(x1), float(y1), float(x2), float(y2), score))
    if not confirm:
        return boxes_to_centers(boxes), boxes
    confirmed, _ = split_person_candidates(boxes, frame_hw=(h, w))
    return boxes_to_centers(confirmed), confirmed


def detect_people_fast_max(
    model: YOLO,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    conf: float = FAST_CONF,
    imgsz: int = FAST_IMGSZ,
    scales=FAST_SCALES,
    use_tta: bool = FAST_USE_TTA,
    max_det: int = FAST_MAX_DET,
    nms_iou: float = SAHI_NMS_IOU,
    device: str | None = None,
):
    """FAST: 멀티스케일 → 스케일합의/고확신 → 형태필터 (오탐↓)."""
    if device is None:
        device = resolve_device()
    h, w = frame_bgr.shape[:2]
    scale_lists: list[list] = []
    scale_list = list(scales) if scales else (FAST_UPSCALE,)
    for i, scale in enumerate(scale_list):
        tta = bool(use_tta) and i == len(scale_list) - 1
        _c, boxes = detect_people_fast(
            model,
            frame_bgr,
            roi_mask,
            conf=conf,
            imgsz=imgsz,
            upscale=float(scale),
            use_tta=tta,
            max_det=max_det,
            confirm=False,
            device=device,
        )
        scale_lists.append(boxes)
    voted, dropped = _merge_boxes_with_votes(
        scale_lists,
        nms_iou=nms_iou,
        min_votes=FAST_MIN_SCALE_VOTES if len(scale_lists) >= 2 else 1,
        high_conf=PERSON_HIGH_CONF,
    )
    confirmed, rejected = split_person_candidates(voted, frame_hw=(h, w))
    rejected = list(rejected) + list(dropped)
    return boxes_to_centers(confirmed), confirmed, rejected


def _box_iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def nms_boxes(boxes: list, iou_thresh: float = SAHI_NMS_IOU) -> list:
    """score 내림차순 NMS. box=(x1,y1,x2,y2,score)."""
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: b[4], reverse=True)
    keep = []
    while ordered:
        best = ordered.pop(0)
        keep.append(best)
        ordered = [b for b in ordered if _box_iou(best, b) < iou_thresh]
    return keep


def detect_people_sahi(
    detection_model: AutoDetectionModel,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    upscale: float = SAHI_UPSCALE,
    slice_size: int = SAHI_SLICE,
    overlap: float = SAHI_OVERLAP,
):
    """SAHI 슬라이스 탐지 (원본만 입력, 초록 오버레이 금지)."""
    h, w = roi_mask.shape[:2]
    clean = np.ascontiguousarray(frame_bgr.copy())
    scale = float(upscale) if upscale and upscale > 1.0 else 1.0
    if scale != 1.0:
        infer = cv2.resize(
            clean,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
    else:
        infer = clean

    result = get_sliced_prediction(
        infer,
        detection_model,
        slice_height=slice_size,
        slice_width=slice_size,
        overlap_height_ratio=overlap,
        overlap_width_ratio=overlap,
        postprocess_type="NMS",
        postprocess_match_threshold=SAHI_NMS_IOU,
        postprocess_class_agnostic=False,
        verbose=0,
    )

    # person 클래스만 후보 (우산·의자·배 등 비사람 제외)
    boxes = []
    for p in result.object_prediction_list:
        name = str(p.category.name).lower()
        cat_id = getattr(p.category, "id", None)
        if name != "person" and cat_id not in (0, "0"):
            continue
        x1 = float(p.bbox.minx) / scale
        y1 = float(p.bbox.miny) / scale
        x2 = float(p.bbox.maxx) / scale
        y2 = float(p.bbox.maxy) / scale
        score = float(p.score.value)
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        ix, iy = int(round(cx)), int(round(cy))
        if 0 <= ix < w and 0 <= iy < h and roi_mask[iy, ix] > 0:
            boxes.append((x1, y1, x2, y2, score))
    return boxes


def fit_long_edge(frame_bgr: np.ndarray, max_edge: int) -> tuple[np.ndarray, float]:
    """긴변을 max_edge 이하로 축소. 반환: (resized_or_same, scale_to_original_divisor).

    resized 좌표 * (1/scale) = 원본 좌표. scale==1이면 원본.
    """
    h, w = frame_bgr.shape[:2]
    long = max(h, w)
    if max_edge <= 0 or long <= max_edge:
        return frame_bgr, 1.0
    r = float(max_edge) / float(long)
    nw = max(1, int(round(w * r)))
    nh = max(1, int(round(h * r)))
    out = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    return out, r


def detect_people_sahi_max(
    detection_model: AutoDetectionModel,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    overlap: float = SAHI_OVERLAP,
    scales=SAHI_MULTI_SCALES,
    slices=SAHI_MULTI_SLICES,
    nms_iou: float = SAHI_NMS_IOU,
    max_edge: int = PRECISE_MAX_EDGE,
    on_scale=None,
):
    """멀티스케일 SAHI → NMS → 고확신 person만 확정.

    CPU에서 원본 고해상도×고배율은 사실상 멈추므로, 긴변을 max_edge로 줄인 뒤
    탐지하고 박스를 원본 좌표로 복원합니다.
    on_scale(i, n, scale, phase) : phase='before'|'after', 스케일 사이 FAST 양보용.
    반환: (centers, confirmed_boxes, rejected_boxes)
    """
    h0, w0 = frame_bgr.shape[:2]
    infer, r = fit_long_edge(frame_bgr, max_edge)
    if r != 1.0:
        roi_infer = cv2.resize(
            roi_mask,
            (infer.shape[1], infer.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
        eprint(
            f"[precise] input resize {w0}x{h0} → {infer.shape[1]}x{infer.shape[0]} "
            f"(max_edge={max_edge})"
        )
    else:
        roi_infer = roi_mask

    scale_list = list(scales) if scales else (2.0,)
    slice_list = list(slices) if slices else (SAHI_SLICE,)
    n = min(len(scale_list), len(slice_list))
    merged_boxes: list = []
    for i in range(n):
        scale = float(scale_list[i])
        slice_size = int(slice_list[i])
        if on_scale is not None:
            on_scale(i, n, scale, "before")
        t0 = time.perf_counter()
        boxes = detect_people_sahi(
            detection_model,
            infer,
            roi_infer,
            upscale=scale,
            slice_size=slice_size,
            overlap=overlap,
        )
        ms = (time.perf_counter() - t0) * 1000.0
        if r != 1.0:
            inv = 1.0 / r
            boxes = [
                (x1 * inv, y1 * inv, x2 * inv, y2 * inv, s)
                for x1, y1, x2, y2, s in boxes
            ]
        merged_boxes.extend(boxes)
        eprint(
            f"[precise] scale[{i + 1}/{n}] x{scale} slice={slice_size} "
            f"boxes={len(boxes)} ms={ms:.0f}"
        )
        if on_scale is not None:
            on_scale(i, n, scale, "after")

    kept = nms_boxes(merged_boxes, iou_thresh=nms_iou)
    confirmed, rejected = split_person_candidates(kept, frame_hw=(h0, w0))
    return boxes_to_centers(confirmed), confirmed, rejected


def detect_people_sahi_fast(
    detection_model: AutoDetectionModel,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    upscale: float = FAST_SAHI_UPSCALE,
    slice_size: int = FAST_SAHI_SLICE,
    overlap: float = FAST_SAHI_OVERLAP,
):
    """FAST 전용 경량 SAHI (단일 스케일) → 형태/확신 필터."""
    h, w = frame_bgr.shape[:2]
    boxes = detect_people_sahi(
        detection_model,
        frame_bgr,
        roi_mask,
        upscale=upscale,
        slice_size=slice_size,
        overlap=overlap,
    )
    confirmed, rejected = split_person_candidates(boxes, frame_hw=(h, w))
    return boxes_to_centers(confirmed), confirmed, rejected


def _sahi_autodm_device() -> str:
    d = resolve_device()
    return d if d.startswith("cuda") else "cpu"


def resolve_fast_sahi_model(precise_model: str | None = None) -> str:
    """FAST SAHI: yolo26s 우선. 로컬 파일이 없으면 ultralytics 자동 다운로드 이름 반환."""
    for rel in FAST_SAHI_MODEL_CANDIDATES:
        p = ROOT / rel
        if p.exists():
            return str(p)
    # 로컬 파일이 없으면 다운로드 이름을 그대로 반환 (ultralytics가 받음)
    return FAST_SAHI_DOWNLOAD_NAME


def draw_yolo_boxes(
    frame_bgr: np.ndarray,
    boxes: list,
    roi_norm=None,
    title: str = "YOLO live",
    rejected: list | None = None,
) -> np.ndarray:
    """확정 사람(밝은 색) / 기각 후보(회색). 초록 격자 없음."""
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    if roi_norm:
        pts = np.array(
            [[int(x * w), int(y * h)] for x, y in roi_norm],
            dtype=np.int32,
        )
        cv2.polylines(out, [pts], True, (0, 255, 255), 1, cv2.LINE_AA)

    for x1, y1, x2, y2, conf in rejected or []:
        p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
        cv2.rectangle(out, p1, p2, (140, 140, 140), 2)
        cv2.putText(
            out,
            f"X {conf:.2f}",
            (p1[0], max(14, p1[1] - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (180, 180, 180),
            1,
            cv2.LINE_AA,
        )

    for x1, y1, x2, y2, conf in boxes:
        p1 = (int(x1), int(y1))
        p2 = (int(x2), int(y2))
        cv2.rectangle(out, p1, p2, (0, 220, 255), 2)
        cv2.putText(
            out,
            f"person {conf:.2f}",
            (p1[0], max(16, p1[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 220, 255),
            1,
            cv2.LINE_AA,
        )

    rej_n = len(rejected or [])
    cv2.putText(
        out,
        f"{title}  person={len(boxes)}  rejected={rej_n}  minConf>={PERSON_MIN_CONF:.2f}",
        (8, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 255, 180),
        2,
        cv2.LINE_AA,
    )
    return draw_bottom_clock(out)


def capture_loop(source: str, reconnect_sec: float = 5.0):
    """카메라/유튜브에서 프레임을 계속 읽어 STORE.raw 갱신."""
    while True:
        try:
            url = resolve_stream_url(source)
            cap = open_capture(url)
            STORE.status = "capturing"
            eprint("[capture] 시작 (저지연: 버퍼 비우기)")
            fail = 0
            while True:
                frame = read_latest_frame(cap, flush=6)
                if frame is None:
                    fail += 1
                    if fail > 30:
                        raise RuntimeError("프레임 수신 실패 (재연결)")
                    time.sleep(0.01)
                    continue
                fail = 0
                STORE.set_raw(frame)
        except Exception as exc:
            STORE.status = f"capture_error: {exc}"
            eprint(f"[capture] 오류: {exc} → {reconnect_sec}s 후 재시도")
            time.sleep(reconnect_sec)


def format_kst_now() -> str:
    """예: 2026-07-29 12:03 KST"""
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def draw_bottom_clock(frame_bgr: np.ndarray, text: str | None = None) -> np.ndarray:
    """화면 하단에 현재 시각(KST)을 표시합니다."""
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    label = text or format_kst_now()
    bar_h = max(28, h // 28)
    cv2.rectangle(out, (0, h - bar_h), (w, h), (0, 0, 0), -1)
    font_scale = max(0.45, min(0.8, w / 1200))
    thickness = 1 if w < 900 else 2
    (tw, th), _ = cv2.getTextSize(
        label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
    )
    x = max(8, (w - tw) // 2)
    y = h - max(8, (bar_h - th) // 2)
    cv2.putText(
        out,
        label,
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    return out


def publish_detection_result(
    raw: np.ndarray,
    centers: list,
    boxes: list,
    rejected: list,
    roi_mask: np.ndarray,
    H,
    cell_w: int,
    cell_h: int,
    infer_ms: float,
    pipeline: str,
):
    """밀도·안전지도·경고를 만들고 STORE에 반영."""
    density, _, _ = build_density_grid_per_m2(
        raw.shape,
        centers,
        roi_mask,
        H,
        cell_w=cell_w,
        cell_h=cell_h,
    )
    safety = render_safety_map(
        raw,
        density,
        alpha=OVERLAY_ALPHA,
        cell_w=cell_w,
        cell_h=cell_h,
    )
    safety = draw_legend(safety)
    tag = f"grid {cell_w}x{cell_h}px | {pipeline}"
    cv2.putText(
        safety,
        tag,
        (8, safety.shape[0] - max(36, safety.shape[0] // 28) - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    danger = count_danger_cells(density)
    alerts = build_warning_messages(danger)

    if alerts["hasDanger"]:
        safety = draw_warning_banners(safety, alerts)
        with STORE.warn_lock:
            if not STORE._last_warn_state:
                eprint(f"[경고][관광객] {MSG_TOURIST}")
                eprint(f"[경고][관리자] {MSG_MANAGER}")
            STORE._last_warn_state = True
    else:
        with STORE.warn_lock:
            STORE._last_warn_state = False

    safety = draw_bottom_clock(safety)
    STORE.set_result(
        safety,
        density,
        alerts,
        len(centers),
        status="ok",
        yolo_boxes=boxes,
        yolo_infer_ms=infer_ms,
        rejected_boxes=rejected,
        pipeline=pipeline,
    )
    return alerts


def _publish_precise_frame(
    raw: np.ndarray,
    centers: list,
    boxes: list,
    rejected: list,
    roi_mask: np.ndarray,
    H,
    cell_w: int,
    cell_h: int,
    infer_ms: float,
):
    """PRECISE 전용 STORE_PRECISE에 결과 반영 (별도 스트림용)."""
    density, _, _ = build_density_grid_per_m2(
        raw.shape, centers, roi_mask, H, cell_w=cell_w, cell_h=cell_h,
    )
    safety = render_safety_map(
        raw, density, alpha=OVERLAY_ALPHA, cell_w=cell_w, cell_h=cell_h,
    )
    safety = draw_legend(safety)
    tag = f"PRECISE grid {cell_w}x{cell_h}px"
    cv2.putText(
        safety, tag,
        (8, safety.shape[0] - max(36, safety.shape[0] // 28) - 8),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 255), 2, cv2.LINE_AA,
    )
    danger = count_danger_cells(density)
    alerts = build_warning_messages(danger)
    if alerts["hasDanger"]:
        safety = draw_warning_banners(safety, alerts)
    safety = draw_bottom_clock(safety)
    STORE_PRECISE.set_result(
        safety, density, alerts, len(centers),
        status="ok", yolo_boxes=boxes, yolo_infer_ms=infer_ms,
        rejected_boxes=rejected, pipeline="precise",
    )


def resolve_fast_model(precise_model: str | None = None) -> str:
    """FAST도 정확도 우선: yolov8l → beach_ft m → m."""
    for rel in FAST_MODEL_CANDIDATES:
        p = ROOT / rel
        if p.exists():
            return str(p)
    if precise_model and Path(precise_model).exists():
        return precise_model
    return "yolov8l.pt"


def fast_analyze_loop(
    model_path: str,
    analyze_every_sec: float = FAST_EVERY_SEC,
    cell_w: int = CELL_W,
    cell_h: int = CELL_H,
    conf: float = FAST_CONF,
    imgsz: int = FAST_IMGSZ,
    upscale: float = FAST_UPSCALE,
    fast_backend: str = "sahi",
):
    """빠른 경로: 위험 조기 경보.

    fast_backend=sahi: 경량 SAHI (yolov8m, 슬라이스 384, 1스케일)
    fast_backend=yolo: YOLO 멀티스케일 (호환)
    """
    device = resolve_device()
    stabilizer = TemporalPersonStabilizer(
        history=TEMPORAL_HISTORY,
        min_hits=TEMPORAL_MIN_HITS,
        iou_thresh=TEMPORAL_IOU,
    )

    if fast_backend == "yolo":
        fast_model = resolve_fast_model(model_path)
        eprint(
            f"[fast] YOLO 모델={fast_model} device={device} "
            f"imgsz={imgsz} scales={FAST_SCALES} conf={conf}"
        )
        STORE.set_detector_name("yolo-multiscale")
        with INFER_LOCK:
            model = YOLO(fast_model)
        while True:
            t0 = time.perf_counter()
            if PRECISE_WANT.is_set():
                time.sleep(0.15)
                continue
            raw = STORE.get_raw_copy()
            if raw is None:
                time.sleep(0.05)
                continue
            STORE.status = "ok"
            h, w = raw.shape[:2]
            roi_mask, _ = make_roi_mask(h, w, LIVE_ROI)
            H = scale_homography_for_frame(w, h)
            with INFER_LOCK:
                if PRECISE_WANT.is_set():
                    continue
                t_inf = time.perf_counter()
                _c, boxes, rejected = detect_people_fast_max(
                    model,
                    raw,
                    roi_mask,
                    conf=conf,
                    imgsz=imgsz,
                    scales=FAST_SCALES,
                    use_tta=FAST_USE_TTA,
                    device=device,
                )
                boxes = stabilizer.update(boxes)
                centers = boxes_to_centers(boxes)
                infer_ms = (time.perf_counter() - t_inf) * 1000.0
            publish_detection_result(
                raw,
                centers,
                boxes,
                rejected,
                roi_mask,
                H,
                cell_w,
                cell_h,
                infer_ms,
                pipeline="fast",
            )
            sleep = max(0.05, analyze_every_sec - (time.perf_counter() - t0))
            time.sleep(sleep)
        return

    fast_path = resolve_fast_sahi_model(model_path)
    sahi_dev = _sahi_autodm_device()
    eprint(
        f"[fast] 경량 SAHI 모델={fast_path} device={sahi_dev} "
        f"upscale={FAST_SAHI_UPSCALE} slice={FAST_SAHI_SLICE} "
        f"overlap={FAST_SAHI_OVERLAP} imgsz={FAST_SAHI_IMGSZ} conf={conf}"
    )
    STORE.set_detector_name("sahi-fast-lite+sahi-precise")
    with INFER_LOCK:
        sahi_model = AutoDetectionModel.from_pretrained(
            model_type="yolov8",
            model_path=fast_path,
            confidence_threshold=conf if conf > 0 else SAHI_CONF,
            device=sahi_dev,
            image_size=FAST_SAHI_IMGSZ,
        )

    while True:
        t0 = time.perf_counter()
        if PRECISE_WANT.is_set():
            # PRECISE가 락을 기다리는 중이면 FAST는 새 추론을 시작하지 않음
            time.sleep(0.15)
            continue
        raw = STORE.get_raw_copy()
        if raw is None:
            time.sleep(0.05)
            continue
        STORE.status = "ok"
        h, w = raw.shape[:2]
        roi_mask, _ = make_roi_mask(h, w, LIVE_ROI)
        H = scale_homography_for_frame(w, h)
        with INFER_LOCK:
            if PRECISE_WANT.is_set():
                continue
            t_inf = time.perf_counter()
            _c, boxes, rejected = detect_people_sahi_fast(sahi_model, raw, roi_mask)
            boxes = stabilizer.update(boxes)
            centers = boxes_to_centers(boxes)
            infer_ms = (time.perf_counter() - t_inf) * 1000.0
        publish_detection_result(
            raw,
            centers,
            boxes,
            rejected,
            roi_mask,
            H,
            cell_w,
            cell_h,
            infer_ms,
            pipeline="fast",
        )
        sleep = max(0.05, analyze_every_sec - (time.perf_counter() - t0))
        time.sleep(sleep)


def precise_analyze_loop(
    model_path: str,
    cell_w: int = CELL_W,
    cell_h: int = CELL_H,
    conf: float = YOLO_CONF,
    overlap: float = PRECISE_OVERLAP,
):
    """정밀 경로: SAHI 멀티스케일 (백그라운드 보정).

    전체 멀티스케일을 한 락으로 묶으면 CPU에서 수십 분 동안 FAST가 멈춘다.
    스케일마다 락을 잡고 풀어서 FAST와 번갈아 돈다.
    """
    device = resolve_device()
    precise_path = model_path
    eprint(f"[precise] SAHI 모델={precise_path} device={device}")
    eprint(
        f"[precise] multi-scale={SAHI_MULTI_SCALES} "
        f"slices={SAHI_MULTI_SLICES} overlap={overlap} "
        f"imgsz={SAHI_IMAGE_SIZE} max_edge={PRECISE_MAX_EDGE}"
    )
    set_precise_meta(state="loading", lastError=None, progress="model")
    try:
        with INFER_LOCK:
            sahi_model = AutoDetectionModel.from_pretrained(
                model_type="yolov8",
                model_path=precise_path,
                confidence_threshold=conf if conf > 0 else SAHI_CONF,
                device=_sahi_autodm_device(),
                image_size=SAHI_IMAGE_SIZE,
            )
    except Exception as exc:
        eprint(f"[precise] model load FAILED: {exc}")
        set_precise_meta(state="error", lastError=str(exc), progress=None)
        return

    stabilizer = TemporalPersonStabilizer(
        history=TEMPORAL_HISTORY,
        min_hits=TEMPORAL_MIN_HITS,
        iou_thresh=TEMPORAL_IOU,
    )
    set_precise_meta(state="idle", progress=None)
    eprint("[precise] ready — waiting for frame + infer lock")

    # 스케일 단위로 락을 잡기 위한 콜백 상태
    _scale_lock_held = {"on": False}

    def _on_scale(i: int, n: int, scale: float, phase: str):
        if phase == "before":
            set_precise_meta(
                state="running",
                progress=f"{i + 1}/{n} x{scale}",
            )
            PRECISE_WANT.set()
            eprint(
                f"[precise] scale[{i + 1}/{n}] x{scale} "
                f"waiting for infer lock..."
            )
            INFER_LOCK.acquire()
            _scale_lock_held["on"] = True
            PRECISE_WANT.clear()
            eprint(f"[precise] scale[{i + 1}/{n}] x{scale} start")
        elif phase == "after":
            if _scale_lock_held["on"]:
                INFER_LOCK.release()
                _scale_lock_held["on"] = False
            # FAST가 한 사이클 돌 여유
            time.sleep(0.2)

    while True:
        try:
            raw = STORE.get_raw_copy()
            if raw is None:
                time.sleep(0.5)
                continue

            h, w = raw.shape[:2]
            roi_mask, _ = make_roi_mask(h, w, LIVE_ROI)
            H = scale_homography_for_frame(w, h)

            set_precise_meta(state="running", progress="start", lastError=None)
            eprint("[precise] start multi-scale SAHI (per-scale lock)...")
            t_inf = time.perf_counter()
            try:
                _c, boxes, rejected = detect_people_sahi_max(
                    sahi_model,
                    raw,
                    roi_mask,
                    overlap=overlap,
                    max_edge=PRECISE_MAX_EDGE,
                    on_scale=_on_scale,
                )
            finally:
                # 예외 시에도 락 누수 방지
                if _scale_lock_held["on"]:
                    INFER_LOCK.release()
                    _scale_lock_held["on"] = False
                PRECISE_WANT.clear()

            boxes = stabilizer.update(boxes)
            centers = boxes_to_centers(boxes)
            infer_ms = (time.perf_counter() - t_inf) * 1000.0

            eprint(
                f"[precise] done people={len(centers)} rejected={len(rejected)} "
                f"ms={infer_ms:.0f}"
            )
            set_precise_meta(
                state="ok",
                personCount=len(centers),
                rejectedCount=len(rejected),
                inferMs=infer_ms,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                lastError=None,
                progress=None,
            )
            publish_detection_result(
                raw,
                centers,
                boxes,
                rejected,
                roi_mask,
                H,
                cell_w,
                cell_h,
                infer_ms,
                pipeline="precise",
            )
            _publish_precise_frame(
                raw, centers, boxes, rejected, roi_mask, H, cell_w, cell_h, infer_ms
            )
            time.sleep(PRECISE_COOLDOWN_SEC)
        except Exception as exc:
            eprint(f"[precise] FAILED: {exc}")
            if _scale_lock_held["on"]:
                try:
                    INFER_LOCK.release()
                except RuntimeError:
                    pass
                _scale_lock_held["on"] = False
            PRECISE_WANT.clear()
            set_precise_meta(state="error", lastError=str(exc), progress=None)
            time.sleep(5.0)


def crowd_count_loop(interval_sec: float = CROWD_INTERVAL_SEC):
    """군중 카운팅(밀도추정) 병행 루프.

    lwcc(DM-Count/CSRNet)로 ROI(하단) 영역의 사람 수를 density map으로 추정한다.
    YOLO 탐지가 놓치는 원거리 밀집 군중까지 세어 보조 카운트를 제공한다.
    torch CPU 경합을 막기 위해 INFER_LOCK으로 직렬화(간헐 실행).
    """
    if not CROWD_ENABLED:
        set_crowd_meta(state="disabled")
        eprint("[crowd] 비활성화(VISION_CROWD=0)")
        return

    global CROWD_INPUT
    CROWD_INPUT = ROOT / "output" / "crowd_input.jpg"
    CROWD_INPUT.parent.mkdir(parents=True, exist_ok=True)

    set_crowd_meta(state="loading")
    eprint(f"[crowd] 모델 로딩 {CROWD_MODEL}/{CROWD_WEIGHTS} ...")
    try:
        from lwcc import LWCC

        with INFER_LOCK:
            model = LWCC.load_model(
                model_name=CROWD_MODEL, model_weights=CROWD_WEIGHTS
            )
    except Exception as exc:
        eprint(f"[crowd] 로딩 실패(비활성화): {exc}")
        set_crowd_meta(state="error", lastError=str(exc))
        return

    set_crowd_meta(state="idle")
    eprint("[crowd] ready")

    while True:
        try:
            raw = STORE.get_raw_copy()
            if raw is None:
                time.sleep(1.0)
                continue
            h, w = raw.shape[:2]
            top = int(max(0.0, min(0.9, CROWD_ROI_TOP)) * h)
            roi_frame = raw[top:h, 0:w]
            # 연산량·락 점유 축소: 긴 변을 CROWD_MAX_EDGE로 제한 (밀도추정은 스케일 견고)
            rh, rw = roi_frame.shape[:2]
            long_edge = max(rh, rw)
            if CROWD_MAX_EDGE and long_edge > CROWD_MAX_EDGE:
                s = CROWD_MAX_EDGE / float(long_edge)
                roi_frame = cv2.resize(
                    roi_frame, (int(rw * s), int(rh * s)), interpolation=cv2.INTER_AREA
                )
            cv2.imwrite(str(CROWD_INPUT), roi_frame)

            set_crowd_meta(state="running")
            PRECISE_WANT.set()  # FAST가 잠깐 양보하도록
            wait0 = time.perf_counter()
            with INFER_LOCK:
                PRECISE_WANT.clear()
                from lwcc import LWCC

                t0 = time.perf_counter()
                count = float(LWCC.get_count(str(CROWD_INPUT), model=model))
                infer_ms = (time.perf_counter() - t0) * 1000.0
            wait_ms = (time.perf_counter() - wait0) * 1000.0 - infer_ms

            set_crowd_meta(
                state="ok",
                count=round(count, 1),
                inferMs=round(infer_ms, 0),
                updatedAt=datetime.now(timezone.utc).isoformat(),
                lastError=None,
            )
            eprint(
                f"[crowd] {CROWD_MODEL}/{CROWD_WEIGHTS} count={count:.1f} "
                f"infer={infer_ms:.0f}ms lockwait={wait_ms:.0f}ms"
            )
            time.sleep(max(1.0, interval_sec))
        except Exception as exc:
            eprint(f"[crowd] FAILED: {exc}")
            PRECISE_WANT.clear()
            set_crowd_meta(state="error", lastError=str(exc))
            time.sleep(10.0)


def analyze_loop(
    model_path: str,
    analyze_every_sec: float = 0.5,
    cell_w: int = CELL_W,
    cell_h: int = CELL_H,
    conf: float = YOLO_CONF,
    imgsz: int = YOLO_IMGSZ,
    upscale: float = DETECT_UPSCALE,
    detector: str = DEFAULT_DETECTOR,
    slice_size: int = SAHI_SLICE,
    overlap: float = SAHI_OVERLAP,
):
    """호환용: detector=sahi 면 이중 경로, yolo 면 빠른 경로만."""
    if detector == "sahi":
        # 이중 경로는 main 에서 스레드 2개로 띄움
        fast_analyze_loop(
            model_path,
            analyze_every_sec=min(0.5, analyze_every_sec),
            cell_w=cell_w,
            cell_h=cell_h,
            conf=conf,
            fast_backend="sahi",
        )
    else:
        fast_analyze_loop(
            model_path,
            analyze_every_sec=analyze_every_sec,
            cell_w=cell_w,
            cell_h=cell_h,
            conf=conf,
            imgsz=imgsz,
            upscale=upscale,
            fast_backend="yolo",
        )


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        snap = STORE.snapshot()
        return jsonify({"ok": True, "service": "realtime-safety-map", **snap})

    @app.get("/api/status")
    def status():
        snap = STORE.snapshot()
        snap["nowKst"] = format_kst_now()
        snap["grid"] = {"cellW": CELL_W, "cellH": CELL_H}
        snap["telecom"] = SK_STORE.get()
        # PRECISE는 별도 메타 (메인 pipeline이 fast로 덮여도 보정 상태 유지)
        pm = get_precise_meta()
        ps = STORE_PRECISE.snapshot()
        snap["precise"] = {
            **pm,
            "maxGridDensityPerM2": ps.get("maxGridDensityPerM2", 0.0),
            "pipeline": ps.get("pipeline"),
            "status": ps.get("status"),
        }
        snap["crowd"] = get_crowd_meta()
        return jsonify(snap)

    @app.get("/api/telecom")
    def telecom():
        """SK 지오비전 퍼즐 장소 혼잡도 (보조). ?force=1 로 캐시 무시."""
        force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
        return jsonify(fetch_sk_congestion(force=force))

    @app.get("/stream")
    def stream():
        """MJPEG 실시간 안전지도 스트림 (표시용, 오버레이 포함)."""

        def gen():
            while True:
                jpg = STORE.get_safety_jpeg()
                if jpg is None:
                    blank = np.zeros((360, 640, 3), dtype=np.uint8)
                    blank = draw_bottom_clock(blank)
                    cv2.putText(
                        blank,
                        "Connecting...",
                        (200, 170),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 255),
                        2,
                    )
                    ok, buf = cv2.imencode(".jpg", blank)
                    jpg = buf.tobytes() if ok else b""
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                )
                time.sleep(0.05)

        return Response(
            gen(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/stream/yolo")
    def stream_yolo():
        """실시간 YOLO 모니터링 (원본 + 박스, 초록 격자 없음)."""

        def gen():
            while True:
                jpg = STORE.get_yolo_jpeg()
                if jpg is None:
                    blank = np.zeros((360, 640, 3), dtype=np.uint8)
                    cv2.putText(
                        blank,
                        "Waiting YOLO...",
                        (170, 170),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 255),
                        2,
                    )
                    ok, buf = cv2.imencode(".jpg", blank)
                    jpg = buf.tobytes() if ok else b""
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                )
                time.sleep(0.05)

        return Response(
            gen(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/stream/precise")
    def stream_precise():
        """PRECISE 전용 안전지도 스트림 (FAST와 독립)."""

        def gen():
            while True:
                jpg = STORE_PRECISE.get_safety_jpeg()
                if jpg is None:
                    blank = np.zeros((360, 640, 3), dtype=np.uint8)
                    blank = draw_bottom_clock(blank)
                    cv2.putText(
                        blank,
                        "Waiting PRECISE...",
                        (150, 170),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (200, 200, 255),
                        2,
                    )
                    ok, buf = cv2.imencode(".jpg", blank)
                    jpg = buf.tobytes() if ok else b""
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                )
                time.sleep(0.05)

        return Response(
            gen(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/stream/raw")
    def stream_raw():
        """YOLO가 보는 원본 스트림 (초록 격자 없음)."""

        def gen():
            while True:
                jpg = STORE.get_raw_jpeg()
                if jpg is None:
                    blank = np.zeros((360, 640, 3), dtype=np.uint8)
                    cv2.putText(
                        blank,
                        "Waiting raw...",
                        (180, 170),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 255),
                        2,
                    )
                    ok, buf = cv2.imencode(".jpg", blank)
                    jpg = buf.tobytes() if ok else b""
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                )
                time.sleep(0.05)

        return Response(
            gen(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/")
    def index():
        return """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>실시간 안전지도 · YOLO</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 12px;
      font-family: "Malgun Gothic", sans-serif;
      background: #111; color: #f2f2f2;
    }
    h1 { font-size: 18px; font-weight: 600; margin: 0 0 8px; }
    h2 { font-size: 14px; font-weight: 600; margin: 14px 0 6px; color: #ddd; }
    .meta { font-size: 13px; color: #bbb; margin-bottom: 10px; }
    .note { font-size: 12px; color: #888; margin: 0 0 8px; }
    .sources {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      max-width: 1100px;
      margin: 0 0 12px;
    }
    @media (min-width: 900px) {
      .sources { grid-template-columns: 1fr 1fr 1fr; }
    }
    .src {
      border: 1px solid #333;
      background: #1a1a1a;
      padding: 10px 12px;
    }
    .src .label { font-size: 11px; color: #888; letter-spacing: 0.04em; }
    .src .value { font-size: 20px; font-weight: 700; margin: 4px 0; }
    .src .sub { font-size: 12px; color: #aaa; }
    .lvl1 { color: #8fd98f; }
    .lvl2 { color: #e6d27a; }
    .lvl3 { color: #f0a060; }
    .lvl4 { color: #ff8a8a; }
    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      max-width: 1100px;
    }
    @media (min-width: 1000px) {
      .grid.two { grid-template-columns: 1fr 1fr; }
    }
    img {
      display: block; width: 100%;
      background: #000; border: 1px solid #333;
    }
    #alert {
      margin-top: 10px; max-width: 1100px;
      font-size: 14px; line-height: 1.45;
      white-space: pre-line;
    }
    .danger { color: #ffb4b4; }
    .ok { color: #b8f5b8; }
  </style>
</head>
<body>
  <h1>실시간 해변 안전지도</h1>
  <div class="meta" id="meta">연결 중…</div>
  <p class="note">YOLO는 원본만 분석합니다. 박스는 최신 영상에 바로 표시됩니다. 군중 카운팅(밀도추정)·SK 혼잡은 보조 참고용입니다.</p>

  <div class="sources">
    <div class="src">
      <div class="label">FAST (경보)</div>
      <div class="value" id="fastVal">—</div>
      <div class="sub" id="fastSub">yolo26s · 수 초</div>
    </div>
    <div class="src">
      <div class="label">PRECISE (보정)</div>
      <div class="value" id="preciseVal">—</div>
      <div class="sub" id="preciseSub">SAHI 백그라운드</div>
    </div>
    <div class="src">
      <div class="label">군중 카운팅 (AI 추정)</div>
      <div class="value" id="crowdVal">—</div>
      <div class="sub" id="crowdSub">밀도추정 병행</div>
    </div>
    <div class="src">
      <div class="label">SK 혼잡도 (보조)</div>
      <div class="value" id="skVal">—</div>
      <div class="sub" id="skSub">지오비전 퍼즐</div>
    </div>
  </div>

  <p class="note">FAST=경량 SAHI(yolo26s·슬라이스384·1스케일) · PRECISE=SAHI(yolo26s·긴변축소). 위험 경보는 FAST.</p>
  <h2>모니터링 (fast 경보 + precise 보정)</h2>
  <img src="/stream/yolo" alt="accuracy-max monitor"/>
  <p class="note">주황=확정 사람 · 회색=기각(저확신/가로형=우산·배 등). 확정=스케일합의 또는 conf≥0.35 + 세로형 필터.</p>

  <h2>안전지도 (FAST 기준)</h2>
  <img src="/stream" alt="safety map"/>

  <h2>PRECISE 보정 (별도 스트림)</h2>
  <img src="/stream/precise" alt="precise safety map"/>

  <div id="alert" class="ok"></div>
  <script>
    function skLevelClass(level) {
      if (level === 1) return 'lvl1';
      if (level === 2) return 'lvl2';
      if (level === 3) return 'lvl3';
      if (level === 4) return 'lvl4';
      return '';
    }
    async function tick() {
      try {
        const r = await fetch('/api/status').then(x => x.json());
        const pipe = r.pipeline || '-';
        document.getElementById('meta').textContent =
          (r.nowKst || '') +
          ' · ' + (r.detector || 'sahi').toUpperCase() +
          ' · ' + pipe +
          ' 확정인원 ' + (r.personCount ?? 0) +
          ' · 기각 ' + (r.rejectedCount ?? 0) +
          ' · minConf ' + Number(r.personMinConf || 0.4).toFixed(2) +
          ' · 박스 ' + (r.yoloBoxCount ?? 0) +
          ' · 추론 ' + Number(r.yoloInferMs || 0).toFixed(0) + 'ms' +
          ' · 최대 ' + Number(r.maxGridDensityPerM2 || 0).toFixed(2) + '명/㎡' +
          ' · 격자 ' + (r.grid?.cellW || 40) + 'x' + (r.grid?.cellH || 15);

        const dens = Number(r.maxGridDensityPerM2 || 0).toFixed(2) + '명/㎡';
        const people = (r.personCount ?? 0) + '명';
        document.getElementById('fastVal').textContent =
          (pipe === 'precise' ? people + ' · ' : '') + dens;
        document.getElementById('fastSub').textContent =
          '경보 기준 · pipeline=' + pipe + ' · ' + Number(r.yoloInferMs || 0).toFixed(0) + 'ms';

        const pr = r.precise || {};
        const prState = pr.state || 'idle';
        const hasPrecise = !!pr.updatedAt;
        if (hasPrecise) {
          document.getElementById('preciseVal').textContent =
            (pr.personCount ?? 0) + '명';
          if (prState === 'running' || prState === 'loading') {
            document.getElementById('preciseSub').textContent =
              '재보정 중 ' + (pr.progress || '') +
              ' · 직전 ' + Number(pr.inferMs || 0).toFixed(0) + 'ms';
          } else if (prState === 'error') {
            document.getElementById('preciseSub').textContent =
              '오류 · 직전 ' + (pr.personCount ?? 0) + '명 · ' + (pr.lastError || '');
          } else {
            document.getElementById('preciseSub').textContent =
              '보정 완료 · ' + Number(pr.inferMs || 0).toFixed(0) + 'ms · 기각 ' +
              (pr.rejectedCount ?? 0);
          }
        } else if (prState === 'running' || prState === 'loading') {
          document.getElementById('preciseVal').textContent = '(보정 중)';
          document.getElementById('preciseSub').textContent =
            prState === 'loading'
              ? '모델 로딩'
              : ('진행 ' + (pr.progress || '멀티스케일 SAHI'));
        } else if (prState === 'error') {
          document.getElementById('preciseVal').textContent = '오류';
          document.getElementById('preciseSub').textContent =
            pr.lastError || 'PRECISE 실패';
        } else {
          document.getElementById('preciseVal').textContent = '(대기)';
          document.getElementById('preciseSub').textContent = '백그라운드 SAHI 대기';
        }

        const cw = r.crowd || {};
        const cwState = cw.state || 'idle';
        const crowdValEl = document.getElementById('crowdVal');
        const crowdSubEl = document.getElementById('crowdSub');
        if (cwState === 'disabled') {
          crowdValEl.textContent = '꺼짐';
          crowdSubEl.textContent = '병행 비활성화';
        } else if (cw.updatedAt) {
          crowdValEl.textContent = Math.round(cw.count ?? 0) + '명';
          crowdSubEl.textContent =
            (cw.model || '밀도추정') + ' · ' +
            Number(cw.inferMs || 0).toFixed(0) + 'ms' +
            (cwState === 'running' ? ' · 재추정 중' : '');
        } else if (cwState === 'running' || cwState === 'loading') {
          crowdValEl.textContent = '(추정 중)';
          crowdSubEl.textContent =
            cwState === 'loading' ? '모델 로딩' : (cw.model || '밀도추정');
        } else if (cwState === 'error') {
          crowdValEl.textContent = '오류';
          crowdSubEl.textContent = cw.lastError || '군중 카운팅 실패';
        } else {
          crowdValEl.textContent = '(대기)';
          crowdSubEl.textContent = cw.model || '밀도추정 병행';
        }

        const t = r.telecom || {};
        const p = (t.places && t.places[0]) || null;
        const skEl = document.getElementById('skVal');
        if (p && (p.congestionLabel || p.congestionLevel)) {
          skEl.textContent = p.congestionLabel || ('레벨 ' + p.congestionLevel);
          skEl.className = 'value ' + skLevelClass(p.congestionLevel);
          const densSk = p.congestionPerSquareMeter != null
            ? Number(p.congestionPerSquareMeter).toFixed(3) + '명/㎡'
            : '-';
          document.getElementById('skSub').textContent =
            densSk + ' · ' + (t.apiStatus || '-') + ' · ' + (p.poiName || p.zoneName || 'SK');
        } else {
          skEl.textContent = t.apiStatus || '대기';
          skEl.className = 'value';
          document.getElementById('skSub').textContent = t.message || 'SK 혼잡도';
        }

        const el = document.getElementById('alert');
        if (r.alerts && r.alerts.hasDanger) {
          el.className = 'danger';
          el.textContent =
            '[관광객] ' + r.alerts.touristMessage + '\\n' +
            '[관리자] ' + r.alerts.managerMessage;
        } else {
          el.className = 'ok';
          el.textContent = '위험 격자 없음';
        }
      } catch (e) {
        document.getElementById('meta').textContent = '상태 조회 실패';
      }
    }
    tick();
    setInterval(tick, 1000);
  </script>
</body>
</html>
"""

    return app


def main():
    global CELL_W, CELL_H, PERSON_MIN_CONF

    parser = argparse.ArgumentParser(description="Realtime beach safety map stream")
    parser.add_argument(
        "--source",
        default=DEFAULT_YOUTUBE,
        help="YouTube URL 또는 RTSP/파일 경로",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--analyze-every", type=float, default=0.2)
    parser.add_argument("--cell-w", type=int, default=40, help="격자 너비(px)")
    parser.add_argument("--cell-h", type=int, default=15, help="격자 높이(px)")
    parser.add_argument(
        "--detector",
        choices=["sahi", "yolo"],
        default=DEFAULT_DETECTOR,
        help="탐지 방식 (기본 SAHI 정확도 모드)",
    )
    parser.add_argument("--conf", type=float, default=SAHI_CONF, help="proposal confidence")
    parser.add_argument(
        "--person-min-conf",
        type=float,
        default=PERSON_MIN_CONF,
        help="확정 사람 최소 확신도 (미만은 밀도 제외)",
    )
    parser.add_argument("--imgsz", type=int, default=YOLO_IMGSZ, help="YOLO input size")
    parser.add_argument(
        "--detect-upscale",
        type=float,
        default=None,
        help="탐지 전 확대 배율 (미지정 시 SAHI 멀티스케일 / YOLO=2.0)",
    )
    parser.add_argument("--slice-size", type=int, default=SAHI_SLICE)
    parser.add_argument("--overlap", type=float, default=SAHI_OVERLAP)
    parser.add_argument(
        "--model",
        default=None,
        help="가중치 경로 (미지정 시 파인튜닝/ yolo8l 자동 선택)",
    )
    parser.add_argument(
        "--no-temporal",
        action="store_true",
        help="시간축 안정화 끄기",
    )
    args = parser.parse_args()

    CELL_W, CELL_H = args.cell_w, args.cell_h
    PERSON_MIN_CONF = float(args.person_min_conf)
    STORE.cell_w, STORE.cell_h = CELL_W, CELL_H
    upscale = args.detect_upscale
    if upscale is None:
        upscale = SAHI_UPSCALE if args.detector == "sahi" else DETECT_UPSCALE
    model_path = resolve_best_model(args.model)

    eprint("=" * 60)
    eprint("실시간 안전지도 (이중 경로: fast 경보 + precise 보정)")
    eprint(f"source   = {args.source}")
    eprint(f"model    = {model_path}")
    eprint(f"device   = {resolve_device_label()}")
    eprint(f"grid     = {args.cell_w}x{args.cell_h} (w x h)")
    eprint(
        f"person   = proposal>={args.conf} confirm>={PERSON_MIN_CONF} "
        f"(low-conf excluded from density)"
    )
    eprint(
        f"detector = {args.detector} "
        f"overlap={args.overlap} multi={SAHI_MULTI_SCALES}/{SAHI_MULTI_SLICES}"
    )
    eprint(
        f"fast     = 경량 SAHI m slice={FAST_SAHI_SLICE} "
        f"upscale={FAST_SAHI_UPSCALE} overlap={FAST_SAHI_OVERLAP}"
    )
    eprint("pipeline = FAST(SAHI-lite) + PRECISE(SAHI multi-scale)")
    eprint("telecom  = SK 지오비전 퍼즐 장소 혼잡도 (보조, 10분 캐시)")
    eprint(f"stream   = http://127.0.0.1:{args.port}/stream")
    eprint(f"precise  = http://127.0.0.1:{args.port}/stream/precise")
    eprint(f"yolo     = http://127.0.0.1:{args.port}/stream/yolo")
    eprint(f"telecom  = http://127.0.0.1:{args.port}/api/telecom")
    eprint(f"UI       = http://127.0.0.1:{args.port}/")
    eprint(
        f"규칙     = <{THRESH_CAUTION} 초록 / "
        f"{THRESH_CAUTION}~{THRESH_DANGER} 노랑 / >={THRESH_DANGER} 빨강"
    )
    eprint("=" * 60)

    t_cap = threading.Thread(
        target=capture_loop, args=(args.source,), daemon=True
    )
    t_cap.start()

    t_sk = threading.Thread(target=sk_refresh_loop, daemon=True)
    t_sk.start()

    if CROWD_ENABLED:
        t_crowd = threading.Thread(target=crowd_count_loop, daemon=True)
        t_crowd.start()
        eprint(f"crowd    = {CROWD_MODEL}/{CROWD_WEIGHTS} 병행 (밀도추정, {CROWD_INTERVAL_SEC:.0f}s)")

    if args.detector == "sahi":
        t_fast = threading.Thread(
            target=fast_analyze_loop,
            args=(model_path, FAST_EVERY_SEC, args.cell_w, args.cell_h, FAST_CONF),
            kwargs={"fast_backend": "sahi"},
            daemon=True,
        )
        t_precise = threading.Thread(
            target=precise_analyze_loop,
            args=(model_path, args.cell_w, args.cell_h, args.conf, PRECISE_OVERLAP),
            daemon=True,
        )
        t_fast.start()
        t_precise.start()
    else:
        t_ana = threading.Thread(
            target=fast_analyze_loop,
            args=(
                model_path,
                args.analyze_every,
                args.cell_w,
                args.cell_h,
                args.conf,
                args.imgsz,
                upscale,
            ),
            kwargs={"fast_backend": "yolo"},
            daemon=True,
        )
        t_ana.start()

    app = create_app()
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
