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
import zipfile
from collections import deque
from statistics import median
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
from flask import Flask, Response, jsonify, request, send_file
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


def _parse_exclude_zones(s: str) -> list:
    """"x1,y1,x2,y2;x1,y1,x2,y2" → [(x1,y1,x2,y2), ...] (정규화 좌표)."""
    zones = []
    for part in s.split(";"):
        vals = [v.strip() for v in part.split(",")]
        if len(vals) == 4:
            try:
                zones.append(tuple(float(v) for v in vals))
            except ValueError:
                pass
    return zones


# 고정 카메라 오탐 제외 구역(정규화 x1,y1,x2,y2). 항상 같은 자리의 구조물을
# 사람으로 오탐하는 경우 여기에 추가. 기본: 우측 하단 노란 에어바운스 조형물.
# VISION_EXCLUDE_ZONES="x1,y1,x2,y2;..." 환경변수로 교체 가능.
EXCLUDE_ZONES = _parse_exclude_zones(
    os.environ.get("VISION_EXCLUDE_ZONES", "0.855,0.865,1.0,1.0")
)


def make_live_roi_mask(h: int, w: int):
    """LIVE_ROI 마스크에서 고정 오탐 구역(EXCLUDE_ZONES)을 뺀 탐지 마스크.

    탐지 함수들이 박스 중심점을 이 마스크로 거르므로, 제외 구역의
    구조물(예: 에어바운스)은 라이브·라벨링 모두에서 무시된다.
    """
    mask, pts = make_roi_mask(h, w, LIVE_ROI)
    for (zx1, zy1, zx2, zy2) in EXCLUDE_ZONES:
        cv2.rectangle(
            mask,
            (int(zx1 * w), int(zy1 * h)),
            (int(zx2 * w), int(zy2 * h)),
            0,
            -1,
        )
    return mask, pts
# 더 작은 직사각 격자 (너비 40, 높이 15)
CELL_W = 40
CELL_H = 15
# 사람/비사람: 저확신은 스케일 합의, 고확신은 단독 통과 + 형태필터
# 0.08은 열린 바다·파도에 저확신 제안이 수백 개 → 기각 회색 박스가 UI를 덮음.
PERSON_PROPOSAL_CONF = float(os.environ.get("VISION_PROPOSAL_CONF", "0.16"))
# 파인튜닝(yolo26s_beach_ft) 모델은 확신도가 낮게 나오는 경향이 있음.
# 전역으로 너무 낮추면(0.12~0.16) 먼 바다 안전 부표가 튜브/사람으로 통과함.
# → 전역은 중간값, 먼 바다(부표 띠)는 별도 엄격 규칙으로 분리.
PERSON_MIN_CONF = float(os.environ.get("VISION_PERSON_MIN_CONF", "0.28"))
PERSON_HIGH_CONF = 0.35
PERSON_MIN_BOX_AREA = 48.0
PERSON_MIN_BOX_H = 12.0
PERSON_MIN_AREA_FRAC = 0.00008  # 프레임 대비 너무 작은 점 노이즈 차단
PERSON_MAX_ASPECT_W_OVER_H = 1.8  # 우산·파라솔·배(가로형) 강하게 차단 (서있는 사람 기준)
PERSON_MIN_ASPECT_H_OVER_W = 0.70  # 세로형(사람) 선호 (서있는 사람 기준)
PERSON_MAX_AREA_FRAC = 0.05
PERSON_MAX_WIDTH_FRAC = 0.18
# 기각(회색 X) 박스: 기본 비표시. 디버그 시 VISION_DRAW_REJECTED=1
DRAW_REJECTED = os.environ.get("VISION_DRAW_REJECTED", "0").strip() in (
    "1", "true", "yes",
)
# ── 구역 인지 필터: 물/모래별 규칙 (파도 오탐↓, 수영자·튜브·파라솔 아래 회수↑) ──
# 광안리 고정 캠 구도: 세로 0.45~0.78 물, 0.78 아래 모래(파라솔)
WATER_Y_TOP = float(os.environ.get("VISION_WATER_TOP", "0.45"))
WATER_Y_BOT = float(os.environ.get("VISION_WATER_BOT", "0.78"))
# 먼 바다(부표 줄 구간). 라이브 프레임 기준 y≈0.45~0.55 에 안전 부표가 가로로 늘어서 있음.
# 이 띠에서는 작은·노란·저확신 후보를 사람/튜브로 인정하지 않는다.
FAR_WATER_Y = float(os.environ.get("VISION_FAR_WATER_Y", "0.55"))
FAR_PERSON_MIN_CONF = float(os.environ.get("VISION_FAR_PERSON_MIN_CONF", "0.40"))
FAR_TUBE_MIN_CONF = float(os.environ.get("VISION_FAR_TUBE_MIN_CONF", "0.40"))
FAR_MAX_AREA_FRAC = 0.0007   # 이보다 작으면 부표/노이즈로 간주 (프레임 대비)
FAR_MAX_BOX_H_FRAC = 0.025   # 박스 높이 상한 (프레임 높이 비율)
# 가까운 입수대 부표: 점 크기만 차단 (중간 크기=튜브 후보로 유지)
NEAR_BUOY_MAX_AREA_FRAC = 0.00015
NEAR_BUOY_MAX_H_FRAC = 0.020
NEAR_TUBE_MIN_AREA_FRAC = 0.00010  # 사실상 점만 '옆에 사람' 요구
NEAR_BUOY_COLOR_AREA = 0.00035     # 부표색이 뚜렷할 때만 조금 더 큰 것도 기각
# 물: 머리·상체만 내민 수영자 = 작은 정사각형~세로형 박스
# (너무 낮추면 열린 바다 잔물결이 통과 → 0.24)
SWIMMER_MIN_CONF = 0.24
SWIMMER_MAX_H_FRAC = 0.055    # 입수대 상반신 조금 더 허용
SWIMMER_MAX_W_OVER_H = 1.5
# 물: 튜브·서프보드 위 사람 = 가로형. 파도는 foam(색)으로 막는다.
FLOAT_MIN_CONF = 0.35
FLOAT_MAX_W_OVER_H = 2.6
# 모래: 서 있는 사람 / 파라솔(빨간·가로형 캐노피) 기각
# 0.22도 빈 모래·그림자 오탐이 남아 0.40으로 상향
BEACH_STAND_MIN_CONF = float(os.environ.get("VISION_BEACH_STAND_MIN_CONF", "0.40"))
BEACH_STAND_MAX_W_OVER_H = 1.65   # 서있는 사람: 파라솔(≥1.2 가로)보다 좁은 편
BEACH_STAND_MIN_H_OVER_W = 0.60
# 앉음·파라솔 아래 사람: 낮고 넓은 실루엣. 다만 캐노피만큼 넓으면 안 됨.
BEACH_SIT_MIN_CONF = 0.35
BEACH_SIT_MIN_H_OVER_W = 0.50
BEACH_SIT_MAX_W_OVER_H = 1.55   # 2.2는 파라솔 캐노피가 통과하기 쉬움
# 파라솔 색(광안리 빨간·주황 우산이 대부분). OpenCV H: 빨강 0~10/170~180, 주황~갈대 10~25
PARASOL_COLOR_FRAC = 0.28
PARASOL_S_MIN = 0.30
PARASOL_V_MIN = 0.30
PARASOL_MIN_W_OVER_H = 1.20   # 캐노피=가로로 넓은 박스
PARASOL_MAX_H_FRAC = 0.12     # 프레임 대비 너무 큰 가로 구조물
# 파도 거품: '밝고 저채도' 비율. 에지 단독 기각은 수영자 실루엣까지 죽여서 사용 금지.
FOAM_V_MIN = 0.70
FOAM_S_MAX = 0.28
FOAM_REJECT_FRAC = 0.52       # 0.42(과도)와 0.60(약함)의 중간
FOAM_OVERRIDE_CONF = 0.45     # 입수대 사람은 이 정도면 foam 검사 통과
FOAM_SOFT_FRAC = 0.28         # 중간 거품 + 강한 잔물결일 때만 보조 기각
FOAM_EDGE_WITH_SOFT = 0.28    # soft foam과 함께일 때만 에지 사용
# 부표(노랑~주황·연한 노랑). CCTV에선 채도가 낮게 보이므로 S 하한을 낮춤.
BUOY_H_MIN, BUOY_H_MAX = 8, 50
BUOY_S_MIN = 0.18
BUOY_V_MIN = 0.35
BUOY_COLOR_FRAC = 0.22        # 박스 내 부표색 픽셀 비율 ≥ 이면 부표 의심
# ── 튜브(class 1): 파인튜닝 모델 전용. 튜브는 물에서만 쓰므로 '사람 1명' 지표 ──
# 기본 COCO 모델에는 tube 클래스가 없어 자동으로 비활성(이름 기반 판별).
TUBE_CLASS_NAME = "tube"
# 0.14는 열린 바다 잔물결·부표를 tube로 통과시킴 → 색 검증과 함께 상향
TUBE_MIN_CONF = float(os.environ.get("VISION_TUBE_MIN_CONF", "0.35"))
TUBE_MIN_W_OVER_H = 0.50
TUBE_MAX_W_OVER_H = 3.8
TUBE_DUP_IOU = 0.30
TUBE_NEAR_PERSON_DIST = 0.12
TUBE_FOAM_REJECT_FRAC = 0.65  # 튜브 foam은 더 느슨 (흰 파도만)
# 튜브 색(주황·노랑·파랑·분홍) 픽셀 비율 — 색 없으면 열린 바다 오탐으로 기각
TUBE_COLOR_FRAC = 0.18
# 물가 튜브는 박스 중심이 WATER_Y_BOT(0.78)보다 아래(모래쪽)로 살짝 내려옴 → 상한을 넓힘
TUBE_Y_BOT = float(os.environ.get("VISION_TUBE_Y_BOT", "0.90"))
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
# GPU 없는 CPU에서 물 구역 고해상 재스캔은 슬라이스 수백 개(수분) → FAST를 굶김.
# FAST 원근밴드(물 2.8x)가 이미 물·모래를 커버하므로 기본 OFF, GPU 등에서만 ON.
PRECISE_ENABLED = os.environ.get("VISION_PRECISE", "0").strip() not in ("0", "false", "")
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
# ── 원근 대응 밴드(고정 카메라: 화면 y = 거리) ──
# 하늘·다리(상단 WATER_Y_TOP 위)는 크롭해 추론에서 제외(공짜 속도).
# 물(멀다)=고배율로 먼 수영자 회수↑, 모래(가깝다)=저배율로 낭비↓.
# (y_top_frac, y_bot_frac, upscale, slice[, overlap])
# overlap 0.40 A/B: 확정 person/tube 동일·기각만 +30~40 → 기본 0.22 유지.
FAST_BANDS = (
    (WATER_Y_TOP, FAR_WATER_Y, 2.2, 384, 0.22),  # 먼 바다(부표 띠)
    (FAR_WATER_Y, WATER_Y_BOT, 2.8, 384, 0.22),  # 가까운 입수대
    (WATER_Y_BOT, 1.0, 2.0, 384, 0.22),          # 모래
)
# PRECISE 물 구역 전담(원거리 수영자 정밀): FAST 물 밴드(2.8x)보다 높은 해상
PRECISE_WATER_UPSCALE = float(os.environ.get("VISION_PRECISE_WATER_UPSCALE", "3.6"))
PRECISE_WATER_SLICE = int(os.environ.get("VISION_PRECISE_WATER_SLICE", "256"))
PRECISE_WATER_OVERLAP = 0.3
PRECISE_WATER_MAX_EDGE = int(os.environ.get("VISION_PRECISE_WATER_MAXEDGE", "1920"))
# --detector yolo 일 때만 사용 (호환)
FAST_MODEL_CANDIDATES = FAST_SAHI_MODEL_CANDIDATES
FAST_IMGSZ = 1280
FAST_UPSCALE = 2.8
FAST_SCALES = (2.2, 3.0)
FAST_MAX_DET = 500
FAST_USE_TTA = False
FAST_MIN_SCALE_VOTES = 2
# FAST 주기가 CPU에서 ~60초라 2프레임 합집합(history=2, min_hits=1)은
# 이동한 사람을 이중 카운트하고 파도 오탐을 2주기 유지시킴 → 비활성(1)
TEMPORAL_HISTORY = 1
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
            tube_n = sum(
                1 for b in self.yolo_boxes if len(b) >= 6 and int(b[5]) == 1
            )
            return {
                "status": self.status,
                "updatedAt": self.updated_at,
                "personCount": self.person_count,
                "tubeCount": tube_n,
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


# ── 모델 핫 리로드(무중단 재적용) ──────────────────────────
# Windows에서 os.execv 자기 재시작은 리스닝 소켓을 깔끔히 넘기지 못해 포트가
# 죽은 PID에 묶인다. 그래서 프로세스는 그대로 두고, FAST 루프가 다음 사이클에
# 새 가중치(models/yolo26s_beach_ft.pt)로 모델만 교체한다(다운타임 없음).
MODEL_RELOAD_REQUEST = threading.Event()
_RELOAD_META_LOCK = threading.Lock()
RELOAD_META: dict = {
    "state": "idle",   # idle | reloading | done | error
    "path": None,
    "requestedAt": None,
    "doneAt": None,
    "error": None,
}


def set_reload_meta(**kwargs):
    with _RELOAD_META_LOCK:
        RELOAD_META.update(kwargs)


def get_reload_meta() -> dict:
    with _RELOAD_META_LOCK:
        return dict(RELOAD_META)


# ── 학습용 ZIP 패키징(수집 프레임 → Colab용) ───────────────
# UI '학습용 ZIP 생성' 버튼이 pack_for_colab.ps1 과 동일한 내용의 zip을 만든다.
# (PowerShell 의존 없이 파이썬으로 직접 패킹 → 실행정책/인코딩 문제 없음)
DATASET_ZIP = Path(__file__).resolve().parent / "finetune" / "gwangalli_colab.zip"
_PACK_META_LOCK = threading.Lock()
PACK_META: dict = {
    "state": "idle",   # idle | packing | done | error
    "path": None,
    "sizeMB": None,
    "frames": 0,
    "startedAt": None,
    "doneAt": None,
    "error": None,
}


def set_pack_meta(**kwargs):
    with _PACK_META_LOCK:
        PACK_META.update(kwargs)


def get_pack_meta() -> dict:
    with _PACK_META_LOCK:
        return dict(PACK_META)


def _do_pack_dataset():
    """vision 코드 + finetune 스크립트/노트북 + 수집 원본(raw/*.jpg)을 zip으로."""
    root = Path(__file__).resolve().parent
    raw_dir = root / "finetune" / "raw"
    try:
        set_pack_meta(
            state="packing",
            error=None,
            startedAt=datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            doneAt=None,
            path=None,
            sizeMB=None,
        )
        frames = sorted(raw_dir.glob("*.jpg"))
        set_pack_meta(frames=len(frames))
        if not frames:
            set_pack_meta(state="error", error="수집된 프레임(raw/*.jpg)이 없습니다.")
            return
        tmp = DATASET_ZIP.with_name(DATASET_ZIP.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            # 1) vision 루트 .py (탐지 코드 + 로컬 모듈 의존)
            for py in sorted(root.glob("*.py")):
                z.write(py, f"vision/{py.name}")
            # 2) Colab에 필요한 finetune 스크립트
            for name in ("prelabel.py", "make_dataset.py", "flag_suspect.py"):
                p = root / "finetune" / name
                if p.exists():
                    z.write(p, f"vision/finetune/{name}")
            # 3) 최신 학습 노트북
            nb = root / "finetune" / "label_and_train_colab.ipynb"
            if nb.exists():
                z.write(nb, "vision/finetune/label_and_train_colab.ipynb")
            # 4) 수집 원본 = 학습 데이터
            for jp in frames:
                z.write(jp, f"vision/finetune/raw/{jp.name}")
        tmp.replace(DATASET_ZIP)
        size = round(DATASET_ZIP.stat().st_size / (1024 * 1024), 1)
        set_pack_meta(
            state="done",
            path=str(DATASET_ZIP),
            sizeMB=size,
            doneAt=datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        )
        eprint(f"[pack] ZIP 생성 완료 → {DATASET_ZIP} ({size} MB, {len(frames)} frames)")
    except Exception as exc:  # noqa: BLE001
        set_pack_meta(state="error", error=str(exc))
        eprint(f"[pack] 실패: {exc}")


def request_pack_dataset() -> bool:
    """ZIP 생성을 백그라운드로 시작. 이미 진행 중이면 False."""
    with _PACK_META_LOCK:
        if PACK_META.get("state") == "packing":
            return False
        PACK_META["state"] = "packing"
    threading.Thread(target=_do_pack_dataset, daemon=True).start()
    return True


# ── 군중 카운팅(밀도추정) 병행 ─────────────────────────────
# lwcc(DM-Count/CSRNet 등)로 원거리 밀집 군중을 추정. YOLO 탐지가 놓치는
# 먼 사람까지 density map으로 세어 보조 카운트를 제공한다. (CPU ~9s/프레임)
CROWD_ENABLED = os.environ.get("VISION_CROWD", "1").strip() not in ("0", "false", "")
CROWD_MODEL = os.environ.get("VISION_CROWD_MODEL", "DM-Count")  # DM-Count|CSRNet|Bay|SFANet
# QNRF(UCF-QNRF: 야외 고밀도) = 해변 CCTV에 가장 근접한 도메인.
# compare_crowd_models.py 실측: SHA=80.8/78.7(모래·파도 텍스처 과탐), QNRF=15.2/18.1(2개 아키텍처 일치).
# → 기본을 SHA(과탐)에서 QNRF로 교체. (env로 언제든 변경 가능)
CROWD_WEIGHTS = os.environ.get("VISION_CROWD_WEIGHTS", "QNRF")  # SHA|SHB|QNRF
# 앙상블: DM-Count+Bay 평균. CPU에선 INFER_LOCK을 오래 잡아 FAST 기각↑·확정↓
# → 기본 OFF. 필요 시 VISION_CROWD_ENSEMBLE=1.
CROWD_ENSEMBLE = os.environ.get("VISION_CROWD_ENSEMBLE", "0").strip() in (
    "1", "true", "yes",
)
CROWD_ENSEMBLE_MODELS = (("DM-Count", "QNRF"), ("Bay", "QNRF"))
CROWD_INTERVAL_SEC = float(os.environ.get("VISION_CROWD_INTERVAL", "30"))
CROWD_ROI_TOP = 0.45   # 상단(하늘·건물) 제외: LIVE_ROI와 동일 비율
CROWD_MAX_EDGE = int(os.environ.get("VISION_CROWD_MAX_EDGE", "1280"))  # 입력 긴 변 제한(연산 축소)
# 보정계수: 밀도모델 원시 → 광안리 구도 스케일.
# calibrate_crowd.py --gt: actual≈130 / ens≈42.7 → ≈3.04 (튜브색 하한+육안)
# 재보정: python calibrate_crowd.py --gt output/calib_samples.csv
CROWD_CALIB = float(os.environ.get("VISION_CROWD_CALIB", "3.0"))
# 시간축 평활: 최근 N회 보정값의 중앙값을 headline으로 사용(프레임별 노이즈·순간 과탐↓).
CROWD_SMOOTH_WINDOW = int(os.environ.get("VISION_CROWD_SMOOTH", "5"))
_CROWD_HISTORY: deque = deque(maxlen=max(1, CROWD_SMOOTH_WINDOW))
CROWD_INPUT = None     # 지연 초기화(출력 폴더)
_CROWD_META_LOCK = threading.Lock()
_CROWD_MODEL_LABEL = (
    "ensemble(DM-Count+Bay)/QNRF" if CROWD_ENSEMBLE else f"{CROWD_MODEL}/{CROWD_WEIGHTS}"
)
CROWD_META: dict = {
    "enabled": CROWD_ENABLED,
    "model": _CROWD_MODEL_LABEL,
    "state": "idle",   # idle | loading | running | ok | error | disabled
    "count": 0,        # 시간축 평활(중앙값) headline
    "countInstant": 0, # 이번 프레임 보정값
    "countRaw": 0,     # 모델(앙상블) 원시 출력
    "calib": CROWD_CALIB,
    "ensemble": CROWD_ENSEMBLE,
    "window": 0,       # 평활에 사용된 표본 수
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


def _foam_fraction(frame_bgr: np.ndarray, box) -> float:
    """박스 내 '밝고 저채도'(파도 거품) 픽셀 비율. 0.0~1.0."""
    if frame_bgr is None:
        return 0.0
    h, w = frame_bgr.shape[:2]
    x1 = max(0, int(round(box[0])))
    y1 = max(0, int(round(box[1])))
    x2 = min(w, int(round(box[2])))
    y2 = min(h, int(round(box[3])))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return 0.0
    patch = frame_bgr[y1:y2, x1:x2]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    return float(np.mean((v >= FOAM_V_MIN) & (s <= FOAM_S_MAX)))


def _wave_edge_fraction(frame_bgr: np.ndarray, box) -> float:
    """박스 내 밝기 에지 비율 — 파도 잔물결·거품 가장자리."""
    if frame_bgr is None:
        return 0.0
    h, w = frame_bgr.shape[:2]
    x1 = max(0, int(round(box[0])))
    y1 = max(0, int(round(box[1])))
    x2 = min(w, int(round(box[2])))
    y2 = min(h, int(round(box[3])))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return 0.0
    gray = cv2.cvtColor(frame_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140)
    return float(np.mean(edges > 0))


def _looks_like_foam(box, frame_bgr) -> bool:
    """물 구역 후보가 파도 거품인지 색상 중심으로 판정.

    주의: Canny 에지 단독 기각은 수영자 실루엣(물 대비 윤곽)까지 죽여서 쓰지 않는다.
    에지는 '어느 정도 거품색이 있을 때'만 보조 신호로 쓴다.
    """
    score = float(box[4])
    if score >= FOAM_OVERRIDE_CONF or frame_bgr is None:
        return False
    foam = _foam_fraction(frame_bgr, box)
    if foam >= FOAM_REJECT_FRAC:
        return True
    # 중간 거품 + 강한 잔물결만 파도로 (사람 윤곽만 있는 박스는 통과)
    if foam >= FOAM_SOFT_FRAC and _wave_edge_fraction(frame_bgr, box) >= FOAM_EDGE_WITH_SOFT:
        return True
    return False


def _box_cy_frac(box, h: float) -> float:
    return (float(box[1]) + float(box[3])) * 0.5 / max(1.0, float(h))


def _in_far_water(box, frame_hw) -> bool:
    """먼 바다(부표 띠): WATER_Y_TOP ≤ cy < FAR_WATER_Y."""
    if frame_hw is None:
        return False
    h = frame_hw[0]
    cy = _box_cy_frac(box, h)
    return WATER_Y_TOP <= cy < FAR_WATER_Y


def _box_hsv_patch(frame_bgr: np.ndarray, box):
    """박스 HSV 패치. 없거나 너무 작으면 None."""
    if frame_bgr is None:
        return None
    h, w = frame_bgr.shape[:2]
    x1 = max(0, int(round(box[0])))
    y1 = max(0, int(round(box[1])))
    x2 = min(w, int(round(box[2])))
    y2 = min(h, int(round(box[3])))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return cv2.cvtColor(frame_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)


def _buoy_color_fraction(frame_bgr: np.ndarray, box) -> float:
    """박스 내 노랑~주황(부표색) 픽셀 비율."""
    hsv = _box_hsv_patch(frame_bgr, box)
    if hsv is None:
        return 0.0
    hh = hsv[..., 0]
    ss = hsv[..., 1].astype(np.float32) / 255.0
    vv = hsv[..., 2].astype(np.float32) / 255.0
    mask = (
        (hh >= BUOY_H_MIN)
        & (hh <= BUOY_H_MAX)
        & (ss >= BUOY_S_MIN)
        & (vv >= BUOY_V_MIN)
    )
    return float(np.mean(mask))


def _tube_color_fraction(frame_bgr: np.ndarray, box) -> float:
    """튜브 특유 색(주황·노랑·파랑·분홍) 픽셀 비율.

    색이 거의 없으면 열린 바다/잔물결 오탐으로 본다.
    """
    hsv = _box_hsv_patch(frame_bgr, box)
    if hsv is None:
        return 0.0
    hh = hsv[..., 0]
    ss = hsv[..., 1].astype(np.float32) / 255.0
    vv = hsv[..., 2].astype(np.float32) / 255.0
    orange = (hh >= 5) & (hh <= 35) & (ss >= 0.25) & (vv >= 0.35)
    blue = (hh >= 90) & (hh <= 130) & (ss >= 0.25) & (vv >= 0.25)
    pink = ((hh >= 140) & (hh <= 175) & (ss >= 0.20) & (vv >= 0.35)) | (
        (hh <= 8) & (ss >= 0.25) & (vv >= 0.35)
    )
    return float(np.mean(orange | blue | pink))


def _looks_like_empty_water(box, frame_hw, frame_bgr) -> bool:
    """열린 바다 허공: 저분산·물색 위주·튜브색 거의 없음."""
    if frame_bgr is None or frame_hw is None:
        return False
    h, w = frame_hw
    cy = _box_cy_frac(box, h)
    if not (WATER_Y_TOP <= cy < WATER_Y_BOT):
        return False
    x1 = max(0, int(round(box[0])))
    y1 = max(0, int(round(box[1])))
    x2 = min(w, int(round(box[2])))
    y2 = min(h, int(round(box[3])))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return False
    patch = frame_bgr[y1:y2, x1:x2]
    if float(patch.std()) > 42.0:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hh = hsv[..., 0]
    ss = hsv[..., 1].astype(np.float32) / 255.0
    vv = hsv[..., 2].astype(np.float32) / 255.0
    water = float(np.mean(
        (hh >= 70) & (hh <= 115) & (ss >= 0.08) & (vv >= 0.15) & (vv <= 0.90)
    ))
    tube_c = _tube_color_fraction(frame_bgr, box)
    return water >= 0.35 and tube_c < 0.10


def _looks_like_safety_buoy(box, frame_hw, frame_bgr=None) -> bool:
    """안전 부표로 보이는지 (먼 바다 줄 + 가까운 입수대 '점' 부표).

    가까운 입수대에서는 중간 크기 후보를 튜브로 남긴다.
    부표로 자르는 경우: (1) 아주 작음 (2) 작~중 + 부표색 뚜렷.
    """
    if frame_hw is None:
        return False
    h, w = frame_hw
    cy = _box_cy_frac(box, h)
    if not (WATER_Y_TOP <= cy <= TUBE_Y_BOT):
        return False
    bw = max(0.0, float(box[2]) - float(box[0]))
    bh = max(0.0, float(box[3]) - float(box[1]))
    area_frac = (bw * bh) / float(max(1, h * w))
    colored = (
        frame_bgr is not None
        and _buoy_color_fraction(frame_bgr, box) >= BUOY_COLOR_FRAC
    )

    if cy < FAR_WATER_Y:
        small = area_frac <= FAR_MAX_AREA_FRAC or bh <= FAR_MAX_BOX_H_FRAC * h
        return bool(small)

    # 가까운 입수대·물가: 점 크기만 무조건 부표, 그 이상은 색이 맞을 때만
    if area_frac <= NEAR_BUOY_MAX_AREA_FRAC or bh <= NEAR_BUOY_MAX_H_FRAC * h:
        return True
    if colored and area_frac <= NEAR_BUOY_COLOR_AREA:
        return True
    return False


def _near_any_box(box, others: list, frame_hw, dist_frac: float) -> bool:
    """박스 중심이 다른 확정 박스 중심과 가까우면 True."""
    if not others or frame_hw is None:
        return False
    h, w = frame_hw
    diag = (h * h + w * w) ** 0.5
    lim = dist_frac * diag
    cx = (float(box[0]) + float(box[2])) * 0.5
    cy = (float(box[1]) + float(box[3])) * 0.5
    for o in others:
        ox = (float(o[0]) + float(o[2])) * 0.5
        oy = (float(o[1]) + float(o[3])) * 0.5
        if ((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5 <= lim:
            return True
    return False


def _parasol_color_fraction(frame_bgr: np.ndarray, box) -> float:
    """박스 내 파라솔(빨강·주황·갈대색) 픽셀 비율."""
    if frame_bgr is None:
        return 0.0
    h, w = frame_bgr.shape[:2]
    x1 = max(0, int(round(box[0])))
    y1 = max(0, int(round(box[1])))
    x2 = min(w, int(round(box[2])))
    y2 = min(h, int(round(box[3])))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return 0.0
    hsv = cv2.cvtColor(frame_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hh = hsv[..., 0]
    ss = hsv[..., 1].astype(np.float32) / 255.0
    vv = hsv[..., 2].astype(np.float32) / 255.0
    # 빨강(랩어라운드) + 주황~갈대
    red = ((hh <= 10) | (hh >= 170)) & (ss >= PARASOL_S_MIN) & (vv >= PARASOL_V_MIN)
    warm = (hh >= 8) & (hh <= 28) & (ss >= PARASOL_S_MIN) & (vv >= PARASOL_V_MIN)
    return float(np.mean(red | warm))


def _looks_like_parasol(box, frame_hw, frame_bgr=None) -> bool:
    """해변 파라솔 캐노피: 가로로 넓고 빨강·주황 천 비율이 높은 경우만.

    세로형(서 있는 사람) 박스는 절대 파라솔로 보지 않는다.
    """
    if frame_hw is None:
        return False
    h, w = frame_hw
    bw = max(1.0, float(box[2]) - float(box[0]))
    bh = max(1.0, float(box[3]) - float(box[1]))
    w_over_h = bw / bh
    # 세로형·정사각에 가까운 박스는 사람 후보
    if w_over_h < PARASOL_MIN_W_OVER_H:
        return False
    colored = _parasol_color_fraction(frame_bgr, box) >= PARASOL_COLOR_FRAC
    if colored:
        return True
    # 색이 약해도 매우 넓은 모래 위 캐노피
    if w_over_h >= 2.0 and (bw * bh) / float(max(1, h * w)) >= 0.0015:
        return True
    return False


def is_confident_person_box(box, frame_hw=None, frame_bgr=None) -> bool:
    """사람으로 '확실히' 인정할지 구역(물/모래) 인지로 판정.

    - 모래: 서있는 사람 문턱을 낮춰 회수↑, 파라솔 캐노피는 색·가로비로 기각
    - 물: 수영자/가로형 + 거품 검사, 먼 바다 부표 억제
    """
    x1, y1, x2, y2, score = box
    bw = max(0.0, float(x2) - float(x1))
    bh = max(0.0, float(y2) - float(y1))
    area = bw * bh
    if area < PERSON_MIN_BOX_AREA or bh < PERSON_MIN_BOX_H:
        return False
    zone = "std"
    if frame_hw is not None:
        h, w = frame_hw
        if x2 <= 0 or y2 <= 0 or x1 >= w or y1 >= h:
            return False
        frame_area = float(max(1, h * w))
        if area / frame_area < PERSON_MIN_AREA_FRAC:
            return False
        if area / frame_area > PERSON_MAX_AREA_FRAC:
            return False
        if bw > PERSON_MAX_WIDTH_FRAC * w:
            return False
        if _looks_like_safety_buoy(box, frame_hw, frame_bgr):
            return False
        # 구역 판정은 박스 하단(발이 닿는 쪽)이 아니라 중심 — 허공·열린 바다 오탐 방지
        cy = _box_cy_frac(box, h)
        if WATER_Y_TOP <= cy < WATER_Y_BOT:
            zone = "water"
            if _looks_like_empty_water(box, frame_hw, frame_bgr):
                return False
        elif cy >= WATER_Y_BOT:
            zone = "beach"
            if _looks_like_parasol(box, frame_hw, frame_bgr):
                return False
        else:
            # ROI 위(하늘·다리): 사람 확정 금지
            return False
    w_over_h = (bw / bh) if bh > 1e-6 else 99.0
    h_over_w = (bh / bw) if bw > 1e-6 else 99.0

    if zone == "beach":
        # 해변 서있는 사람: 세로형, 파라솔 폭 제한
        if (
            score >= BEACH_STAND_MIN_CONF
            and w_over_h <= BEACH_STAND_MAX_W_OVER_H
            and h_over_w >= BEACH_STAND_MIN_H_OVER_W
        ):
            return True
        # 앉음·파라솔 아래(사람만): 가로비 상한을 캐노피보다 낮게
        if (
            score >= BEACH_SIT_MIN_CONF
            and h_over_w >= BEACH_SIT_MIN_H_OVER_W
            and w_over_h <= BEACH_SIT_MAX_W_OVER_H
        ):
            return True
        return False

    # 먼 바다: 전역보다 높은 확신 요구 (부표·허공 오탐 억제)
    need = FAR_PERSON_MIN_CONF if _in_far_water(box, frame_hw) else PERSON_MIN_CONF

    # 표준 규칙: 서있는 사람 (물·기타)
    if (
        score >= need
        and w_over_h <= PERSON_MAX_ASPECT_W_OVER_H
        and h_over_w >= PERSON_MIN_ASPECT_H_OVER_W
    ):
        if zone == "water" and _looks_like_foam(box, frame_bgr):
            return False
        return True

    if zone == "water":
        if _in_far_water(box, frame_hw):
            return False
        is_small = frame_hw is not None and bh <= SWIMMER_MAX_H_FRAC * frame_hw[0]
        if (
            is_small
            and score >= SWIMMER_MIN_CONF
            and w_over_h <= SWIMMER_MAX_W_OVER_H
        ):
            return not _looks_like_foam(box, frame_bgr)
        if score >= FLOAT_MIN_CONF and w_over_h <= FLOAT_MAX_W_OVER_H:
            return not _looks_like_foam(box, frame_bgr)
        return False

    return False


def split_person_candidates(boxes: list, frame_hw=None, frame_bgr=None):
    """확정 사람 / 기각(낮은 확신·비정상 박스)으로 분리."""
    confirmed = []
    rejected = []
    for b in boxes:
        if is_confident_person_box(b, frame_hw=frame_hw, frame_bgr=frame_bgr):
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

    # 파인튜닝 모델(names에 tube 보유)일 때만 tube 클래스도 요청
    names = getattr(model, "names", {}) or {}
    tube_ok = str(names.get(1, "")).lower() == TUBE_CLASS_NAME
    results = model.predict(
        source=infer,
        classes=[0, 1] if tube_ok else [0],  # person(+tube) — 비사람 제외
        conf=conf,
        verbose=False,
        imgsz=imgsz,
        augment=bool(use_tta),
        max_det=int(max_det),
        iou=0.5,
        device=device,
    )
    boxes = []
    tube_boxes = []
    r0 = results[0]
    if r0.boxes is None:
        return [], boxes
    for box in r0.boxes:
        cls_id = int(box.cls[0]) if box.cls is not None else -1
        if cls_id != 0 and not (tube_ok and cls_id == 1):
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        if scale != 1.0:
            x1, y1, x2, y2 = x1 / scale, y1 / scale, x2 / scale, y2 / scale
        score = float(box.conf[0]) if box.conf is not None else 0.0
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        ix, iy = int(round(cx)), int(round(cy))
        if 0 <= ix < w and 0 <= iy < h and roi_mask[iy, ix] > 0:
            item = (float(x1), float(y1), float(x2), float(y2), score)
            (tube_boxes if cls_id == 1 else boxes).append(item)
    if not confirm:
        return boxes_to_centers(boxes), boxes
    confirmed, _ = split_person_candidates(boxes, frame_hw=(h, w), frame_bgr=clean)
    tubes_ok = filter_tubes(tube_boxes, (h, w), confirmed, frame_bgr=clean)
    out_boxes = confirmed + tubes_ok
    centers = boxes_to_centers(confirmed + _tubes_for_count(tubes_ok, confirmed))
    return centers, out_boxes


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
    confirmed, rejected = split_person_candidates(
        voted, frame_hw=(h, w), frame_bgr=frame_bgr
    )
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
    """score 내림차순 NMS. box=(x1,y1,x2,y2,score[,cls]).

    같은 클래스끼리만 억제한다. person↔tube가 겹쳐도(튜브 탄 사람)
    확신 낮은 튜브를 지우지 않음 — 클래스 무시 NMS는 tube recall을 죽임.
    """
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: b[4], reverse=True)
    keep = []
    while ordered:
        best = ordered.pop(0)
        keep.append(best)
        bc = best[5] if len(best) >= 6 else 0
        ordered = [
            b for b in ordered
            if (b[5] if len(b) >= 6 else 0) != bc or _box_iou(best, b) < iou_thresh
        ]
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

    # person(+파인튜닝 모델의 tube) 클래스만 후보 (우산·의자·배 등 비사람 제외)
    # box = (x1, y1, x2, y2, score, cls)  cls: 0=person, 1=tube
    boxes = []
    for p in result.object_prediction_list:
        name = str(p.category.name).lower()
        cat_id = getattr(p.category, "id", None)
        if name == "person" or cat_id in (0, "0"):
            cls = 0
        elif name == TUBE_CLASS_NAME or cat_id in (1, "1"):
            # 이름 또는 id=1 둘 다 허용 (SAHI가 id만 넘기는 경우 대비)
            cls = 1
        else:
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
            boxes.append((x1, y1, x2, y2, score, cls))
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
                (x1 * inv, y1 * inv, x2 * inv, y2 * inv, s, *rest)
                for x1, y1, x2, y2, s, *rest in boxes
            ]
        merged_boxes.extend(boxes)
        eprint(
            f"[precise] scale[{i + 1}/{n}] x{scale} slice={slice_size} "
            f"boxes={len(boxes)} ms={ms:.0f}"
        )
        if on_scale is not None:
            on_scale(i, n, scale, "after")

    kept = nms_boxes(merged_boxes, iou_thresh=nms_iou)
    persons, tubes = _split_by_class(kept)
    confirmed, rejected = split_person_candidates(
        persons, frame_hw=(h0, w0), frame_bgr=frame_bgr
    )
    tubes_ok = filter_tubes(tubes, (h0, w0), confirmed, frame_bgr=frame_bgr)
    out_boxes = confirmed + tubes_ok
    centers = boxes_to_centers(confirmed + _tubes_for_count(tubes_ok, confirmed))
    return centers, out_boxes, rejected


def detect_people_sahi_band(
    detection_model: AutoDetectionModel,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    y_top_frac: float,
    y_bot_frac: float,
    upscale: float,
    slice_size: int,
    overlap: float,
    max_edge: int = 0,
) -> list:
    """세로 밴드([y_top_frac,y_bot_frac])만 잘라 SAHI 탐지 → 원본 좌표 박스 반환.

    하늘·다리를 추론에서 제외(속도↑)하고 밴드별 배율(원근 대응)을 적용한다.
    max_edge>0이면 밴드 크롭의 긴 변을 제한(정밀 경로 비용 상한).
    """
    h, w = frame_bgr.shape[:2]
    y0 = max(0, int(round(y_top_frac * h)))
    y1 = min(h, int(round(y_bot_frac * h)))
    if y1 - y0 < 8:
        return []
    band = frame_bgr[y0:y1, 0:w]
    band_mask = roi_mask[y0:y1, 0:w]
    down = 1.0
    if max_edge and max(band.shape[0], band.shape[1]) > max_edge:
        band, down = fit_long_edge(band, max_edge)
        band_mask = cv2.resize(
            band_mask, (band.shape[1], band.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    boxes = detect_people_sahi(
        detection_model, band, band_mask,
        upscale=upscale, slice_size=slice_size, overlap=overlap,
    )
    inv = 1.0 / down if down != 1.0 else 1.0
    out = []
    for x1, yy1, x2, yy2, s, *rest in boxes:
        out.append((x1 * inv, yy1 * inv + y0, x2 * inv, yy2 * inv + y0, s, *rest))
    return out


def _split_by_class(boxes: list) -> tuple[list, list]:
    """(x1,y1,x2,y2,score[,cls]) 목록을 (persons, tubes) 5-튜플로 분리."""
    persons = []
    tubes = []
    for b in boxes:
        cls = b[5] if len(b) >= 6 else 0
        (tubes if cls == 1 else persons).append(tuple(b[:5]))
    return persons, tubes


def filter_tubes(tubes: list, frame_hw, confirmed_persons: list, frame_bgr=None) -> list:
    """튜브 박스 확정: 물 구역·튜브색·확신도 + 부표/파도 억제.

    열린 바다 오탐 방지: (1) 주황/파랑 등 튜브색 필수 (2) 문턱 상향
    (3) 먼 바다는 옆 사람 또는 강한 색+고확신.
    """
    if not tubes:
        return []
    h, w = frame_hw
    out = []
    for b in tubes:
        x1, y1, x2, y2, s = b
        if s < TUBE_MIN_CONF:
            continue
        cy = (y1 + y2) / 2.0 / max(1.0, float(h))
        if not (WATER_Y_TOP <= cy <= TUBE_Y_BOT):
            continue
        if _looks_like_safety_buoy(b, frame_hw, frame_bgr):
            continue
        if _looks_like_empty_water(b, frame_hw, frame_bgr):
            continue
        # 튜브 foam: 사람용보다 덜 민감 (색 있는 튜브 보호)
        if frame_bgr is not None and float(b[4]) < FOAM_OVERRIDE_CONF:
            if _foam_fraction(frame_bgr, b) >= TUBE_FOAM_REJECT_FRAC:
                continue
        # 색 없는 '튜브' = 거의 항상 잔물결/부표 오탐
        tube_c = _tube_color_fraction(frame_bgr, b) if frame_bgr is not None else 0.0
        if tube_c < TUBE_COLOR_FRAC:
            continue
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)
        area_frac = (bw * bh) / float(max(1, h * w))
        near_person = _near_any_box(
            b, confirmed_persons, frame_hw, TUBE_NEAR_PERSON_DIST
        )
        # 아주 작은 점: 옆 사람 없으면 부표
        if area_frac < NEAR_TUBE_MIN_AREA_FRAC and not near_person:
            continue
        if cy < FAR_WATER_Y:
            if s < FAR_TUBE_MIN_CONF:
                continue
            # 먼 바다: 옆 사람 또는 (강한 색 + 고확신)
            if not near_person and not (tube_c >= 0.35 and s >= 0.50):
                continue
        elif cy < WATER_Y_BOT:
            # 입수대·열린 물: 색은 이미 통과. 저확신은 옆 사람 필요
            if s < 0.45 and not near_person:
                continue
        ratio = bw / bh
        if not (TUBE_MIN_W_OVER_H <= ratio <= TUBE_MAX_W_OVER_H):
            continue
        if (bw * bh) > PERSON_MAX_AREA_FRAC * w * h:
            continue
        out.append((float(x1), float(y1), float(x2), float(y2), float(s), 1))
    return out


def _tubes_for_count(tubes: list, confirmed_persons: list) -> list:
    """인원 합산용: 확정 사람과 겹치는 튜브는 제외(이중 집계 방지)."""
    return [
        t for t in tubes
        if not any(_box_iou(t, p) >= TUBE_DUP_IOU for p in confirmed_persons)
    ]


def detect_people_sahi_fast(
    detection_model: AutoDetectionModel,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    upscale: float = FAST_SAHI_UPSCALE,
    slice_size: int = FAST_SAHI_SLICE,
    overlap: float = FAST_SAHI_OVERLAP,
    bands=FAST_BANDS,
):
    """FAST: 원근 밴드별 경량 SAHI(하늘 크롭) → NMS → 구역인지 필터.

    파인튜닝 모델(tube 클래스 보유) 사용 시 물 위 튜브를 사람 1명 지표로 합산.
    사람과 겹친 튜브는 화면(파란 박스)에는 남기고, centers(인원)에는 넣지 않는다.
    """
    h, w = frame_bgr.shape[:2]
    merged: list = []
    for band in bands:
        yt, yb, up, sl = band[0], band[1], band[2], band[3]
        ov = float(band[4]) if len(band) >= 5 else overlap
        merged.extend(
            detect_people_sahi_band(
                detection_model, frame_bgr, roi_mask,
                yt, yb, up, sl, ov,
            )
        )
    kept = nms_boxes(merged, iou_thresh=SAHI_NMS_IOU)
    persons, tubes = _split_by_class(kept)
    confirmed, rejected = split_person_candidates(
        persons, frame_hw=(h, w), frame_bgr=frame_bgr
    )
    tubes_ok = filter_tubes(tubes, (h, w), confirmed, frame_bgr=frame_bgr)
    boxes = confirmed + tubes_ok
    centers = boxes_to_centers(confirmed + _tubes_for_count(tubes_ok, confirmed))
    return centers, boxes, rejected


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

    # 기각 후보는 기본 비표시(열린 바다에 회색 X 수백 개가 UI를 덮음).
    # VISION_DRAW_REJECTED=1 일 때만 그림. 개수는 상단 텍스트에 항상 표기.
    if DRAW_REJECTED:
        for r in rejected or []:
            x1, y1, x2, y2, conf = r[0], r[1], r[2], r[3], r[4]
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

    tube_n = 0
    for b in boxes:
        x1, y1, x2, y2, conf = b[0], b[1], b[2], b[3], b[4]
        is_tube = len(b) >= 6 and int(b[5]) == 1
        p1 = (int(x1), int(y1))
        p2 = (int(x2), int(y2))
        # 튜브: 하늘색~파랑(BGR (255,150,0)) / 사람: 기존 노랑주황(0,220,255)
        color = (255, 150, 0) if is_tube else (0, 220, 255)
        label = f"tube {conf:.2f}" if is_tube else f"person {conf:.2f}"
        if is_tube:
            tube_n += 1
        cv2.rectangle(out, p1, p2, color, 2)
        cv2.putText(
            out,
            label,
            (p1[0], max(16, p1[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

    rej_n = len(rejected or [])
    person_n = len(boxes) - tube_n
    cv2.putText(
        out,
        f"{title}  person={person_n}  tube={tube_n}  rejected={rej_n}  minConf>={PERSON_MIN_CONF:.2f}",
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
            roi_mask, _ = make_live_roi_mask(h, w)
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
        # UI '모델 재적용' 버튼 → 다음 사이클에 새 가중치로 무중단 교체
        if MODEL_RELOAD_REQUEST.is_set():
            new_path = resolve_fast_sahi_model()
            set_reload_meta(state="reloading", path=new_path)
            try:
                with INFER_LOCK:
                    sahi_model = AutoDetectionModel.from_pretrained(
                        model_type="yolov8",
                        model_path=new_path,
                        confidence_threshold=conf if conf > 0 else SAHI_CONF,
                        device=sahi_dev,
                        image_size=FAST_SAHI_IMGSZ,
                    )
                stabilizer.buf.clear()
                set_reload_meta(
                    state="done",
                    path=new_path,
                    doneAt=datetime.now(timezone.utc).isoformat(),
                    error=None,
                )
                eprint(f"[reload] FAST 모델 교체 완료 → {new_path}")
            except Exception as exc:  # noqa: BLE001
                set_reload_meta(state="error", error=str(exc))
                eprint(f"[reload] 모델 교체 실패: {exc}")
            finally:
                MODEL_RELOAD_REQUEST.clear()
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
        roi_mask, _ = make_live_roi_mask(h, w)
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
            roi_mask, _ = make_live_roi_mask(h, w)
            H = scale_homography_for_frame(w, h)

            set_precise_meta(state="running", progress="water", lastError=None)
            eprint("[precise] start water-zone specialist SAHI (high-res)...")
            t_inf = time.perf_counter()
            try:
                # 물 구역만 FAST(2.8x)보다 높은 배율·촘촘한 슬라이스로 재스캔
                _on_scale(0, 1, PRECISE_WATER_UPSCALE, "before")
                try:
                    raw_boxes = detect_people_sahi_band(
                        sahi_model, raw, roi_mask,
                        WATER_Y_TOP, WATER_Y_BOT,
                        PRECISE_WATER_UPSCALE, PRECISE_WATER_SLICE,
                        PRECISE_WATER_OVERLAP,
                        max_edge=PRECISE_WATER_MAX_EDGE,
                    )
                finally:
                    _on_scale(0, 1, PRECISE_WATER_UPSCALE, "after")
                confirmed, rejected = split_person_candidates(
                    nms_boxes(raw_boxes, iou_thresh=SAHI_NMS_IOU),
                    frame_hw=(h, w), frame_bgr=raw,
                )
                boxes = confirmed
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
            # 메인 안전지도(STORE)는 FAST 기준을 유지한다.
            # PRECISE는 CPU 사정상 입력을 960px로 축소해 유효 해상도가 FAST보다
            # 낮으므로(1536 vs 3840), 메인을 덮어쓰면 오히려 인원이 줄어드는
            # 퇴행이 생긴다 → 전용 스트림·메타에만 발행.
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
    model_specs = (
        list(CROWD_ENSEMBLE_MODELS)
        if CROWD_ENSEMBLE
        else [(CROWD_MODEL, CROWD_WEIGHTS)]
    )
    eprint(f"[crowd] 모델 로딩 {_CROWD_MODEL_LABEL} ...")
    try:
        from lwcc import LWCC

        models = []
        with INFER_LOCK:
            for name, weights in model_specs:
                models.append(
                    (
                        f"{name}/{weights}",
                        LWCC.load_model(model_name=name, model_weights=weights),
                    )
                )
    except Exception as exc:
        eprint(f"[crowd] 로딩 실패(비활성화): {exc}")
        set_crowd_meta(state="error", lastError=str(exc))
        return

    set_crowd_meta(state="idle")
    eprint(f"[crowd] ready ({len(models)} model(s), calib×{CROWD_CALIB:g})")

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
                per_model = []
                for label, model in models:
                    per_model.append(
                        float(LWCC.get_count(str(CROWD_INPUT), model=model))
                    )
                raw_count = float(sum(per_model) / len(per_model))
                infer_ms = (time.perf_counter() - t0) * 1000.0
            wait_ms = (time.perf_counter() - wait0) * 1000.0 - infer_ms
            instant = raw_count * CROWD_CALIB  # 보정계수 적용
            # 시간축 평활: 최근 표본의 중앙값(순간 과탐/노이즈에 강건)
            _CROWD_HISTORY.append(instant)
            smoothed = float(median(_CROWD_HISTORY))

            set_crowd_meta(
                state="ok",
                count=round(smoothed, 1),
                countInstant=round(instant, 1),
                countRaw=round(raw_count, 1),
                calib=CROWD_CALIB,
                ensemble=CROWD_ENSEMBLE,
                window=len(_CROWD_HISTORY),
                inferMs=round(infer_ms, 0),
                updatedAt=datetime.now(timezone.utc).isoformat(),
                lastError=None,
            )
            parts = "+".join(f"{c:.1f}" for c in per_model)
            eprint(
                f"[crowd] {_CROWD_MODEL_LABEL} raw=[{parts}]→{raw_count:.1f} "
                f"calib×{CROWD_CALIB:g}={instant:.1f} "
                f"med{len(_CROWD_HISTORY)}={smoothed:.1f} "
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


def inspect_fast_model(load_classes: bool = False) -> dict:
    """FAST가 로드할 모델(파인튜닝 우선) 파일 상태 확인.

    load_classes=True 면 클래스(사람/튜브)까지 읽는다(YOLO 로드 → 느릴 수 있음).
    재시작 버튼은 응답이 빨라야 하므로 기본 False(파일 정보만)로 쓴다.
    """
    path = resolve_fast_sahi_model()
    p = Path(path)
    info: dict = {"path": str(path), "isLocalFile": p.exists()}
    if not p.exists():
        return info
    st = p.stat()
    info["sizeMB"] = round(st.st_size / (1024 * 1024), 1)
    info["modifiedAt"] = datetime.fromtimestamp(st.st_mtime, tz=KST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    if load_classes:
        try:
            # 추론 루프와 경합 방지를 위해 락 안에서 잠깐 로드.
            with INFER_LOCK:
                names = YOLO(str(p)).names
            info["classes"] = names
        except Exception as exc:  # noqa: BLE001
            info["classesError"] = str(exc)
    return info


def request_model_reload() -> dict:
    """FAST 루프에 무중단 모델 교체를 요청(다음 사이클에 새 가중치 로드).

    Windows에서 프로세스 자기 재시작(os.execv)은 리스닝 소켓을 놓지 못해
    포트가 죽은 PID에 묶이는 문제가 있어, 프로세스는 유지하고 모델만 교체한다.
    """
    set_reload_meta(
        state="reloading",
        requestedAt=datetime.now(timezone.utc).isoformat(),
        doneAt=None,
        error=None,
    )
    MODEL_RELOAD_REQUEST.set()
    return get_reload_meta()


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
        cw = get_crowd_meta()
        snap["crowd"] = cw
        # ── 탐지⊕밀도 융합 추정 ───────────────────────────────
        # 탐지(YOLO+SAHI)는 근거리·확정에 정확하나 원거리 밀집을 놓침.
        # 밀도추정(QNRF)은 원거리 밀집을 잡지만 순간 텍스처에 흔들림.
        # 두 추정의 최댓값을 "최소 이 정도"의 융합 추정으로 노출한다.
        det_total = int(snap.get("personCount", 0)) + int(snap.get("tubeCount", 0))
        dens = float(cw.get("count", 0) or 0) if cw.get("enabled") else 0.0
        fused = max(det_total, round(dens))
        snap["estimatedTotal"] = {
            "count": int(fused),
            "detection": det_total,
            "density": round(dens, 1),
            "source": "density" if dens > det_total else "detection",
        }
        snap["reload"] = get_reload_meta()
        snap["pack"] = get_pack_meta()
        return jsonify(snap)

    @app.get("/api/telecom")
    def telecom():
        """SK 지오비전 퍼즐 장소 혼잡도 (보조). ?force=1 로 캐시 무시."""
        force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
        return jsonify(fetch_sk_congestion(force=force))

    @app.get("/api/model-info")
    def model_info():
        """FAST가 로드할 모델(파인튜닝 우선) 파일 상태·클래스."""
        return jsonify(inspect_fast_model(load_classes=True))

    @app.post("/api/reload-model")
    def reload_model():
        """저장된 파인튜닝 모델을 확인하고 무중단으로 재적용.

        - models/yolo26s_beach_ft.pt 가 없으면 재적용하지 않고 안내.
        - 있으면 FAST 루프가 다음 사이클에 새 가중치로 교체(다운타임 없음).
        """
        info = inspect_fast_model()
        if not info.get("isLocalFile"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "message": "파인튜닝 모델 파일이 없습니다: " + info.get("path", "?")
                        + "  (Drive의 best.pt를 vision/models/yolo26s_beach_ft.pt 로 저장하세요)",
                        "model": info,
                    }
                ),
                404,
            )
        request_model_reload()
        return jsonify(
            {
                "ok": True,
                "message": "모델 확인 완료 · 다음 사이클에 무중단 재적용",
                "model": info,
            }
        )

    @app.post("/api/pack-dataset")
    def pack_dataset():
        """수집된 프레임으로 Colab 학습용 zip 생성(백그라운드)."""
        started = request_pack_dataset()
        if not started:
            return (
                jsonify({"ok": False, "message": "이미 ZIP 생성 중입니다."}),
                409,
            )
        return jsonify({"ok": True, "message": "ZIP 생성 시작"})

    @app.get("/api/dataset-zip")
    def dataset_zip():
        """생성된 학습용 zip 다운로드."""
        if not DATASET_ZIP.exists():
            return (
                jsonify({"ok": False, "message": "zip이 아직 없습니다. 먼저 생성하세요."}),
                404,
            )
        return send_file(
            str(DATASET_ZIP),
            as_attachment=True,
            download_name="gwangalli_colab.zip",
        )

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
    .controls {
      display: flex; align-items: center; gap: 10px;
      flex-wrap: wrap; margin: 0 0 12px; max-width: 1100px;
    }
    button.reload {
      background: #1f6fe5; color: #fff; border: 0;
      padding: 9px 16px; font-size: 14px; font-weight: 600;
      border-radius: 6px; cursor: pointer;
    }
    button.reload:hover { background: #1a5fc4; }
    button.reload:disabled { background: #444; cursor: not-allowed; }
    #reloadStatus { font-size: 12px; color: #9fb7d8; }
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
      <div class="label">PRECISE (물 정밀·선택)</div>
      <div class="value" id="preciseVal">—</div>
      <div class="sub" id="preciseSub">원거리 수영자 정밀</div>
    </div>
    <div class="src">
      <div class="label">군중 카운팅 (AI 추정)</div>
      <div class="value" id="crowdVal">—</div>
      <div class="sub" id="crowdSub">밀도추정 병행</div>
    </div>
    <div class="src">
      <div class="label">융합 추정 (탐지⊕밀도)</div>
      <div class="value" id="fusedVal">—</div>
      <div class="sub" id="fusedSub">최소 이 정도</div>
    </div>
    <div class="src">
      <div class="label">SK 혼잡도 (보조)</div>
      <div class="value" id="skVal">—</div>
      <div class="sub" id="skSub">지오비전 퍼즐</div>
    </div>
  </div>

  <p class="note">FAST=원근 밴드 SAHI(yolo26s): 하늘 크롭 후 물=2.8배(먼 수영자)·모래=1.4배(파라솔). PRECISE(물 정밀)는 GPU용 선택. 위험 경보는 FAST.</p>
  <h2>모니터링 (fast 경보)</h2>
  <img src="/stream/yolo" alt="accuracy-max monitor"/>
  <p class="note">주황=확정 사람 · 회색=기각. 구역 인지 필터: 물=수영자(작은 박스 conf≥0.20)·튜브(가로형 conf≥0.40)+파도 거품 색상 기각 / 모래=앉음·파라솔 아래(conf≥0.28) / 공통=서있는 사람 conf≥0.35+세로형.</p>

  <h2>안전지도 (FAST 기준)</h2>
  <img src="/stream" alt="safety map"/>

  <div id="alert" class="ok"></div>

  <h2>관리 도구</h2>
  <div class="controls">
    <button class="reload" id="packBtn" onclick="packDataset()">학습용 ZIP 생성</button>
    <span id="packStatus">수집된 프레임(finetune/raw)으로 Colab 학습용 zip을 만듭니다.</span>
  </div>
  <div class="controls">
    <button class="reload" id="reloadBtn" onclick="reloadModel()">모델 재적용 &amp; 서버 재시작</button>
    <span id="reloadStatus">파인튜닝 모델(models/yolo26s_beach_ft.pt)을 확인하고 무중단 재적용합니다.</span>
  </div>

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
        if (prState === 'disabled') {
          document.getElementById('preciseVal').textContent = '꺼짐';
          document.getElementById('preciseSub').textContent =
            'FAST 원근밴드가 커버 · VISION_PRECISE=1로 켜기';
        } else if (hasPrecise) {
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
          const calibTxt = (cw.calib && Number(cw.calib) !== 1)
            ? ' · ×' + Number(cw.calib).toFixed(2) : '';
          const medTxt = (cw.window > 1)
            ? ' · 중앙값' + cw.window + '회(순간 ' + Math.round(cw.countInstant ?? 0) + ')'
            : '';
          const ensTxt = cw.ensemble ? ' · 앙상블' : '';
          crowdSubEl.textContent =
            (cw.model || '밀도추정') + ensTxt + calibTxt + medTxt + ' · ' +
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

        const est = r.estimatedTotal || {};
        const fusedValEl = document.getElementById('fusedVal');
        const fusedSubEl = document.getElementById('fusedSub');
        if (est.count != null) {
          fusedValEl.textContent = Math.round(est.count) + '명';
          fusedSubEl.textContent =
            '탐지 ' + (est.detection ?? 0) + ' · 밀도 ' +
            Number(est.density ?? 0).toFixed(0) +
            ' · 채택=' + (est.source === 'density' ? '밀도' : '탐지');
        } else {
          fusedValEl.textContent = '—';
          fusedSubEl.textContent = '최소 이 정도';
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
    async function packDataset() {
      const btn = document.getElementById('packBtn');
      const st = document.getElementById('packStatus');
      if (!confirm('지금까지 수집된 프레임으로 Colab 학습용 ZIP을 만들까요?\\n프레임 수에 따라 수십 초 걸릴 수 있습니다.')) return;
      btn.disabled = true;
      st.textContent = 'ZIP 생성 시작…';
      try {
        const r = await fetch('/api/pack-dataset', { method: 'POST' }).then(x => x.json());
        if (!r.ok) {
          st.textContent = '실패: ' + (r.message || '알 수 없는 오류');
          btn.disabled = false;
          return;
        }
        waitForPack(st, btn);
      } catch (e) {
        st.textContent = '요청 실패: ' + e;
        btn.disabled = false;
      }
    }

    async function waitForPack(st, btn) {
      const t0 = Date.now();
      for (let i = 0; i < 200; i++) {
        await new Promise(res => setTimeout(res, 1500));
        try {
          const r = await fetch('/api/status', { cache: 'no-store' }).then(x => x.json());
          const pk = r.pack || {};
          const elapsed = Math.round((Date.now() - t0) / 1000);
          if (pk.state === 'done') {
            st.innerHTML = 'ZIP 완료 · ' + (pk.frames || 0) + '장 · ' +
              (pk.sizeMB != null ? pk.sizeMB + 'MB' : '') +
              ' (' + elapsed + 's) · ' +
              '<a href="/api/dataset-zip" style="color:#7fb2ff">다운로드</a>' +
              ' → Drive에 올리고 Colab 실행';
            btn.disabled = false;
            return;
          }
          if (pk.state === 'error') {
            st.textContent = 'ZIP 오류: ' + (pk.error || '알 수 없음');
            btn.disabled = false;
            return;
          }
          st.textContent = 'ZIP 생성 중… (' + (pk.frames || 0) + '장 포함, ' + elapsed + 's)';
        } catch (e) {
          /* 일시적 실패 무시하고 계속 폴링 */
        }
      }
      st.textContent = 'ZIP 생성 확인 시간 초과 · 잠시 후 다시 시도해주세요';
      btn.disabled = false;
    }

    async function reloadModel() {
      const btn = document.getElementById('reloadBtn');
      const st = document.getElementById('reloadStatus');
      if (!confirm('저장된 파인튜닝 모델을 확인하고 다시 적용할까요?\\n다음 분석 사이클에 무중단으로 교체됩니다(스트림 안 끊김).')) return;
      btn.disabled = true;
      st.textContent = '모델 확인 중…';
      try {
        const r = await fetch('/api/reload-model', { method: 'POST' }).then(x => x.json());
        if (!r.ok) {
          st.textContent = '실패: ' + (r.message || '알 수 없는 오류');
          btn.disabled = false;
          return;
        }
        const m = r.model || {};
        st.textContent = '적용 모델: ' + (m.path || '?') +
          ' · ' + (m.sizeMB != null ? m.sizeMB + 'MB' : '?') +
          ' · ' + (m.modifiedAt || '') + ' · 교체 대기 중…';
        waitForReload(st, btn);
      } catch (e) {
        st.textContent = '요청 실패: ' + e;
        btn.disabled = false;
      }
    }

    async function waitForReload(st, btn) {
      const t0 = Date.now();
      for (let i = 0; i < 120; i++) {
        await new Promise(res => setTimeout(res, 1500));
        try {
          const r = await fetch('/api/status', { cache: 'no-store' }).then(x => x.json());
          const rl = r.reload || {};
          const elapsed = Math.round((Date.now() - t0) / 1000);
          if (rl.state === 'done') {
            st.textContent = '재적용 완료 · 새 모델 반영됨 (' + elapsed + 's)';
            btn.disabled = false;
            document.querySelectorAll('img').forEach(im => {
              const base = im.src.split('?')[0];
              im.src = base + '?t=' + Date.now();
            });
            return;
          }
          if (rl.state === 'error') {
            st.textContent = '재적용 오류: ' + (rl.error || '알 수 없음');
            btn.disabled = false;
            return;
          }
          st.textContent = '모델 교체 대기 중… (' + elapsed + 's, 다음 사이클에 반영)';
        } catch (e) {
          /* 일시적 실패는 무시하고 계속 폴링 */
        }
      }
      st.textContent = '재적용 확인 시간 초과 · 잠시 후 다시 시도해주세요';
      btn.disabled = false;
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
        t_fast.start()
        if PRECISE_ENABLED:
            t_precise = threading.Thread(
                target=precise_analyze_loop,
                args=(model_path, args.cell_w, args.cell_h, args.conf, PRECISE_OVERLAP),
                daemon=True,
            )
            t_precise.start()
            eprint("precise  = 물 구역 원거리 수영자 정밀 (VISION_PRECISE=1)")
        else:
            set_precise_meta(state="disabled", progress=None)
            eprint(
                "precise  = 비활성(기본). FAST 원근밴드가 물·모래 모두 커버. "
                "GPU 등에서 VISION_PRECISE=1 로 물 정밀 활성화"
            )
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
