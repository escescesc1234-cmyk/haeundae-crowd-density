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
import random
import shutil
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
from precision_gate import GateConfig, WaterPrecisionGate
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
# (회수 기준값 = 튜브 도입·원근밴드 안정화 시점 0a52786. 오탐 대응으로 올렸던
#  proposal/beach/swimmer 문턱·empty_water 강제가 회수를 크게 떨어뜨림 → 원복)
PERSON_PROPOSAL_CONF = float(os.environ.get("VISION_PROPOSAL_CONF", "0.08"))
# 파인튜닝(yolo26s_beach_ft) 모델은 확신도가 낮게 나오는 경향이 있음.
# 전역으로 너무 낮추면(0.12~0.16) 먼 바다 안전 부표가 튜브/사람으로 통과함.
PERSON_MIN_CONF = float(os.environ.get("VISION_PERSON_MIN_CONF", "0.20"))
PERSON_HIGH_CONF = 0.35
# 점·부표급 초소형 박스 차단 (픽셀·프레임 비율)
PERSON_MIN_BOX_AREA = float(os.environ.get("VISION_PERSON_MIN_AREA", "36"))
PERSON_MIN_BOX_H = float(os.environ.get("VISION_PERSON_MIN_H", "14"))
PERSON_MIN_AREA_FRAC = float(os.environ.get("VISION_PERSON_MIN_AREA_FRAC", "0.00016"))
PERSON_MAX_ASPECT_W_OVER_H = 1.8  # 우산·파라솔·배(가로형) 강하게 차단 (서있는 사람 기준)
PERSON_MIN_ASPECT_H_OVER_W = 0.70  # 세로형(사람) 선호 (서있는 사람 기준)
PERSON_MAX_AREA_FRAC = 0.05
PERSON_MAX_WIDTH_FRAC = 0.18
# 기각(회색 X) 박스: 기본 비표시(UI 노이즈). 디버그 시 VISION_DRAW_REJECTED=1
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
FAR_PERSON_MIN_CONF = float(os.environ.get("VISION_FAR_PERSON_MIN_CONF", "0.28"))
FAR_TUBE_MIN_CONF = float(os.environ.get("VISION_FAR_TUBE_MIN_CONF", "0.40"))
FAR_MAX_AREA_FRAC = 0.0007   # 이보다 작으면 부표 후보 (색 있을 때만 기각)
FAR_MAX_BOX_H_FRAC = 0.025   # 박스 높이 상한 (프레임 높이 비율)
# 머리만 내민 수영자(먼 바다): 부표색 없으면 회수 허용하는 하한
FAR_SWIMMER_MIN_CONF = float(os.environ.get("VISION_FAR_SWIMMER_MIN_CONF", "0.18"))
# 가까운 입수대 부표: 점 크기만 차단 (중간 크기=튜브 후보로 유지)
NEAR_BUOY_MAX_AREA_FRAC = 0.00015
NEAR_BUOY_MAX_H_FRAC = 0.020
NEAR_TUBE_MIN_AREA_FRAC = 0.00010  # 사실상 점만 '옆에 사람' 요구
NEAR_BUOY_COLOR_AREA = 0.00035     # 부표색이 뚜렷할 때만 조금 더 큰 것도 기각
# 물: 머리·상체만 내민 수영자 = 작은 정사각형~세로형 박스, 확신 완화
SWIMMER_MIN_CONF = float(os.environ.get("VISION_SWIMMER_MIN_CONF", "0.12"))
SWIMMER_MAX_H_FRAC = 0.070    # 입수대 상반신·머리+어깨
SWIMMER_MAX_W_OVER_H = 1.7
# 물: 튜브·서프보드 위 사람 = 가로형. 파도는 foam(색)으로 막는다.
FLOAT_MIN_CONF = 0.32
FLOAT_MAX_W_OVER_H = 2.4
# 모래: 서 있는 사람 회수↑ / 파라솔(빨간·가로형 캐노피) 기각
BEACH_STAND_MIN_CONF = float(os.environ.get("VISION_BEACH_STAND_MIN_CONF", "0.10"))
BEACH_STAND_MAX_W_OVER_H = 1.75   # 서있는 사람: 파라솔(≥1.2 가로)보다 좁은 편
BEACH_STAND_MIN_H_OVER_W = 0.50   # 원거리 전신은 박스가 짧아져도 통과
# 앉음·파라솔 아래 사람: 낮고 넓은 실루엣. 다만 캐노피만큼 넓으면 안 됨.
BEACH_SIT_MIN_CONF = 0.14
BEACH_SIT_MIN_H_OVER_W = 0.45
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
FOAM_REJECT_FRAC = 0.42       # 허공·파도 사람 오탐↓ (해변은 zone별 미적용)
FOAM_OVERRIDE_CONF = 0.55     # 이 이상만 foam 검사 우회
FOAM_SOFT_FRAC = 0.22         # 중간 거품 + 강한 잔물결일 때만 보조 기각
FOAM_EDGE_WITH_SOFT = 0.22    # soft foam과 함께일 때만 에지 사용
# 물 구역 허공: 바다색 비율·대비 — 이하면 사람으로 안 봄
EMPTY_SEA_FRAC = float(os.environ.get("VISION_EMPTY_SEA", "0.48"))
EMPTY_STD_MAX = float(os.environ.get("VISION_EMPTY_STD", "42.0"))
WATER_PERSON_SEA_MAX = float(os.environ.get("VISION_WATER_SEA_MAX", "0.52"))
WATER_PERSON_SEA_CONF = float(os.environ.get("VISION_WATER_SEA_CONF", "0.45"))
# 부표(노랑~주황·연한 노랑). CCTV에선 채도가 낮게 보이므로 S 하한을 낮춤.
BUOY_H_MIN, BUOY_H_MAX = 8, 50
BUOY_S_MIN = 0.18
BUOY_V_MIN = 0.35
BUOY_COLOR_FRAC = 0.22        # 박스 내 부표색 픽셀 비율 ≥ 이면 부표 의심
# ── 튜브(class 1): 파인튜닝 모델 전용. 튜브는 물에서만 쓰므로 '사람 1명' 지표 ──
# 기본 COCO 모델에는 tube 클래스가 없어 자동으로 비활성(이름 기반 판별).
TUBE_CLASS_NAME = "tube"
TUBE_MIN_CONF = float(os.environ.get("VISION_TUBE_MIN_CONF", "0.28"))
# 부표·점 크기 튜브 차단 (conf는 낮춰도 너무 작은 건 제외)
TUBE_MIN_BOX_AREA = float(os.environ.get("VISION_TUBE_MIN_AREA", "48"))
TUBE_MIN_BOX_H = float(os.environ.get("VISION_TUBE_MIN_H", "12"))
TUBE_MIN_AREA_FRAC = float(os.environ.get("VISION_TUBE_MIN_AREA_FRAC", "0.00022"))
TUBE_MIN_W_OVER_H = 0.50
TUBE_MAX_W_OVER_H = 3.8
TUBE_DUP_IOU = 0.30
TUBE_NEAR_PERSON_DIST = 0.10
TUBE_FOAM_REJECT_FRAC = 0.65  # 튜브 foam은 더 느슨 (흰 파도만)
# 저확신( conf < 이 값 ) 튜브만 색/허공 가드 적용 — 고확신 실튜브 회수 유지
TUBE_SOFT_GUARD_CONF = 0.40
TUBE_COLOR_FRAC = 0.12
# 물가 튜브는 박스 중심이 WATER_Y_BOT(0.78)보다 아래(모래쪽)로 살짝 내려옴 → 상한을 넓힘
TUBE_Y_BOT = float(os.environ.get("VISION_TUBE_Y_BOT", "0.90"))
# 정확도 맥스 설정
YOLO_IMGSZ = 1280
YOLO_CONF = PERSON_PROPOSAL_CONF
DETECT_UPSCALE = 2.0
DEFAULT_DETECTOR = "both"  # light(빠른 갱신) ∪ SAHI-256(정밀) 합집합
# both 모드: SAHI 재추론 간격(초). light는 매 사이클, SAHI는 이 주기마다
HYBRID_SAHI_EVERY_SEC = float(os.environ.get("VISION_HYBRID_SAHI_EVERY", "45"))
# 정확도 최우선 모드(시간 희생 OK): 멀티스케일+밴드크롭+SAHI 합집합+군중앙상블
ACCURACY_MAX = os.environ.get("VISION_MAXACC", "1").strip() not in (
    "0", "false", "",
)
if ACCURACY_MAX:
    # 회수↑ 하되 허공 오탐을 키우지 않도록 문턱은 보수적으로 유지
    if "VISION_PERSON_MIN_CONF" not in os.environ:
        PERSON_MIN_CONF = 0.24
    if "VISION_FAR_SWIMMER_MIN_CONF" not in os.environ:
        FAR_SWIMMER_MIN_CONF = 0.28
    if "VISION_FAR_PERSON_MIN_CONF" not in os.environ:
        FAR_PERSON_MIN_CONF = 0.35
    if "VISION_SWIMMER_MIN_CONF" not in os.environ:
        SWIMMER_MIN_CONF = 0.20
    if "VISION_BEACH_STAND_MIN_CONF" not in os.environ:
        BEACH_STAND_MIN_CONF = 0.10
    # 튜브는 ACCURACY_MAX에서도 올리지 않음 — 파도 FP가 더 치명적
    if "VISION_TUBE_MIN_CONF" not in os.environ:
        TUBE_MIN_CONF = 0.28
    FOAM_REJECT_FRAC = 0.40
    FOAM_SOFT_FRAC = 0.18
    SWIMMER_MAX_H_FRAC = 0.070
    BEACH_STAND_MIN_H_OVER_W = 0.50
# 사람 회수는 기본(ACCURACY_MAX) 유지. 파도→튜브만 별도 강하게 차단.
STRICT_FP = os.environ.get("VISION_STRICT_FP", "0").strip() not in (
    "0", "false", "",
)
# 정밀도 게이트(배경차+거품+시간): 구글/논문 권장 — 고정캠 FP 억제. 끄기: VISION_PRECISION=0
PRECISION_ENABLED = os.environ.get("VISION_PRECISION", "1").strip() not in (
    "0", "false", "",
)
# 물 구역 탐지는 N프레임 이상 지속될 때만 채택 (파도·반사 간헐성 — PLOS One ASV)
WATER_TEMPORAL_HITS = int(os.environ.get("VISION_WATER_HITS", "3"))
# PRECISION 시 SAHI 보조는 FP↑ → 기본 OFF (VISION_PRECISION_SAHI=1 로 복구)
PRECISION_SAHI = os.environ.get("VISION_PRECISION_SAHI", "0").strip() in (
    "1", "true", "yes",
)
WAVE_AS_TUBE_FOAM = float(os.environ.get("VISION_WAVE_TUBE_FOAM", "0.16"))
WAVE_AS_TUBE_COLOR_MAX = float(os.environ.get("VISION_WAVE_TUBE_COLOR", "0.22"))
# 튜브 최소 색 비율 — 이보다 낮으면 파도로 기각 (주황/분홍 튜브만 통과)
TUBE_REQUIRE_COLOR = float(os.environ.get("VISION_TUBE_REQUIRE_COLOR", "0.22"))
# 사람 없는 단독 튜브: 고확신+강한 색만 (파도 단독 박스 차단)
TUBE_ALONE_MIN_CONF = float(os.environ.get("VISION_TUBE_ALONE_CONF", "0.45"))
TUBE_ALONE_MIN_COLOR = float(os.environ.get("VISION_TUBE_ALONE_COLOR", "0.30"))
MAXACC_EXTRA_MIN_CONF = float(os.environ.get("VISION_MAXACC_EXTRA_CONF", "0.32"))
MAXACC_EXTRA_HIGH_CONF = float(os.environ.get("VISION_MAXACC_EXTRA_HIGH", "0.48"))
USE_TEACHER_EXTRA = os.environ.get(
    "VISION_TEACHER", "0" if PRECISION_ENABLED else "1"
).strip() not in ("0", "false", "")
PRECISION_GATE = WaterPrecisionGate(
    GateConfig(enabled=PRECISION_ENABLED)
)
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
# Colab 산출물 우선: models/yolo26s_beach_ft.pt → 없으면 models/best.pt
FAST_MODEL_BEST = "models/best.pt"
FAST_MODEL_DEPLOY = "models/yolo26s_beach_ft.pt"
FAST_SAHI_MODEL_CANDIDATES = (
    FAST_MODEL_DEPLOY,
    FAST_MODEL_BEST,
    "yolo26s.pt",
    "models/yolov8m_beach_ft.pt",
    "yolov8m.pt",
)
# ultralytics가 자동 다운로드하는 기본 이름 (로컬 파일이 없을 때 사용)
FAST_SAHI_DOWNLOAD_NAME = "yolo26s.pt"
# FAST=SAHI: 모니터(/stream/sahi256)와 동일 규격 (실측 정확도↑)
FAST_SAHI_UPSCALE = float(os.environ.get("VISION_SAHI_UPSCALE", "2.0"))
FAST_SAHI_SLICE = int(os.environ.get("VISION_SAHI_SLICE", "256"))
FAST_SAHI_OVERLAP = float(os.environ.get("VISION_SAHI_OVERLAP", "0.25"))
FAST_SAHI_IMGSZ = 640
FAST_SAHI_MAX_EDGE = int(os.environ.get("VISION_SAHI_MAXEDGE", "1280"))
FAST_EVERY_SEC = float(os.environ.get("VISION_FAST_EVERY", "1.0"))
FAST_CONF = PERSON_PROPOSAL_CONF
# preview(autolabel_watch)와 동일: 단일 YOLO imgsz=1920, upscale=1.0
FAST_LIGHT_IMGSZ = int(os.environ.get("VISION_LIGHT_IMGSZ", "1920"))
FAST_LIGHT_UPSCALE = float(os.environ.get("VISION_LIGHT_UPSCALE", "1.0"))
# ACCURACY_MAX: light 멀티스케일 (upscale, imgsz)
FAST_LIGHT_SCALES = (
    (1.0, FAST_LIGHT_IMGSZ),
    (1.5, FAST_LIGHT_IMGSZ),
    (2.0, 1600),
)
FAST_LIGHT_TTA = os.environ.get("VISION_LIGHT_TTA", "1" if ACCURACY_MAX else "0").strip() in (
    "1", "true", "yes",
)
# 보조 교사(COCO 대형): best.pt 와 합집합 → 회수↑
FAST_TEACHER_EXTRA = (
    "yolo26m.pt",
    "yolov8m.pt",
    "yolo11m.pt",
)
# ── 원근 대응 밴드(고정 카메라: 화면 y = 거리) ──
# 하늘·다리(상단 WATER_Y_TOP 위)는 크롭해 추론에서 제외(공짜 속도).
# 물(멀다)=고배율로 먼 수영자 회수↑, 모래(가깝다)=저배율로 낭비↓.
# (y_top_frac, y_bot_frac, upscale, slice[, overlap])
FAST_BANDS = (
    (WATER_Y_TOP, FAR_WATER_Y, 3.2, 320, 0.28),  # 먼 바다: 머리만 수영자
    (FAR_WATER_Y, WATER_Y_BOT, 3.2, 352, 0.25),  # 입수대: 상반신·머리
    (WATER_Y_BOT, 1.0, 2.8, 384, 0.22),          # 모래: 서있는 사람
)
# ── 256×256 SAHI 비교 모니터 (FAST가 이미 SAHI면 기본 OFF — 중복 추론 방지) ──
# 비교용으로 켜기: VISION_SAHI256=1  (+ --detector light 일 때 유용)
SAHI256_ENABLED = os.environ.get("VISION_SAHI256", "0").strip() not in (
    "0", "false", "",
)
SAHI256_UPSCALE = float(os.environ.get("VISION_SAHI256_UPSCALE", "2.0"))
SAHI256_SLICE = 256
SAHI256_OVERLAP = 0.25
SAHI256_COOLDOWN_SEC = float(os.environ.get("VISION_SAHI256_COOLDOWN", "20"))
SAHI256_MAX_EDGE = int(os.environ.get("VISION_SAHI256_MAXEDGE", "1280"))
# 모니터가 INFER_LOCK을 길게 잡으면 FAST 경보가 멈춤 → 기본 락 없이 실행
# (구동작: VISION_SAHI256_LOCK=1)
SAHI256_USE_LOCK = os.environ.get("VISION_SAHI256_LOCK", "0").strip() in (
    "1", "true", "yes",
)
# ── 역할 분리 ─────────────────────────────────────────────
# 알람(FAST/both): 오탐 최소 — 구역별 엄격 (경보·격자)
# SAHI256 비교: 회수 우선 — conf 완화, 부표·보트만 강제 기각
SAHI256_RELAX_CONF = float(os.environ.get("VISION_SAHI256_RELAX_CONF", "0.20"))
SAHI256_PERSON_MIN_CONF = float(os.environ.get("VISION_SAHI256_PERSON_CONF", "0.12"))
SAHI256_TUBE_MIN_CONF = float(os.environ.get("VISION_SAHI256_TUBE_CONF", "0.05"))
SAHI256_RELAX_COLOR = float(os.environ.get("VISION_SAHI256_RELAX_COLOR", "0.18"))
# 알람 구역별 사람 conf (해변 회수↑ / 먼 바다 오탐↓)
ALARM_BEACH_PERSON_CONF = float(os.environ.get("VISION_ALARM_BEACH_CONF", "0.14"))
ALARM_WATER_PERSON_CONF = float(os.environ.get("VISION_ALARM_WATER_CONF", "0.28"))
ALARM_FAR_PERSON_CONF = float(os.environ.get("VISION_ALARM_FAR_CONF", "0.38"))
# 알람 튜브: conf↓ + 최소 크기 강제. 비교 모니터는 SAHI256_TUBE_* 사용
ALARM_TUBE_MIN_CONF = float(os.environ.get("VISION_ALARM_TUBE_CONF", "0.16"))
# 보트→튜브 방지: 큰 면적·가로로 긴 선체·흰/회색 갑판
BOAT_MIN_AREA_FRAC = float(os.environ.get("VISION_BOAT_AREA", "0.0018"))
BOAT_MIN_W_OVER_H = float(os.environ.get("VISION_BOAT_W_OVER_H", "2.6"))
BOAT_WHITE_FRAC = float(os.environ.get("VISION_BOAT_WHITE", "0.28"))
BOAT_DARK_FRAC = float(os.environ.get("VISION_BOAT_DARK", "0.35"))
# 비교 모니터 텍스처/색 가드 (0=거의 OFF — 회수 최우선, 부표·보트는 별도)
SAHI256_TEXTURE_GATE = os.environ.get("VISION_SAHI256_TEXTURE", "0").strip() not in (
    "0", "false", "",
)
SAHI256_TUBE_COLOR_GATE = os.environ.get("VISION_SAHI256_TUBE_COLOR", "0").strip() not in (
    "0", "false", "",
)
# 파인튜닝용 raw 프레임 내장 수집 (별도 collect 프로세스가 죽어도 서버만 켜져 있으면 저장)
COLLECT_RAW_ENABLED = os.environ.get("VISION_COLLECT_RAW", "1").strip() not in (
    "0", "false", "",
)
COLLECT_RAW_EVERY_SEC = float(os.environ.get("VISION_COLLECT_EVERY", "60"))
COLLECT_RAW_DIR = ROOT / "finetune" / "raw"
COLLECT_RAW_JPEG_QUALITY = 90
_COLLECT_META_LOCK = threading.Lock()
COLLECT_META: dict = {
    "enabled": COLLECT_RAW_ENABLED,
    "everySec": COLLECT_RAW_EVERY_SEC,
    "saved": 0,
    "lastPath": None,
    "lastAt": None,
    "lastError": None,
}
# raw → images/labels/preview 자동 박스 (별도 프로세스). 꺼짐: VISION_AUTOLABEL=0
AUTOLABEL_WATCH_ENABLED = os.environ.get("VISION_AUTOLABEL", "1").strip() not in (
    "0", "false", "",
)


def set_collect_meta(**kwargs):
    with _COLLECT_META_LOCK:
        COLLECT_META.update(kwargs)


def get_collect_meta() -> dict:
    with _COLLECT_META_LOCK:
        return dict(COLLECT_META)
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
# light(~5s) / maxacc 에서는 짧은 히스토리로 깜빡임↓. SAHI 초장주기일 땐 1 유지.
TEMPORAL_HISTORY = int(
    os.environ.get(
        "VISION_TEMPORAL_HIST",
        "3" if ACCURACY_MAX else "1",
    )
)
TEMPORAL_MIN_HITS = int(os.environ.get("VISION_TEMPORAL_HITS", "1"))
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
        if detector == "sahi-256":
            title = "SAHI 256 compare (X=rejected)"
        elif "light+sahi" in str(detector).lower():
            title = "FAST light+SAHI-256"
        elif "accuracy-max" in str(detector).lower():
            title = "ACCURACY-MAX ensemble"
        elif "light" in str(detector).lower():
            title = "FAST LIGHT imgsz1920"
        elif "sahi" in str(detector).lower():
            title = "FAST SAHI-256"
        else:
            title = "YOLO person-only"
        # SAHI 비교 모니터만 기각 박스 표시 (알람 FAST는 기존처럼 숨김)
        out = draw_yolo_boxes(
            raw,
            boxes,
            LIVE_ROI,
            title=title,
            rejected=rejected,
            draw_rejected=(detector == "sahi-256"),
        )
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
# 256×256 SAHI 모니터 전용 (FAST 경보와 독립)
STORE_SAHI256 = LatestFrameStore()

# CPU에서 FAST·PRECISE 동시 추론 시 PyTorch/Ultralytics가 서로 굶김 → 직렬화
INFER_LOCK = threading.Lock()
PRECISE_WANT = threading.Event()  # PRECISE/SAHI256이 락을 원할 때 FAST가 양보
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
_SAHI256_META_LOCK = threading.Lock()
SAHI256_META: dict = {
    "enabled": SAHI256_ENABLED,
    "state": "idle",  # idle | loading | running | ok | error | disabled
    "personCount": 0,
    "tubeCount": 0,
    "rejectedCount": 0,
    "inferMs": 0.0,
    "updatedAt": None,
    "lastError": None,
    "slice": SAHI256_SLICE,
    "overlap": SAHI256_OVERLAP,
    "upscale": SAHI256_UPSCALE,
}


def set_precise_meta(**kwargs):
    with _PRECISE_META_LOCK:
        PRECISE_META.update(kwargs)


def get_precise_meta() -> dict:
    with _PRECISE_META_LOCK:
        return dict(PRECISE_META)


def set_sahi256_meta(**kwargs):
    with _SAHI256_META_LOCK:
        SAHI256_META.update(kwargs)


def get_sahi256_meta() -> dict:
    with _SAHI256_META_LOCK:
        return dict(SAHI256_META)


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


# ── 프로세스 재시작 (UI 버튼) ─────────────────────────────
# Windows는 포트 점유 때문에 즉시 exec 불가 → 지연 spawn 후 현재 프로세스 종료.
SERVER_ARGV: list[str] = []
_RESTART_META_LOCK = threading.Lock()
RESTART_META: dict = {
    "state": "idle",  # idle | restarting | error
    "requestedAt": None,
    "error": None,
}


def set_restart_meta(**kwargs):
    with _RESTART_META_LOCK:
        RESTART_META.update(kwargs)


def get_restart_meta() -> dict:
    with _RESTART_META_LOCK:
        return dict(RESTART_META)


def request_server_restart() -> dict:
    """HTTP 응답 후 새 프로세스를 띄우고 현재 서버를 종료."""
    set_restart_meta(
        state="restarting",
        requestedAt=datetime.now(timezone.utc).isoformat(),
        error=None,
    )

    def _restart():
        try:
            time.sleep(0.7)  # 응답 플러시
            script = str(Path(__file__).resolve())
            argv_tail = list(SERVER_ARGV) if SERVER_ARGV else ["--detector", "both"]
            child_cmd = [sys.executable, script, *argv_tail]
            # 부모 종료 후 포트 해제될 시간을 두고 자식 기동
            launcher = (
                "import time,subprocess,os,sys;"
                "time.sleep(1.8);"
                f"subprocess.Popen({child_cmd!r}, cwd={str(ROOT)!r}, "
                "env=os.environ.copy());"
            )
            popen_kw: dict = {
                "cwd": str(ROOT),
                "env": os.environ.copy(),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                popen_kw["creationflags"] = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            subprocess.Popen([sys.executable, "-c", launcher], **popen_kw)
            eprint(f"[restart] relaunch scheduled: {' '.join(child_cmd)}")
        except Exception as exc:  # noqa: BLE001
            eprint(f"[restart] FAILED: {exc}")
            set_restart_meta(state="error", error=str(exc))
            return
        os._exit(0)

    threading.Thread(target=_restart, daemon=True, name="server-restart").start()
    return {
        "ok": True,
        "message": "서버 재시작 중… 수 초 후 자동으로 다시 연결됩니다.",
        "restart": get_restart_meta(),
    }


# ── 학습용 ZIP 패키징 (Colab: gwangalli_dataset.zip) ───────
# UI '학습용 ZIP 생성' = make_dataset.py 와 동일:
#   finetune/dataset/{images,labels} → train/val 분할 → gwangalli_dataset.zip
DATASET_ZIP = Path(__file__).resolve().parent / "finetune" / "gwangalli_dataset.zip"
_PACK_META_LOCK = threading.Lock()
PACK_META: dict = {
    "state": "idle",   # idle | packing | done | error
    "path": None,
    "sizeMB": None,
    "frames": 0,
    "train": 0,
    "val": 0,
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
    """라벨된 dataset → train/val 분할 후 gwangalli_dataset.zip 생성."""
    root = Path(__file__).resolve().parent
    ds = root / "finetune" / "dataset"
    img_dir = ds / "images"
    lbl_dir = ds / "labels"
    val_ratio = 0.2
    seed = 42
    try:
        set_pack_meta(
            state="packing",
            error=None,
            startedAt=datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            doneAt=None,
            path=None,
            sizeMB=None,
            train=0,
            val=0,
            frames=0,
        )
        if not img_dir.is_dir() or not lbl_dir.is_dir():
            set_pack_meta(
                state="error",
                error="finetune/dataset/images·labels 가 없습니다. 라벨링 후 다시 시도하세요.",
            )
            return
        images = sorted(img_dir.glob("*.jpg"))
        pairs = [
            (im, lbl_dir / (im.stem + ".txt"))
            for im in images
            if (lbl_dir / (im.stem + ".txt")).exists()
        ]
        set_pack_meta(frames=len(pairs))
        if not pairs:
            set_pack_meta(
                state="error",
                error="라벨된 이미지 쌍이 없습니다. (dataset/images + labels)",
            )
            return

        random.Random(seed).shuffle(pairs)
        n_val = max(1, int(len(pairs) * val_ratio))
        splits = {"val": pairs[:n_val], "train": pairs[n_val:]}
        set_pack_meta(train=len(splits["train"]), val=len(splits["val"]))

        for split in ("train", "val"):
            for sub in ("images", "labels"):
                d = ds / split / sub
                if d.exists():
                    shutil.rmtree(d)
                d.mkdir(parents=True, exist_ok=True)
        for split, items in splits.items():
            for im, lb in items:
                shutil.copy2(im, ds / split / "images" / im.name)
                shutil.copy2(lb, ds / split / "labels" / lb.name)

        data_yaml = (
            "path: .\n"
            "train: train/images\n"
            "val: val/images\n"
            "names:\n"
            "  0: person\n"
            "  1: tube\n"
        )
        (ds / "data.yaml").write_text(data_yaml, encoding="utf-8")

        tmp = DATASET_ZIP.with_name(DATASET_ZIP.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for split in ("train", "val"):
                for sub in ("images", "labels"):
                    for f in sorted((ds / split / sub).glob("*")):
                        z.write(f, f"{split}/{sub}/{f.name}")
            z.write(ds / "data.yaml", "data.yaml")
        tmp.replace(DATASET_ZIP)
        size = round(DATASET_ZIP.stat().st_size / (1024 * 1024), 1)
        set_pack_meta(
            state="done",
            path=str(DATASET_ZIP),
            sizeMB=size,
            frames=len(pairs),
            train=len(splits["train"]),
            val=len(splits["val"]),
            doneAt=datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        )
        eprint(
            f"[pack] ZIP 완료 → {DATASET_ZIP} ({size} MB, "
            f"train={len(splits['train'])} val={len(splits['val'])})"
        )
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
# 앙상블: DM-Count+Bay 평균. 정확도 모드(VISION_MAXACC=1)에서는 기본 ON.
CROWD_ENSEMBLE = os.environ.get(
    "VISION_CROWD_ENSEMBLE", "1" if ACCURACY_MAX else "0"
).strip() in (
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
        ROOT / FAST_MODEL_DEPLOY,
        ROOT / FAST_MODEL_BEST,
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

    def update(self, boxes: list, frame_hw=None) -> list:
        """물 구역은 여러 프레임 지속될 때만 채택(파도·반사 FP↓)."""
        self.buf.append(list(boxes))
        if len(self.buf) == 1:
            return list(boxes)
        pooled: list = []
        for frame_boxes in self.buf:
            pooled.extend(frame_boxes)
        merged = nms_boxes(pooled, iou_thresh=max(0.4, SAHI_NMS_IOU))
        base_need = min(self.min_hits, len(self.buf))
        kept = []
        h = float(frame_hw[0]) if frame_hw is not None else None
        for b in merged:
            hits = 0
            for frame_boxes in self.buf:
                if any(_box_iou(b, x) >= self.iou_thresh for x in frame_boxes):
                    hits += 1
            need = base_need
            if h is not None and PRECISION_ENABLED:
                cy = (float(b[1]) + float(b[3])) * 0.5 / h
                if WATER_Y_TOP <= cy < WATER_Y_BOT:
                    need = max(need, min(WATER_TEMPORAL_HITS, len(self.buf)))
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


def _looks_like_foam(box, frame_bgr, force: bool = False) -> bool:
    """물 구역 후보가 파도 거품인지 색상 중심으로 판정.

    주의: Canny 에지 단독 기각은 수영자 실루엣(물 대비 윤곽)까지 죽여서 쓰지 않는다.
    에지는 '어느 정도 거품색이 있을 때'만 보조 신호로 쓴다.
    force=True(STRICT)면 고확신도 foam이면 기각.
    """
    if frame_bgr is None:
        return False
    score = float(box[4])
    if not force and score >= FOAM_OVERRIDE_CONF:
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
    # 바다 청록과 구분: 주황·노랑·분홍·선명파랑만 (청록 sea 제외)
    orange = (hh >= 5) & (hh <= 28) & (ss >= 0.40) & (vv >= 0.45)
    yellow = (hh >= 22) & (hh <= 38) & (ss >= 0.45) & (vv >= 0.50)
    blue = (hh >= 100) & (hh <= 120) & (ss >= 0.55) & (vv >= 0.45) & (vv <= 0.95)
    pink = ((hh >= 145) & (hh <= 175) & (ss >= 0.35) & (vv >= 0.45)) | (
        (hh <= 6) & (ss >= 0.40) & (vv >= 0.45)
    )
    return float(np.mean(orange | yellow | blue | pink))


def _sea_blue_fraction(frame_bgr: np.ndarray, box) -> float:
    """박스 내 바다색(청록·남색·회색바다) 비율 — 허공·파도 판별용."""
    hsv = _box_hsv_patch(frame_bgr, box)
    if hsv is None:
        return 0.0
    hh = hsv[..., 0]
    ss = hsv[..., 1].astype(np.float32) / 255.0
    vv = hsv[..., 2].astype(np.float32) / 255.0
    blue = (hh >= 85) & (hh <= 140) & (ss >= 0.12) & (vv >= 0.18) & (vv <= 0.92)
    gray_sea = (ss <= 0.22) & (vv >= 0.25) & (vv <= 0.85) & (hh >= 70) & (hh <= 150)
    return float(np.mean(blue | gray_sea))


def _looks_like_empty_water(box, frame_hw, frame_bgr) -> bool:
    """열린 바다 허공(저분산·무색·바다색). 튜브·사람 공통 가드."""
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
    std = float(patch.std())
    tube_c = _tube_color_fraction(frame_bgr, box)
    sea = _sea_blue_fraction(frame_bgr, box)
    foam = _foam_fraction(frame_bgr, box)
    # 밋밋 + 튜브색 없음
    if std < 30.0 and tube_c < TUBE_COLOR_FRAC:
        return True
    # 대부분 바다색 + 낮은 대비 = 허공 (STRICT 없이도)
    if sea >= EMPTY_SEA_FRAC and std < EMPTY_STD_MAX and tube_c < 0.10:
        return True
    if sea >= 0.70 and std < 45.0:
        return True
    # 바다색 + 거품 위주 (파도 마루를 사람으로 오인)
    if sea >= 0.45 and foam >= 0.28 and std < 42.0 and tube_c < 0.12:
        return True
    return False


def _looks_like_empty_false_positive(box, frame_hw, frame_bgr) -> bool:
    """허공·파도·부표 오탐 가드 (물 구역 — 보조패스·본경로 공통)."""
    if frame_bgr is None or frame_hw is None:
        return False
    score = float(box[4])
    h, w = frame_hw
    cy = _box_cy_frac(box, h)
    # 하늘·다리 위는 무조건 기각
    if cy < WATER_Y_TOP:
        return True
    if cy >= WATER_Y_BOT:
        return False
    if _looks_like_foam(box, frame_bgr, force=score < 0.62):
        return True
    if _looks_like_safety_buoy(box, frame_hw, frame_bgr):
        return True
    sea = _sea_blue_fraction(frame_bgr, box)
    if _looks_like_empty_water(box, frame_hw, frame_bgr):
        # 허공 판정이면 거의 전부 기각 (실수영자는 대비·실루엣으로 empty 아님)
        if score < 0.62 or sea >= 0.50:
            return True
    x1 = max(0, int(round(box[0])))
    y1 = max(0, int(round(box[1])))
    x2 = min(w, int(round(box[2])))
    y2 = min(h, int(round(box[3])))
    if x2 - x1 >= 2 and y2 - y1 >= 2:
        patch = frame_bgr[y1:y2, x1:x2]
        std = float(patch.std())
        if std < 34.0 and score < 0.48:
            return True
        if sea >= WATER_PERSON_SEA_MAX and score < WATER_PERSON_SEA_CONF:
            return True
        if sea >= 0.58 and std < 40.0 and score < 0.50:
            return True
        if sea >= 0.72 and std < 45.0:
            return True
    return False


def _near_any_box_xy(box, others: list, iou_thr: float = 0.25) -> bool:
    return any(_box_iou(box, o) >= iou_thr for o in others)


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
        # 먼 바다 작은 박스 = 부표일 수도, 머리만 내민 수영자일 수도 있음.
        # 크기만으로 전부 기각하면 수영자 회수가 붕괴 → 부표색이 있을 때만 기각.
        small = area_frac <= FAR_MAX_AREA_FRAC or bh <= FAR_MAX_BOX_H_FRAC * h
        if not small:
            return False
        if frame_bgr is None:
            return True
        return bool(colored)

    # 가까운 입수대·물가: 점 크기만 무조건 부표, 그 이상은 색이 맞을 때만
    if area_frac <= NEAR_BUOY_MAX_AREA_FRAC or bh <= NEAR_BUOY_MAX_H_FRAC * h:
        return True
    if colored and area_frac <= NEAR_BUOY_COLOR_AREA:
        return True
    return False


def _looks_like_tube_as_buoy(box, frame_hw, frame_bgr=None) -> bool:
    """튜브 후보가 안전 부표인지 — '물 위 + 작은 점' (+ 먼 바다 부표띠).

    실튜브는 입수대·중간 이상 크기. 부표는 먼 바다(y≈0.45~0.55)에 작은 점으로 가로 배열.
    """
    if frame_hw is None:
        return False
    h, w = frame_hw
    cy = _box_cy_frac(box, h)
    # 물 구역만
    if not (WATER_Y_TOP <= cy < WATER_Y_BOT):
        return False
    bw = max(1.0, float(box[2]) - float(box[0]))
    bh = max(1.0, float(box[3]) - float(box[1]))
    area_frac = (bw * bh) / float(max(1, h * w))
    buoy_c = (
        _buoy_color_fraction(frame_bgr, box) if frame_bgr is not None else 0.0
    )
    far = cy < FAR_WATER_Y
    # ── 먼 바다 부표 띠: 작은 것은 전부 부표로 (실튜브는 거의 여기 안 옴) ──
    if far:
        small_far = (
            area_frac <= FAR_MAX_AREA_FRAC * 3.0
            or bh <= FAR_MAX_BOX_H_FRAC * 1.8 * h
        )
        if small_far:
            return True
        # 조금 커도 노랑·주황 뚜렷하면 부표
        if buoy_c >= 0.18 and area_frac <= FAR_MAX_AREA_FRAC * 5.0:
            return True
        return False
    # ── 입수대: 진짜 점만 (+ 노랑) ──
    tiny_near = (
        area_frac <= NEAR_BUOY_MAX_AREA_FRAC * 2.5
        or bh <= NEAR_BUOY_MAX_H_FRAC * 1.25 * h
    )
    if tiny_near and (buoy_c >= 0.10 or area_frac <= NEAR_BUOY_MAX_AREA_FRAC):
        return True
    return False


def refine_alarm_boxes(
    boxes: list, frame_bgr, frame_hw
) -> tuple[list, list]:
    """알람용 정밀 재필터 — 회수(light∪SAHI) 후 오탐만 한 번 더 자름.

    해변 사람은 유지, 물·먼 바다·부표·보트는 엄격.
    """
    if not boxes:
        return [], []
    persons, tubes = _split_by_class(boxes)
    persons_ok, rej_p = split_person_candidates(
        persons, frame_hw=frame_hw, frame_bgr=frame_bgr, relax=False
    )
    tubes_ok = filter_tubes(
        tubes, frame_hw, persons_ok, frame_bgr=frame_bgr, relax=False
    )
    tubes_ok = [
        t for t in tubes_ok
        if not _looks_like_boat(t, frame_hw, frame_bgr)
        and not _looks_like_tube_as_buoy(t, frame_hw, frame_bgr)
    ]
    tubes_ok = _reject_buoy_line_tubes(tubes_ok, frame_hw)
    # cls 유지: 0=person, 1=tube — filter_tubes는 이미 6튜플일 수 있어 [:5]만 사용
    kept = [(*tuple(p[:5]), 0) for p in persons_ok] + [
        (*tuple(t[:5]), 1) for t in tubes_ok
    ]
    return kept, list(rej_p)


def _reject_buoy_line_tubes(tubes: list, frame_hw) -> list:
    """같은 높이(y)에 작은 튜브가 3개 이상 가로로 늘면 부표줄로 보고 제거."""
    if not tubes or frame_hw is None or len(tubes) < 3:
        return tubes
    h, w = frame_hw
    # (index, cy, area_frac, box)
    meta = []
    for i, b in enumerate(tubes):
        bw = max(1.0, float(b[2]) - float(b[0]))
        bh = max(1.0, float(b[3]) - float(b[1]))
        area_frac = (bw * bh) / float(max(1, h * w))
        cy = _box_cy_frac(b, h)
        meta.append((i, cy, area_frac, b))
    drop = set()
    # y 밴드 폭 ~2% 안에 작은 박스 군집
    for i, cy_i, af_i, _ in meta:
        if af_i > FAR_MAX_AREA_FRAC * 4.0:
            continue
        peers = [
            j
            for j, cy_j, af_j, _ in meta
            if j != i
            and af_j <= FAR_MAX_AREA_FRAC * 4.0
            and abs(cy_i - cy_j) <= 0.025
        ]
        if len(peers) >= 2:  # 본인+2 = 줄 3개
            drop.add(i)
            drop.update(peers)
    if not drop:
        return tubes
    return [b for i, b in enumerate(tubes) if i not in drop]


def _boat_hull_fraction(frame_bgr: np.ndarray, box) -> tuple[float, float]:
    """박스 내 (흰/밝은 갑판, 어두운 선체) 비율."""
    hsv = _box_hsv_patch(frame_bgr, box)
    if hsv is None:
        return 0.0, 0.0
    ss = hsv[..., 1].astype(np.float32) / 255.0
    vv = hsv[..., 2].astype(np.float32) / 255.0
    white = (ss <= 0.28) & (vv >= 0.55)
    dark = (vv <= 0.35) & (ss <= 0.45)
    return float(np.mean(white)), float(np.mean(dark))


def _looks_like_boat(box, frame_hw, frame_bgr=None) -> bool:
    """튜브 후보가 보트/선박인지 — 큰 면적·가로로 긴 선체·흰/어두운 갑판.

    실튜브는 작고 채도 높은 주황/분홍. 보트는 훨씬 크고 길다.
    """
    if frame_hw is None:
        return False
    h, w = frame_hw
    cy = _box_cy_frac(box, h)
    if not (WATER_Y_TOP <= cy <= TUBE_Y_BOT):
        return False
    bw = max(1.0, float(box[2]) - float(box[0]))
    bh = max(1.0, float(box[3]) - float(box[1]))
    area_frac = (bw * bh) / float(max(1, h * w))
    ratio = bw / bh
    tube_c = (
        _tube_color_fraction(frame_bgr, box) if frame_bgr is not None else 0.0
    )
    # 뚜렷한 튜브색이면 보트로 보지 않음 (주황 튜브 보호)
    if tube_c >= 0.22:
        return False
    white_f, dark_f = (0.0, 0.0)
    if frame_bgr is not None:
        white_f, dark_f = _boat_hull_fraction(frame_bgr, box)
    large = area_frac >= BOAT_MIN_AREA_FRAC
    very_large = area_frac >= BOAT_MIN_AREA_FRAC * 2.2
    long_hull = ratio >= BOAT_MIN_W_OVER_H
    hull_color = white_f >= BOAT_WHITE_FRAC or dark_f >= BOAT_DARK_FRAC
    # 큰데 가로로 김 → 보트
    if large and long_hull:
        return True
    # 아주 큼 + (흰/어두운 선체 | 튜브색 거의 없음)
    if very_large and (hull_color or tube_c < 0.10):
        return True
    # 중간 이상 + 매우 긴 선체 + 갑판색
    if area_frac >= BOAT_MIN_AREA_FRAC * 0.7 and ratio >= 3.2 and hull_color:
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


def _water_person_texture_ok(
    box, frame_hw, frame_bgr, score: float, relax: bool = False
) -> bool:
    """물 구역 사람: 허공·파도·밋밋한 수면이면 False."""
    if frame_bgr is None or frame_hw is None:
        return True
    # SAHI256 비교: 텍스처 가드 기본 OFF → conf만으로 거의 통과
    if relax and not SAHI256_TEXTURE_GATE:
        return True
    pmin = SAHI256_PERSON_MIN_CONF if relax else 0.0
    if _looks_like_empty_false_positive(box, frame_hw, frame_bgr):
        if not (relax and score >= pmin):
            return False
        sea0 = _sea_blue_fraction(frame_bgr, box)
        if sea0 >= (0.82 if relax else 0.72):
            return False
    foam_cut = 0.80 if relax else 0.65
    if _looks_like_foam(box, frame_bgr, force=score < foam_cut):
        return False
    sea = _sea_blue_fraction(frame_bgr, box)
    x1 = max(0, int(round(box[0])))
    y1 = max(0, int(round(box[1])))
    x2 = min(frame_bgr.shape[1], int(round(box[2])))
    y2 = min(frame_bgr.shape[0], int(round(box[3])))
    std = 999.0
    if x2 - x1 >= 2 and y2 - y1 >= 2:
        std = float(frame_bgr[y1:y2, x1:x2].std())
    sea_hard = 0.85 if relax else 0.70
    if sea >= sea_hard and std < (50.0 if relax else 42.0):
        return False
    if not relax:
        if sea >= 0.58 and std < 38.0 and score < 0.48:
            return False
        if sea >= 0.50 and std < 30.0 and score < 0.38:
            return False
    return True


def is_confident_person_box(
    box, frame_hw=None, frame_bgr=None, relax: bool = False
) -> bool:
    """사람으로 '확실히' 인정할지 구역(물/모래) 인지로 판정.

    - 모래: 서있는 사람 문턱을 낮춰 회수↑, 파라솔 캐노피는 색·가로비로 기각
    - 물: 수영자/가로형 + 거품·허공 검사, 먼 바다 부표 억제
    - relax=True: SAHI256 비교 모니터용 (기각 문턱 소폭↓)
    """
    x1, y1, x2, y2, score = box[0], box[1], box[2], box[3], box[4]
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
            return False  # 점·부표급 초소형
        if area / frame_area > PERSON_MAX_AREA_FRAC:
            return False
        if bw > PERSON_MAX_WIDTH_FRAC * w:
            return False
        if (not relax) and _looks_like_safety_buoy(box, frame_hw, frame_bgr):
            return False
        # 중심 기준으로 구역 판정 (바닥만 물에 걸친 허공 박스 방지)
        cy = _box_cy_frac(box, h)
        y_bot = float(y2) / float(max(1, h))
        if cy < WATER_Y_TOP:
            return False  # 하늘·다리
        if WATER_Y_TOP <= cy < WATER_Y_BOT:
            zone = "water"
        elif y_bot >= WATER_Y_BOT:
            zone = "beach"
            if (not relax) and _looks_like_parasol(box, frame_hw, frame_bgr):
                return False
        else:
            zone = "water"  # 경계 모호 → 물 가드 적용(허공 FP 방지)
    w_over_h = (bw / bh) if bh > 1e-6 else 99.0
    h_over_w = (bh / bw) if bw > 1e-6 else 99.0

    if zone == "beach":
        # 알람: 해변 회수 유지 / 비교: SAHI256 완화
        beach_need = (
            SAHI256_PERSON_MIN_CONF if relax else ALARM_BEACH_PERSON_CONF
        )
        sit_need = (
            SAHI256_PERSON_MIN_CONF if relax else max(0.08, ALARM_BEACH_PERSON_CONF)
        )
        if (
            score >= beach_need
            and w_over_h <= BEACH_STAND_MAX_W_OVER_H
            and h_over_w >= BEACH_STAND_MIN_H_OVER_W
        ):
            return True
        if (
            score >= sit_need
            and h_over_w >= BEACH_SIT_MIN_H_OVER_W
            and w_over_h <= BEACH_SIT_MAX_W_OVER_H
        ):
            return True
        return False

    # 물 구역: 허공·파도 가드 필수
    if not _water_person_texture_ok(
        box, frame_hw, frame_bgr, score, relax=relax
    ):
        return False

    if relax:
        # 비교 모니터: 회수 우선 (부표는 사람 경로에서 크기·색으로만)
        need = SAHI256_PERSON_MIN_CONF
        need_sw = SAHI256_PERSON_MIN_CONF
        float_need = SAHI256_PERSON_MIN_CONF
    else:
        # 알람: 구역별 — 먼 바다 엄격 / 입수대 중간
        if _in_far_water(box, frame_hw):
            need = ALARM_FAR_PERSON_CONF
            need_sw = max(ALARM_FAR_PERSON_CONF - 0.04, FAR_SWIMMER_MIN_CONF)
        else:
            need = ALARM_WATER_PERSON_CONF
            need_sw = max(0.16, SWIMMER_MIN_CONF)
        float_need = FLOAT_MIN_CONF

    aspect_h_min = 0.40 if relax else PERSON_MIN_ASPECT_H_OVER_W
    if (
        score >= need
        and w_over_h <= (2.4 if relax else PERSON_MAX_ASPECT_W_OVER_H)
        and h_over_w >= aspect_h_min
    ):
        return True

    is_small = frame_hw is not None and bh <= SWIMMER_MAX_H_FRAC * frame_hw[0]
    if is_small and w_over_h <= (2.2 if relax else SWIMMER_MAX_W_OVER_H):
        if score < need_sw:
            return False
        if (not relax) and _in_far_water(box, frame_hw) and frame_bgr is not None:
            if _buoy_color_fraction(frame_bgr, box) >= BUOY_COLOR_FRAC:
                return False
            if _sea_blue_fraction(frame_bgr, box) >= 0.48 and score < 0.45:
                return False
        return True
    if score >= float_need and w_over_h <= (3.0 if relax else FLOAT_MAX_W_OVER_H):
        return True
    # relax: conf만 넘으면 형태 느슨하게 통과
    if relax and score >= SAHI256_PERSON_MIN_CONF and w_over_h <= 3.2:
        return True
    return False


def split_person_candidates(
    boxes: list, frame_hw=None, frame_bgr=None, relax: bool = False
):
    """확정 사람 / 기각(낮은 확신·비정상 박스)으로 분리."""
    confirmed = []
    rejected = []
    for b in boxes:
        if is_confident_person_box(
            b, frame_hw=frame_hw, frame_bgr=frame_bgr, relax=relax
        ):
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
        return boxes_to_centers(boxes), boxes, []
    confirmed, rejected = split_person_candidates(
        boxes, frame_hw=(h, w), frame_bgr=clean
    )
    tubes_ok = filter_tubes(tube_boxes, (h, w), confirmed, frame_bgr=clean)
    # person에도 cls=0 부여 (NMS·집계·표시가 tube와 동일 포맷)
    out_boxes = [(*tuple(p[:5]), 0) for p in confirmed] + [
        (*tuple(t[:5]), 1) for t in tubes_ok
    ]
    PRECISION_GATE.update_background(clean)
    out_boxes, gate_rej = PRECISION_GATE.filter_boxes(out_boxes, clean)
    confirmed = [b for b in out_boxes if not (len(b) >= 6 and int(b[5]) == 1)]
    tubes_ok = [b for b in out_boxes if len(b) >= 6 and int(b[5]) == 1]
    centers = boxes_to_centers(confirmed + _tubes_for_count(tubes_ok, confirmed))
    return centers, out_boxes, list(rejected) + list(gate_rej)


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
        _c, boxes, _rej = detect_people_fast(
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


def _detect_yolo_band(
    model: YOLO,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    y0f: float,
    y1f: float,
    upscale: float,
    imgsz: int,
    conf: float,
    device: str,
) -> list:
    """세로 밴드 크롭→확대→YOLO → 원본 좌표 박스(필터 전, cls 포함)."""
    h, w = frame_bgr.shape[:2]
    y0, y1 = int(y0f * h), int(y1f * h)
    if y1 - y0 < 8:
        return []
    band = frame_bgr[y0:y1, :]
    band_mask = roi_mask[y0:y1, :]
    big = cv2.resize(
        band,
        (max(1, int(band.shape[1] * upscale)), max(1, int(band.shape[0] * upscale))),
        interpolation=cv2.INTER_CUBIC,
    )
    big_mask = cv2.resize(
        band_mask, (big.shape[1], big.shape[0]), interpolation=cv2.INTER_NEAREST
    )
    _c, boxes, _r = detect_people_fast(
        model,
        big,
        big_mask,
        conf=conf,
        imgsz=imgsz,
        upscale=1.0,
        confirm=True,
        device=device,
    )
    out = []
    for b in boxes:
        x1, y1b, x2, y2b = b[0], b[1], b[2], b[3]
        score = float(b[4])
        cls = int(b[5]) if len(b) >= 6 else 0
        out.append(
            (
                x1 / upscale,
                y1b / upscale + y0,
                x2 / upscale,
                y2b / upscale + y0,
                score,
                cls,
            )
        )
    return out


def detect_people_accuracy_max(
    primary: YOLO,
    teachers: list,
    sahi_model: AutoDetectionModel | None,
    frame_bgr: np.ndarray,
    roi_mask: np.ndarray,
    conf: float = FAST_CONF,
    device: str | None = None,
) -> tuple[list, list, list]:
    """정확도 최우선 탐지 — 코어(light) + 검증된 보조 패스만 합집합.

    허공 오탐 방지:
      - 코어 = primary 1.0×1920 (+선택 TTA) 결과만 무조건 채택
      - 밴드/교사/SAHI 추가는 (코어와 겹침) 또는 (고확신) 또는
        (conf≥EXTRA_MIN 이고 거품·허공·부표 가드 통과) 일 때만 추가
    """
    if device is None:
        device = resolve_device()
    h, w = frame_bgr.shape[:2]
    frame_hw = (h, w)

    # ── 코어: preview와 동일한 light 1장 (+TTA 1장) ──
    core: list = []
    _c, boxes, _rej = detect_people_fast(
        primary,
        frame_bgr,
        roi_mask,
        conf=conf,
        imgsz=FAST_LIGHT_IMGSZ,
        upscale=1.0,
        use_tta=False,
        confirm=True,
        device=device,
    )
    core.extend(boxes)
    if FAST_LIGHT_TTA:
        _c, boxes, _rej = detect_people_fast(
            primary,
            frame_bgr,
            roi_mask,
            conf=conf,
            imgsz=FAST_LIGHT_IMGSZ,
            upscale=1.0,
            use_tta=True,
            confirm=True,
            device=device,
        )
        core.extend(boxes)
    # 코어 스케일 1.5 (중간 거리) — 코어로 인정하되 이후 허공 가드 적용
    _c, boxes, _rej = detect_people_fast(
        primary,
        frame_bgr,
        roi_mask,
        conf=conf,
        imgsz=FAST_LIGHT_IMGSZ,
        upscale=1.5,
        confirm=True,
        device=device,
    )
    core.extend(boxes)
    core = nms_boxes(core, iou_thresh=0.45)
    core_persons = [b for b in core if not (len(b) >= 6 and int(b[5]) == 1)]

    extras: list = []

    def _accept_extra(b) -> bool:
        score = float(b[4])
        if _near_any_box_xy(b, core_persons, iou_thr=0.25):
            return not _looks_like_empty_false_positive(b, frame_hw, frame_bgr)
        if score >= MAXACC_EXTRA_HIGH_CONF:
            return not _looks_like_foam(b, frame_bgr, force=False)
        if score < MAXACC_EXTRA_MIN_CONF:
            return False
        if _looks_like_empty_false_positive(b, frame_hw, frame_bgr):
            return False
        if len(b) >= 6 and int(b[5]) == 1:
            # 밴드 확대 튜브는 파도 FP↑ — 코어 사람 근처·고확신만
            if score < 0.42:
                return False
            return _near_any_box_xy(b, core_persons, iou_thr=0.20) or score >= 0.55
        return is_confident_person_box(b, frame_hw=frame_hw, frame_bgr=frame_bgr)

    # ── 보조: 물/모래 밴드 (primary만). 튜브는 밴드에서 제외(파도 FP) ──
    for band_boxes in (
        _detect_yolo_band(
            primary, frame_bgr, roi_mask,
            WATER_Y_TOP, WATER_Y_BOT, 3.0, FAST_LIGHT_IMGSZ, max(conf, 0.18), device,
        ),
        _detect_yolo_band(
            primary, frame_bgr, roi_mask,
            WATER_Y_BOT, 0.98, 2.4, FAST_LIGHT_IMGSZ, max(conf, 0.12), device,
        ),
        # 먼 바다 밴드: 확대↑ → 허공 사람 FP↑ — conf 상향
        _detect_yolo_band(
            primary, frame_bgr, roi_mask,
            WATER_Y_TOP, FAR_WATER_Y, 3.6, FAST_LIGHT_IMGSZ, max(conf, 0.28), device,
        ),
    ):
        for b in band_boxes:
            if len(b) >= 6 and int(b[5]) == 1:
                continue  # 물 밴드 튜브 = 파도 오탐 주원인
            # 물 밴드 사람은 허공 가드 통과 + 코어 근처/고확신만
            if _looks_like_empty_false_positive(b, frame_hw, frame_bgr):
                continue
            if _accept_extra(b):
                extras.append(b)

    # ── 보조: 교사 전체 프레임만 (물 밴드 금지) ──
    for tmodel in teachers:
        _c, boxes, _rej = detect_people_fast(
            tmodel,
            frame_bgr,
            roi_mask,
            conf=max(conf, 0.15),
            imgsz=FAST_LIGHT_IMGSZ,
            upscale=1.0,
            confirm=True,
            device=device,
        )
        for b in boxes:
            if float(b[4]) < MAXACC_EXTRA_MIN_CONF:
                continue
            if _accept_extra(b):
                extras.append(b)

    # ── 보조: SAHI 밴드 (PRECISION 기본 OFF — 타일 FP↑) ──
    if sahi_model is not None and (PRECISION_SAHI or not PRECISION_ENABLED):
        _c, boxes, _rej = detect_people_sahi_fast(sahi_model, frame_bgr, roi_mask)
        for b in boxes:
            if _accept_extra(b):
                extras.append(b)

    pooled = nms_boxes(core + extras, iou_thresh=0.45)
    persons, tubes = _split_by_class(pooled)
    persons_final, persons_rej = split_person_candidates(
        persons, frame_hw=frame_hw, frame_bgr=frame_bgr
    )
    tubes_ok = filter_tubes(tubes, frame_hw, persons_final, frame_bgr=frame_bgr)
    tubes_tagged = [(*tuple(t[:5]), 1) for t in tubes_ok]
    persons_tagged = [(*tuple(p[:5]), 0) for p in persons_final]
    final = nms_boxes(persons_tagged + tubes_tagged, iou_thresh=0.45)
    # 정밀도 게이트: 배경차+거품 (고정캠 BEM 계열 / 수면 HSV)
    PRECISION_GATE.update_background(frame_bgr)
    final, gate_rej = PRECISION_GATE.filter_boxes(final, frame_bgr)
    persons_final = [b for b in final if not (len(b) >= 6 and int(b[5]) == 1)]
    tubes_ok = [b for b in final if len(b) >= 6 and int(b[5]) == 1]
    centers = boxes_to_centers(
        persons_final + _tubes_for_count(tubes_ok, persons_final)
    )
    rejected = list(persons_rej) + list(gate_rej)
    return centers, final, rejected


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


def filter_tubes(
    tubes: list,
    frame_hw,
    confirmed_persons: list,
    frame_bgr=None,
    relax: bool = False,
) -> list:
    """튜브 확정. 파도(흰 거품·무색)는 강하게 기각, 색 있는 실튜브는 유지."""
    if not tubes:
        return []
    h, w = frame_hw
    rc = SAHI256_RELAX_CONF if relax else 0.0
    rcol = SAHI256_RELAX_COLOR if relax else 0.0
    if relax:
        # 비교: 회수 우선 (부표·보트 가드는 아래에서 강제)
        min_conf = SAHI256_TUBE_MIN_CONF
        req_color = 0.0 if not SAHI256_TUBE_COLOR_GATE else max(
            0.04, TUBE_REQUIRE_COLOR - rcol
        )
        alone_conf = SAHI256_TUBE_MIN_CONF
        alone_color = 0.0 if not SAHI256_TUBE_COLOR_GATE else max(
            0.08, TUBE_ALONE_MIN_COLOR - rcol
        )
    else:
        # 알람: 튜브 conf↓ (회수) · 색/단독은 소폭만 유지
        min_conf = ALARM_TUBE_MIN_CONF
        req_color = max(0.10, TUBE_REQUIRE_COLOR - 0.06)
        alone_conf = max(0.28, TUBE_ALONE_MIN_CONF - 0.10)
        alone_color = max(0.18, TUBE_ALONE_MIN_COLOR - 0.08)
    out = []
    for b in tubes:
        x1, y1, x2, y2, s = b[:5]
        if s < min_conf:
            continue
        cy = (y1 + y2) / 2.0 / max(1.0, float(h))
        if not (WATER_Y_TOP <= cy <= TUBE_Y_BOT):
            continue
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)
        area = bw * bh
        area_frac = area / float(max(1, h * w))
        # conf는 낮춰도 초소형(부표·점)은 제외
        if area < TUBE_MIN_BOX_AREA or bh < TUBE_MIN_BOX_H or area_frac < TUBE_MIN_AREA_FRAC:
            continue
        ratio = bw / bh
        # 부표→튜브 / 보트→튜브 (relax여도 적용)
        if _looks_like_tube_as_buoy(b, frame_hw, frame_bgr):
            continue
        if _looks_like_boat(b, frame_hw, frame_bgr):
            continue
        # SAHI256 비교: conf·위치·크기만 (색/거품/단독 가드 거의 OFF)
        if relax and not SAHI256_TUBE_COLOR_GATE:
            if not (0.35 <= ratio <= 4.5):
                continue
            if area > PERSON_MAX_AREA_FRAC * w * h:
                continue
            out.append((float(x1), float(y1), float(x2), float(y2), float(s), 1))
            continue
        tube_c = (
            _tube_color_fraction(frame_bgr, b) if frame_bgr is not None else 0.0
        )
        foam = _foam_fraction(frame_bgr, b) if frame_bgr is not None else 0.0
        # ── 파도→튜브 차단 (색 없는 흰 거품은 전부 기각) ──
        if frame_bgr is not None:
            if tube_c < req_color:
                continue
            if foam >= WAVE_AS_TUBE_FOAM and tube_c < (WAVE_AS_TUBE_COLOR_MAX + rcol):
                continue
            if foam >= 0.28 and tube_c < (0.28 - rcol):
                continue
            if foam >= 0.18 and tube_c < (0.20 - rcol):
                continue
            if _looks_like_empty_water(b, frame_hw, frame_bgr) and tube_c < (
                0.24 - rcol
            ):
                continue
            sea = _sea_blue_fraction(frame_bgr, b)
            if sea >= 0.40 and tube_c < (0.24 - rcol):
                continue
        if _looks_like_safety_buoy(b, frame_hw, frame_bgr):
            continue
        if s < max(0.25, TUBE_SOFT_GUARD_CONF - rc) and tube_c < (0.22 - rcol):
            continue
        area_frac = (bw * bh) / float(max(1, h * w))
        near_person = _near_any_box(
            b, confirmed_persons, frame_hw, TUBE_NEAR_PERSON_DIST
        )
        if not near_person and (s < alone_conf or tube_c < alone_color):
            continue
        if (
            cy >= FAR_WATER_Y
            and area_frac < NEAR_TUBE_MIN_AREA_FRAC
            and not near_person
            and tube_c < (0.30 - rcol)
        ):
            continue
        if cy < FAR_WATER_Y:
            if s < max(0.30, FAR_TUBE_MIN_CONF - rc):
                continue
            if tube_c < (0.32 - rcol):
                continue
            if not near_person and not relax:
                continue
            if not near_person and tube_c < alone_color:
                continue
        if not (TUBE_MIN_W_OVER_H <= ratio <= TUBE_MAX_W_OVER_H):
            continue
        if (bw * bh) > PERSON_MAX_AREA_FRAC * w * h:
            continue
        out.append((float(x1), float(y1), float(x2), float(y2), float(s), 1))
    # 가로로 늘어선 작은 점들 = 부표줄
    out = _reject_buoy_line_tubes(out, frame_hw)
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
    bands=None,
    max_edge: int = FAST_SAHI_MAX_EDGE,
):
    """FAST 기본: 전체프레임 SAHI(256×겹침25%×2.0) — 모니터와 동일 규격.

    VISION_SAHI_BANDS=1 이면 예전 원근 밴드 모드로 폴백.
    """
    h0, w0 = frame_bgr.shape[:2]
    use_bands = os.environ.get("VISION_SAHI_BANDS", "0").strip() in (
        "1", "true", "yes",
    )
    if use_bands:
        band_list = bands if bands is not None else FAST_BANDS
        merged: list = []
        for band in band_list:
            yt, yb, up, sl = band[0], band[1], band[2], band[3]
            ov = float(band[4]) if len(band) >= 5 else overlap
            merged.extend(
                detect_people_sahi_band(
                    detection_model, frame_bgr, roi_mask,
                    yt, yb, up, sl, ov,
                )
            )
        kept = nms_boxes(merged, iou_thresh=SAHI_NMS_IOU)
    else:
        # sahi256과 동일: 긴변 제한 → 전체 SAHI → 원본 좌표
        infer_frame, r = fit_long_edge(frame_bgr, max_edge)
        ih, iw = infer_frame.shape[:2]
        infer_mask, _ = make_live_roi_mask(ih, iw)
        raw_boxes = detect_people_sahi(
            detection_model,
            infer_frame,
            infer_mask,
            upscale=upscale,
            slice_size=slice_size,
            overlap=overlap,
        )
        inv = 1.0 / r if r != 1.0 else 1.0
        kept = []
        for b in raw_boxes:
            x1, y1, x2, y2, score = b[0], b[1], b[2], b[3], b[4]
            cls = int(b[5]) if len(b) >= 6 else 0
            kept.append(
                (x1 * inv, y1 * inv, x2 * inv, y2 * inv, score, cls)
            )
        kept = nms_boxes(kept, iou_thresh=SAHI_NMS_IOU)

    persons, tubes = _split_by_class(kept)
    confirmed, rejected = split_person_candidates(
        persons, frame_hw=(h0, w0), frame_bgr=frame_bgr
    )
    tubes_ok = filter_tubes(tubes, (h0, w0), confirmed, frame_bgr=frame_bgr)
    boxes = [(*tuple(p[:5]), 0) for p in confirmed] + [
        (*tuple(t[:5]), 1) for t in tubes_ok
    ]
    PRECISION_GATE.update_background(frame_bgr)
    boxes, gate_rej = PRECISION_GATE.filter_boxes(boxes, frame_bgr)
    confirmed = [b for b in boxes if not (len(b) >= 6 and int(b[5]) == 1)]
    tubes_ok = [b for b in boxes if len(b) >= 6 and int(b[5]) == 1]
    centers = boxes_to_centers(confirmed + _tubes_for_count(tubes_ok, confirmed))
    return centers, boxes, list(rejected) + list(gate_rej)


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
    draw_rejected: bool | None = None,
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

    # 기각: 기본 OFF(알람 UI 노이즈). SAHI 비교 모니터·VISION_DRAW_REJECTED=1 만 ON.
    show_rej = DRAW_REJECTED if draw_rejected is None else bool(draw_rejected)
    if show_rej:
        for r in rejected or []:
            x1, y1, x2, y2, conf = r[0], r[1], r[2], r[3], r[4]
            is_tube = len(r) >= 6 and int(r[5]) == 1
            p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
            # 기각 사람=회색, 기각 튜브=어두운 파랑
            color = (160, 100, 40) if is_tube else (140, 140, 140)
            tag = f"X tube {conf:.2f}" if is_tube else f"X {conf:.2f}"
            cv2.rectangle(out, p1, p2, color, 2)
            cv2.putText(
                out,
                tag,
                (p1[0], max(14, p1[1] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                color,
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


def _autolabel_watch_running() -> bool:
    """Windows에서 autolabel_watch.py 프로세스 존재 여부."""
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
                    "| Where-Object { $_.CommandLine -match 'autolabel_watch' } "
                    "| Select-Object -First 1 -ExpandProperty ProcessId"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return bool((r.stdout or "").strip())
    except Exception:
        return False


def ensure_autolabel_watch() -> None:
    """preview/ 갱신용 autolabel_watch 를 백그라운드로 기동(이미 있으면 스킵)."""
    if not AUTOLABEL_WATCH_ENABLED:
        eprint("[watch] 비활성 (VISION_AUTOLABEL=0) — preview 자동 갱신 안 함")
        return
    if _autolabel_watch_running():
        eprint("[watch] autolabel_watch 이미 실행 중 → dataset/preview")
        return
    script = ROOT / "finetune" / "autolabel_watch.py"
    if not script.is_file():
        eprint(f"[watch] 스크립트 없음: {script}")
        return
    out_log = ROOT / "finetune" / "autolabel_watch.out.log"
    err_log = ROOT / "finetune" / "autolabel_watch.err.log"
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        with out_log.open("a", encoding="utf-8") as fo, err_log.open(
            "a", encoding="utf-8"
        ) as fe:
            subprocess.Popen(
                [
                    sys.executable,
                    str(script),
                    "--interval",
                    "10",
                    "--upscale",
                    "1.0",
                    "--imgsz",
                    "1920",
                ],
                cwd=str(ROOT),
                stdout=fo,
                stderr=fe,
                creationflags=flags,
            )
        eprint(f"[watch] started → {ROOT / 'finetune' / 'dataset' / 'preview'}")
    except Exception as exc:  # noqa: BLE001
        eprint(f"[watch] 기동 실패: {exc}")


def collect_raw_loop(every_sec: float = COLLECT_RAW_EVERY_SEC):
    """STORE.raw 를 finetune/raw 에 주기 저장 (서버 내장 수집).

    예전엔 별도 collect_finetune_frames.py 가 /stream/raw 를 긁었는데,
    서버 재시작·PC 재부팅 후 수집기만 안 켜지면 7/31처럼 저장이 끊긴다.
    서버 프로세스에 붙여 두면 스트림이 살아있는 한 계속 모인다.
    preview/ 는 autolabel_watch 가 raw→라벨·미리보기로 변환한다.
    """
    if not COLLECT_RAW_ENABLED:
        set_collect_meta(enabled=False)
        eprint("[collect] 내장 수집 비활성 (VISION_COLLECT_RAW=0)")
        return
    COLLECT_RAW_DIR.mkdir(parents=True, exist_ok=True)
    set_collect_meta(enabled=True, everySec=every_sec)
    eprint(f"[collect] 내장 수집 every={every_sec:.0f}s → {COLLECT_RAW_DIR}")
    n = 0
    while True:
        time.sleep(max(5.0, every_sec))
        try:
            raw = STORE.get_raw_copy()
            if raw is None:
                set_collect_meta(lastError="no raw frame yet")
                continue
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = COLLECT_RAW_DIR / f"gwangalli_{ts}.jpg"
            ok, buf = cv2.imencode(
                ".jpg",
                raw,
                [int(cv2.IMWRITE_JPEG_QUALITY), COLLECT_RAW_JPEG_QUALITY],
            )
            if not ok:
                set_collect_meta(lastError="imencode failed")
                continue
            path.write_bytes(buf.tobytes())
            n += 1
            set_collect_meta(
                saved=n,
                lastPath=path.name,
                lastAt=datetime.now(timezone.utc).isoformat(),
                lastError=None,
            )
            if n == 1 or n % 10 == 0:
                eprint(f"[collect] {n} saved {path.name} ({path.stat().st_size} bytes)")
        except Exception as exc:  # noqa: BLE001
            set_collect_meta(lastError=str(exc))
            eprint(f"[collect] error: {exc}")


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

    fast_backend=both: light(자주) ∪ SAHI-256(주기적) 합집합 ← 권장
    fast_backend=sahi: SAHI-256만 (정밀, 느림)
    fast_backend=light: YOLO light(+maxacc)
    fast_backend=yolo: YOLO 멀티스케일 (호환)
    """
    device = resolve_device()
    stabilizer = TemporalPersonStabilizer(
        history=TEMPORAL_HISTORY,
        min_hits=TEMPORAL_MIN_HITS,
        iou_thresh=TEMPORAL_IOU,
    )

    if fast_backend == "both":
        light_path = resolve_fast_sahi_model()
        sahi_dev = _sahi_autodm_device()
        eprint(
            f"[fast] BOTH light∪SAHI 모델={light_path} device={device}/{sahi_dev} "
            f"light_imgsz={FAST_LIGHT_IMGSZ} "
            f"sahi slice={FAST_SAHI_SLICE} overlap={FAST_SAHI_OVERLAP} "
            f"sahi_every={HYBRID_SAHI_EVERY_SEC:.0f}s "
            f"hybrid_sahi={'off(monitor-on)' if SAHI256_ENABLED else 'on'}"
        )
        STORE.set_detector_name("light+sahi-256")
        with INFER_LOCK:
            light_model = YOLO(light_path)
            sahi_model = AutoDetectionModel.from_pretrained(
                model_type="yolov8",
                model_path=light_path,
                confidence_threshold=conf if conf > 0 else SAHI_CONF,
                device=sahi_dev,
                image_size=FAST_SAHI_IMGSZ,
            )
        sahi_cache: list = []
        sahi_rej_cache: list = []
        last_sahi_t = 0.0
        sahi_ready = False
        force_sahi_next = False  # 첫 light 게시 직후 1회 SAHI
        cache_lock = threading.Lock()
        sahi_busy = threading.Event()
        light_cycles = 0

        def _publish_both_union(raw_f, boxes_l, rej_l, t_inf0):
            """light∪cache → 알람재필터 → 밀도 게시. 튜브·사람 이중집계 방지."""
            with cache_lock:
                cache = list(sahi_cache)
                rej_c = list(sahi_rej_cache)
            h_f, w_f = raw_f.shape[:2]
            roi_m, _ = make_live_roi_mask(h_f, w_f)
            H_f = scale_homography_for_frame(w_f, h_f)
            merged = nms_boxes(list(boxes_l) + cache, iou_thresh=0.45)
            merged, alarm_rej = refine_alarm_boxes(merged, raw_f, (h_f, w_f))
            boxes = stabilizer.update(merged, (h_f, w_f))
            persons = [
                b for b in boxes if not (len(b) >= 6 and int(b[5]) == 1)
            ]
            tubes = [b for b in boxes if len(b) >= 6 and int(b[5]) == 1]
            centers = boxes_to_centers(
                persons + _tubes_for_count(tubes, persons)
            )
            infer_ms = (time.perf_counter() - t_inf0) * 1000.0
            rejected = list(rej_l) + rej_c + list(alarm_rej)
            publish_detection_result(
                raw_f,
                centers,
                boxes,
                rejected,
                roi_m,
                H_f,
                cell_w,
                cell_h,
                infer_ms,
                pipeline="fast",
            )

        def _sahi_refresh_bg(frame, mask):
            """hybrid SAHI는 백그라운드 — light 갱신을 막지 않음."""
            nonlocal sahi_cache, sahi_rej_cache, last_sahi_t, sahi_ready
            t_sahi = time.perf_counter()
            try:
                with INFER_LOCK:
                    _cs, boxes_s, rej_s = detect_people_sahi_fast(
                        sahi_model, frame, mask
                    )
                with cache_lock:
                    sahi_cache = list(boxes_s)
                    sahi_rej_cache = list(rej_s)
                    last_sahi_t = time.perf_counter()
                    sahi_ready = True
                eprint(
                    f"[fast/both] SAHI refresh person="
                    f"{sum(1 for b in boxes_s if not (len(b)>=6 and int(b[5])==1))} "
                    f"tube="
                    f"{sum(1 for b in boxes_s if len(b)>=6 and int(b[5])==1)} "
                    f"ms={(time.perf_counter() - t_sahi)*1000:.0f}"
                )
            except Exception as exc:  # noqa: BLE001
                eprint(f"[fast/both] SAHI FAILED: {exc}")
            finally:
                sahi_busy.clear()

        while True:
            t0 = time.perf_counter()
            if MODEL_RELOAD_REQUEST.is_set():
                new_path = resolve_fast_sahi_model()
                set_reload_meta(state="reloading", path=new_path)
                try:
                    for _ in range(200):
                        if not sahi_busy.is_set():
                            break
                        time.sleep(0.1)
                    with INFER_LOCK:
                        light_model = YOLO(new_path)
                        sahi_model = AutoDetectionModel.from_pretrained(
                            model_type="yolov8",
                            model_path=new_path,
                            confidence_threshold=conf if conf > 0 else SAHI_CONF,
                            device=sahi_dev,
                            image_size=FAST_SAHI_IMGSZ,
                        )
                    with cache_lock:
                        sahi_cache, sahi_rej_cache = [], []
                        last_sahi_t = 0.0
                        sahi_ready = False
                    force_sahi_next = False
                    stabilizer.buf.clear()
                    set_reload_meta(
                        state="done",
                        path=new_path,
                        doneAt=datetime.now(timezone.utc).isoformat(),
                        error=None,
                    )
                    eprint(f"[reload] BOTH 모델 교체 완료 → {new_path}")
                except Exception as exc:  # noqa: BLE001
                    set_reload_meta(state="error", error=str(exc))
                    eprint(f"[reload] BOTH 교체 실패: {exc}")
                finally:
                    MODEL_RELOAD_REQUEST.clear()
            raw = STORE.get_raw_copy()
            if raw is None:
                time.sleep(0.05)
                continue
            STORE.status = "ok"
            t_inf = time.perf_counter()
            roi_mask, _ = make_live_roi_mask(raw.shape[0], raw.shape[1])
            # light는 블로킹 락(짧게 점유). hybrid SAHI는 백그라운드라 light 루프를 안 막음.
            with INFER_LOCK:
                _c, boxes_l, rej_l = detect_people_fast(
                    light_model,
                    raw,
                    roi_mask,
                    conf=conf,
                    imgsz=FAST_LIGHT_IMGSZ,
                    upscale=FAST_LIGHT_UPSCALE,
                    device=device,
                )
            _publish_both_union(raw, boxes_l, rej_l, t_inf)
            light_cycles += 1

            # SAHI256 모니터가 켜져 있으면 hybrid SAHI는 끔
            # (CPU에서 SAHI×2 + INFER_LOCK → FAST 경보가 분 단위로 정지하는 것이 확인됨)
            if not SAHI256_ENABLED:
                with cache_lock:
                    ready_now = sahi_ready
                    last_t = last_sahi_t
                if not ready_now and light_cycles >= 2 and not force_sahi_next:
                    force_sahi_next = True
                now = time.perf_counter()
                due = force_sahi_next or (
                    ready_now and (now - last_t) >= HYBRID_SAHI_EVERY_SEC
                )
                if due and not PRECISE_WANT.is_set() and not sahi_busy.is_set():
                    force_sahi_next = False
                    sahi_busy.set()
                    threading.Thread(
                        target=_sahi_refresh_bg,
                        args=(raw.copy(), roi_mask.copy()),
                        daemon=True,
                        name="both-sahi-refresh",
                    ).start()

            sleep = max(
                0.15,
                min(analyze_every_sec, 2.0) - (time.perf_counter() - t0),
            )
            time.sleep(sleep)
        return

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
            # 락을 잡은 뒤에는 양보하지 않음 (continue 시 FAST 기아)
            with INFER_LOCK:
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
                boxes = stabilizer.update(boxes, (h, w))
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

    if fast_backend == "light":
        # preview LIGHT + (VISION_MAXACC=1) 멀티스케일·밴드·SAHI·교사 합집합
        light_path = resolve_fast_sahi_model()
        light_imgsz = FAST_LIGHT_IMGSZ
        light_up = FAST_LIGHT_UPSCALE
        use_max = ACCURACY_MAX
        eprint(
            f"[fast] LIGHT 모델={light_path} device={device} "
            f"imgsz={light_imgsz} upscale={light_up} conf={conf} "
            f"maxacc={use_max}"
        )
        STORE.set_detector_name(
            "yolo-accuracy-max" if use_max else "yolo-light-1920"
        )
        with INFER_LOCK:
            model = YOLO(light_path)
        teachers: list = []
        sahi_model = None
        if use_max:
            eprint(
                f"[fast] STRICT_FP={STRICT_FP} teacher={USE_TEACHER_EXTRA} "
                f"personMin={PERSON_MIN_CONF} tubeMin={TUBE_MIN_CONF} "
                f"foamReject={FOAM_REJECT_FRAC}"
            )
            if USE_TEACHER_EXTRA:
                for rel in FAST_TEACHER_EXTRA:
                    tp = ROOT / rel
                    if not tp.exists() or tp.resolve() == Path(light_path).resolve():
                        continue
                    try:
                        with INFER_LOCK:
                            teachers.append(YOLO(str(tp)))
                        eprint(f"[fast] maxacc teacher += {tp.name}")
                        break  # 대형 1개면 충분 (CPU 시간)
                    except Exception as exc:  # noqa: BLE001
                        eprint(f"[fast] teacher skip {rel}: {exc}")
            else:
                eprint("[fast] maxacc teacher OFF (PRECISION — 허공 오탐 억제)")
            # PRECISION 기본: SAHI 보조는 로드만 하고 추론 합집합에는 안 넣음
            # (VISION_PRECISION_SAHI=1 이면 합집합 사용). 모니터는 SAHI256 별도.
            if PRECISION_SAHI or not PRECISION_ENABLED:
                try:
                    with INFER_LOCK:
                        sahi_model = AutoDetectionModel.from_pretrained(
                            model_type="yolov8",
                            model_path=light_path,
                            confidence_threshold=conf if conf > 0 else SAHI_CONF,
                            device=_sahi_autodm_device(),
                            image_size=FAST_SAHI_IMGSZ,
                        )
                    eprint("[fast] maxacc SAHI band pass ON (알람 합집합)")
                except Exception as exc:  # noqa: BLE001
                    eprint(f"[fast] SAHI pass skip: {exc}")
                    sahi_model = None
            else:
                sahi_model = None
                eprint(
                    "[fast] maxacc SAHI 합집합 OFF (PRECISION). "
                    "모니터: /stream/sahi256 · 합집합: VISION_PRECISION_SAHI=1"
                )
        while True:
            t0 = time.perf_counter()
            if MODEL_RELOAD_REQUEST.is_set():
                new_path = resolve_fast_sahi_model()
                set_reload_meta(state="reloading", path=new_path)
                try:
                    with INFER_LOCK:
                        model = YOLO(new_path)
                        if sahi_model is not None:
                            sahi_model = AutoDetectionModel.from_pretrained(
                                model_type="yolov8",
                                model_path=new_path,
                                confidence_threshold=conf if conf > 0 else SAHI_CONF,
                                device=_sahi_autodm_device(),
                                image_size=FAST_SAHI_IMGSZ,
                            )
                    stabilizer.buf.clear()
                    set_reload_meta(
                        state="done",
                        path=new_path,
                        doneAt=datetime.now(timezone.utc).isoformat(),
                        error=None,
                    )
                    eprint(f"[reload] LIGHT/maxacc 모델 교체 완료 → {new_path}")
                except Exception as exc:  # noqa: BLE001
                    set_reload_meta(state="error", error=str(exc))
                    eprint(f"[reload] LIGHT 교체 실패: {exc}")
                finally:
                    MODEL_RELOAD_REQUEST.clear()
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
                t_inf = time.perf_counter()
                if use_max:
                    centers, boxes, rejected = detect_people_accuracy_max(
                        model,
                        teachers,
                        sahi_model,
                        raw,
                        roi_mask,
                        conf=conf,
                        device=device,
                    )
                else:
                    centers, boxes, rejected = detect_people_fast(
                        model,
                        raw,
                        roi_mask,
                        conf=conf,
                        imgsz=light_imgsz,
                        upscale=light_up,
                        device=device,
                        confirm=True,
                    )
                boxes = stabilizer.update(boxes, (h, w))
                persons = [
                    b for b in boxes if not (len(b) >= 6 and int(b[5]) == 1)
                ]
                tubes = [b for b in boxes if len(b) >= 6 and int(b[5]) == 1]
                centers = boxes_to_centers(
                    persons + _tubes_for_count(tubes, persons)
                )
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
        f"[fast] SAHI(알람) 모델={fast_path} device={sahi_dev} "
        f"upscale={FAST_SAHI_UPSCALE} slice={FAST_SAHI_SLICE} "
        f"overlap={FAST_SAHI_OVERLAP} max_edge={FAST_SAHI_MAX_EDGE} "
        f"imgsz={FAST_SAHI_IMGSZ} conf={conf}"
    )
    STORE.set_detector_name("sahi-256-fast")
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
            t_inf = time.perf_counter()
            _c, boxes, rejected = detect_people_sahi_fast(sahi_model, raw, roi_mask)
            boxes = stabilizer.update(boxes, (h, w))
            persons = [
                b for b in boxes if not (len(b) >= 6 and int(b[5]) == 1)
            ]
            tubes = [b for b in boxes if len(b) >= 6 and int(b[5]) == 1]
            centers = boxes_to_centers(
                persons + _tubes_for_count(tubes, persons)
            )
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
        # SAHI 1회가 수십 초일 수 있음 → 끝나면 바로 다음 (최소 간격만 보장)
        sleep = max(0.2, analyze_every_sec - (time.perf_counter() - t0))
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

            boxes = stabilizer.update(boxes, (h, w))
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


def sahi256_analyze_loop(
    model_path: str,
    conf: float = YOLO_CONF,
):
    """256×256 / 겹침 25% SAHI 모니터 전용 루프 (detect_sahi_2x 규격).

    경보·안전지도(STORE)는 FAST만 갱신. 이 루프는 STORE_SAHI256·/stream/sahi256 만 갱신.
    CPU 경합을 줄이려 INFER_LOCK + PRECISE_WANT(FAST 양보) + 쿨다운을 쓴다.
    """
    if not SAHI256_ENABLED:
        set_sahi256_meta(state="disabled", enabled=False)
        eprint("[sahi256] 비활성 (VISION_SAHI256=0)")
        return

    path = resolve_fast_sahi_model()
    device = _sahi_autodm_device()
    eprint(
        f"[sahi256] model={path} device={device} "
        f"upscale={SAHI256_UPSCALE} slice={SAHI256_SLICE} "
        f"overlap={SAHI256_OVERLAP} max_edge={SAHI256_MAX_EDGE} "
        f"cooldown={SAHI256_COOLDOWN_SEC:.0f}s"
    )
    set_sahi256_meta(state="loading", lastError=None, enabled=True)
    STORE_SAHI256.set_detector_name("sahi-256")
    try:
        with INFER_LOCK:
            sahi_model = AutoDetectionModel.from_pretrained(
                model_type="yolov8",
                model_path=path,
                confidence_threshold=conf if conf > 0 else SAHI_CONF,
                device=device,
                image_size=FAST_SAHI_IMGSZ,
            )
    except Exception as exc:  # noqa: BLE001
        eprint(f"[sahi256] model load FAILED: {exc}")
        set_sahi256_meta(state="error", lastError=str(exc))
        return

    set_sahi256_meta(state="idle")
    eprint("[sahi256] ready — monitor /stream/sahi256")

    while True:
        try:
            raw = STORE.get_raw_copy()
            if raw is None:
                time.sleep(0.5)
                continue

            # 긴변 제한으로 조각 수 폭발 방지 (모니터용)
            h0, w0 = raw.shape[:2]
            infer_frame, r = fit_long_edge(raw, SAHI256_MAX_EDGE)
            ih, iw = infer_frame.shape[:2]
            roi_mask, _ = make_live_roi_mask(ih, iw)

            set_sahi256_meta(state="running", lastError=None)
            t0 = time.perf_counter()
            # 기본: 락 없이 추론 — FAST 경보를 분 단위로 굶기지 않음
            if SAHI256_USE_LOCK:
                PRECISE_WANT.set()
                try:
                    with INFER_LOCK:
                        PRECISE_WANT.clear()
                        raw_boxes = detect_people_sahi(
                            sahi_model,
                            infer_frame,
                            roi_mask,
                            upscale=SAHI256_UPSCALE,
                            slice_size=SAHI256_SLICE,
                            overlap=SAHI256_OVERLAP,
                        )
                finally:
                    PRECISE_WANT.clear()
            else:
                raw_boxes = detect_people_sahi(
                    sahi_model,
                    infer_frame,
                    roi_mask,
                    upscale=SAHI256_UPSCALE,
                    slice_size=SAHI256_SLICE,
                    overlap=SAHI256_OVERLAP,
                )

            # 축소 좌표 → 원본 좌표
            inv = 1.0 / r if r != 1.0 else 1.0
            scaled = []
            for b in raw_boxes:
                x1, y1, x2, y2, score = b[0], b[1], b[2], b[3], b[4]
                cls = int(b[5]) if len(b) >= 6 else 0
                scaled.append(
                    (x1 * inv, y1 * inv, x2 * inv, y2 * inv, score, cls)
                )

            persons, tubes = _split_by_class(scaled)
            # 비교 모니터: 기각 문턱 소폭 완화 (알람 FAST와 분리)
            confirmed, person_rej = split_person_candidates(
                persons, frame_hw=(h0, w0), frame_bgr=raw, relax=True
            )
            tubes_ok = filter_tubes(
                tubes, (h0, w0), confirmed, frame_bgr=raw, relax=True
            )
            # 기각 튜브도 비교 모니터에 표시 (확정 목록에 없는 원본 튜브)
            ok_keys = {
                (round(float(t[0]), 1), round(float(t[1]), 1),
                 round(float(t[2]), 1), round(float(t[3]), 1))
                for t in tubes_ok
            }
            tube_rej = []
            for t in tubes:
                key = (
                    round(float(t[0]), 1), round(float(t[1]), 1),
                    round(float(t[2]), 1), round(float(t[3]), 1),
                )
                if key not in ok_keys:
                    tube_rej.append(
                        (float(t[0]), float(t[1]), float(t[2]), float(t[3]),
                         float(t[4]), 1)
                    )
            rejected = list(person_rej) + tube_rej
            boxes = [(*tuple(p[:5]), 0) for p in confirmed] + [
                (*tuple(t[:5]), 1) for t in tubes_ok
            ]
            infer_ms = (time.perf_counter() - t0) * 1000.0
            n_tube = sum(1 for b in boxes if len(b) >= 6 and int(b[5]) == 1)
            n_person = len(boxes) - n_tube

            STORE_SAHI256.set_raw(raw)
            empty = np.zeros((max(1, h0 // CELL_H), max(1, w0 // CELL_W)), dtype=np.float32)
            STORE_SAHI256.set_result(
                safety=raw,
                density=empty,
                alerts=build_warning_messages(0),
                person_count=n_person,
                status="ok",
                yolo_boxes=boxes,
                yolo_infer_ms=infer_ms,
                rejected_boxes=rejected,
                pipeline="sahi256",
            )
            set_sahi256_meta(
                state="ok",
                personCount=n_person,
                tubeCount=n_tube,
                rejectedCount=len(rejected),
                inferMs=infer_ms,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                lastError=None,
            )
            eprint(
                f"[sahi256] person={n_person} tube={n_tube} "
                f"rejected={len(rejected)} ms={infer_ms:.0f}"
            )
            time.sleep(SAHI256_COOLDOWN_SEC)
        except Exception as exc:  # noqa: BLE001
            eprint(f"[sahi256] FAILED: {exc}")
            PRECISE_WANT.clear()
            set_sahi256_meta(state="error", lastError=str(exc))
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

    @app.after_request
    def _cors(resp):
        """타 앱(다른 포트)에서 스트림·상태 JSON을 직접 쓸 수 있게 허용."""
        raw = (os.environ.get("CORS_ORIGINS") or "*").strip()
        origin = request.headers.get("Origin")
        if raw == "*":
            resp.headers["Access-Control-Allow-Origin"] = "*"
        elif origin and origin in {s.strip() for s in raw.split(",") if s.strip()}:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    @app.route("/", methods=["OPTIONS"])
    @app.route("/<path:_any>", methods=["OPTIONS"])
    def _cors_preflight(_any: str = ""):
        return ("", 204)

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
        snap["restart"] = get_restart_meta()
        snap["pack"] = get_pack_meta()
        snap["sahi256"] = get_sahi256_meta()
        snap["collect"] = get_collect_meta()
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
        """학습 가중치를 무중단 재적용.

        - 우선 models/yolo26s_beach_ft.pt (Colab 저장명) 그대로 적용.
        - 없고 best.pt 만 있으면 → yolo26s_beach_ft.pt 로 복사 후 적용.
        - FAST 루프가 다음 사이클에 새 가중치로 교체(다운타임 없음).
        """
        best = ROOT / FAST_MODEL_BEST
        deploy = ROOT / FAST_MODEL_DEPLOY
        deploy.parent.mkdir(parents=True, exist_ok=True)
        applied_from = None
        if deploy.is_file():
            applied_from = str(deploy)
            eprint(f"[reload] 우선 적용: {deploy.name}")
        elif best.is_file():
            shutil.copy2(best, deploy)
            applied_from = str(best)
            eprint(f"[reload] {best.name} → {deploy.name} 복사 후 적용")
        else:
            return (
                jsonify(
                    {
                        "ok": False,
                        "message": (
                            "모델 파일이 없습니다: "
                            f"{deploy} (권장, Colab 저장명) 또는 {best}. "
                            "Colab의 yolo26s_beach_ft.pt 를 vision/models/ 에 넣으세요"
                        ),
                        "model": {"path": str(deploy), "isLocalFile": False},
                    }
                ),
                404,
            )

        info = inspect_fast_model()
        info["appliedFrom"] = applied_from
        request_model_reload()
        return jsonify(
            {
                "ok": True,
                "message": (
                    f"{Path(applied_from).name} 적용 · 다음 사이클에 무중단 재적용"
                ),
                "model": info,
            }
        )

    @app.post("/api/restart-server")
    def restart_server():
        """비전 서버 프로세스 재시작 (코드/환경변수 반영)."""
        if get_restart_meta().get("state") == "restarting":
            return (
                jsonify({"ok": False, "message": "이미 재시작 중입니다."}),
                409,
            )
        return jsonify(request_server_restart())

    @app.post("/api/pack-dataset")
    def pack_dataset():
        """라벨 데이터셋으로 gwangalli_dataset.zip 생성(백그라운드)."""
        started = request_pack_dataset()
        if not started:
            return (
                jsonify({"ok": False, "message": "이미 ZIP 생성 중입니다."}),
                409,
            )
        return jsonify({"ok": True, "message": "gwangalli_dataset.zip 생성 시작"})

    @app.get("/api/dataset-zip")
    def dataset_zip():
        """생성된 gwangalli_dataset.zip 다운로드."""
        if not DATASET_ZIP.exists():
            return (
                jsonify({"ok": False, "message": "zip이 아직 없습니다. 먼저 생성하세요."}),
                404,
            )
        return send_file(
            str(DATASET_ZIP),
            as_attachment=True,
            download_name="gwangalli_dataset.zip",
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

    @app.get("/stream/sahi256")
    def stream_sahi256():
        """256×256 / 겹침 25% SAHI 모니터 (FAST와 독립, 박스만)."""

        def gen():
            while True:
                jpg = STORE_SAHI256.get_yolo_jpeg()
                if jpg is None:
                    blank = np.zeros((360, 640, 3), dtype=np.uint8)
                    cv2.putText(
                        blank,
                        "Waiting SAHI 256...",
                        (140, 170),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 220, 255),
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
    button.restart {
      background: #c45c1a; color: #fff; border: 0;
      padding: 9px 16px; font-size: 14px; font-weight: 600;
      border-radius: 6px; cursor: pointer;
    }
    button.restart:hover { background: #a34c15; }
    button.restart:disabled { background: #444; cursor: not-allowed; }
    #reloadStatus, #restartStatus, #packStatus { font-size: 12px; color: #9fb7d8; }
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
      <div class="sub" id="fastSub">밴드 SAHI · 경보 기준</div>
    </div>
    <div class="src">
      <div class="label">SAHI 256×256</div>
      <div class="value" id="sahi256Val">—</div>
      <div class="sub" id="sahi256Sub">비교용 · 기본 OFF</div>
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

  <p class="note">역할분리: 알람(A)=light∪SAHI 후 정밀재필터(구역·부표·보트) · 비교(B)=SAHI256 회수우선. 알람 conf: VISION_ALARM_BEACH/WATER/FAR/TUBE_CONF · SAHI주기: VISION_HYBRID_SAHI_EVERY</p>

  <h2>모니터링 A — FAST 경보 (light∪SAHI)</h2>
  <img src="/stream/yolo" alt="FAST monitor"/>
  <p class="note">light∪SAHI 합집합 후 알람 정밀재필터(물/먼바다 높은 conf·부표줄·보트 제거). 주황=확정. 경보·격자 기준.</p>

  <h2>모니터링 B — SAHI 256 비교 (기본 꺼짐)</h2>
  <img src="/stream/sahi256" alt="SAHI 256 monitor"/>
  <p class="note">비교=회수 우선(낮은 conf·텍스처 OFF). 회색 X=기각 사람 · 어두운 파랑 X=기각 튜브. VISION_SAHI256=1 · PERSON/TUBE_CONF</p>

  <h2>안전지도 (FAST 기준)</h2>
  <img src="/stream" alt="safety map"/>

  <div id="alert" class="ok"></div>

  <h2>관리 도구</h2>
  <div class="controls">
    <button class="reload" id="packBtn" onclick="packDataset()">학습용 ZIP 생성</button>
    <span id="packStatus">라벨된 dataset → gwangalli_dataset.zip (Colab 학습용 train/val).</span>
  </div>
  <div class="controls">
    <button class="reload" id="reloadBtn" onclick="reloadModel()">모델 재적용</button>
    <span id="reloadStatus">models/yolo26s_beach_ft.pt 우선 적용 (없으면 best.pt). 무중단 교체.</span>
  </div>
  <div class="controls">
    <button class="restart" id="restartBtn" onclick="restartServer()">서버 재시작</button>
    <span id="restartStatus">프로세스 종료 후 동일 옵션으로 다시 기동 (코드·env 반영, 약 5~15초).</span>
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
          (r.personCount ?? 0) + '명 · tube ' + (r.tubeCount ?? 0);
        document.getElementById('fastSub').textContent =
          '경보 기준 · ' + dens + ' · ' + Number(r.yoloInferMs || 0).toFixed(0) + 'ms';

        const s256 = r.sahi256 || {};
        const s256El = document.getElementById('sahi256Val');
        const s256Sub = document.getElementById('sahi256Sub');
        if (s256El && s256Sub) {
          if (s256.state === 'disabled' || s256.enabled === false) {
            s256El.textContent = '꺼짐';
            s256Sub.textContent = 'VISION_SAHI256=1 로 켜기';
          } else if (s256.state === 'error') {
            s256El.textContent = '오류';
            s256Sub.textContent = s256.lastError || '';
          } else if (s256.state === 'running' || s256.state === 'loading') {
            s256El.textContent = '분석 중…';
            s256Sub.textContent = '256×256 · 겹침 25%';
          } else {
            s256El.textContent =
              (s256.personCount ?? 0) + '명 · tube ' + (s256.tubeCount ?? 0);
            s256Sub.textContent =
              '기각 ' + (s256.rejectedCount ?? 0) +
              ' · ' + Number(s256.inferMs || 0).toFixed(0) + 'ms · 모니터 전용';
          }
        }

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
      if (!confirm('라벨된 사진으로 gwangalli_dataset.zip 을 만들까요?\\n(dataset/images+labels → train/val)\\n장수에 따라 수 분 걸릴 수 있습니다.')) return;
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
      for (let i = 0; i < 600; i++) {
        await new Promise(res => setTimeout(res, 1500));
        try {
          const r = await fetch('/api/status', { cache: 'no-store' }).then(x => x.json());
          const pk = r.pack || {};
          const elapsed = Math.round((Date.now() - t0) / 1000);
          if (pk.state === 'done') {
            st.innerHTML = 'gwangalli_dataset.zip 완료 · ' + (pk.frames || 0) + '장' +
              ' (train ' + (pk.train || 0) + ' / val ' + (pk.val || 0) + ') · ' +
              (pk.sizeMB != null ? pk.sizeMB + 'MB' : '') +
              ' (' + elapsed + 's) · ' +
              '<a href="/api/dataset-zip" style="color:#7fb2ff" download>다운로드</a>' +
              ' → Drive에 올리고 Colab 4절 실행';
            btn.disabled = false;
            return;
          }
          if (pk.state === 'error') {
            st.textContent = 'ZIP 오류: ' + (pk.error || '알 수 없음');
            btn.disabled = false;
            return;
          }
          st.textContent = 'ZIP 생성 중… (' + (pk.frames || 0) + '장, train ' +
            (pk.train || 0) + '/val ' + (pk.val || 0) + ', ' + elapsed + 's)';
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
      if (!confirm('models/yolo26s_beach_ft.pt 를 우선 적용할까요?\\n없으면 best.pt 를 배포 슬롯에 복사합니다.\\n다음 분석 사이클에 무중단 교체됩니다.')) return;
      btn.disabled = true;
      st.textContent = 'yolo26s_beach_ft.pt 적용 중…';
      try {
        const r = await fetch('/api/reload-model', { method: 'POST' }).then(x => x.json());
        if (!r.ok) {
          st.textContent = '실패: ' + (r.message || '알 수 없는 오류');
          btn.disabled = false;
          return;
        }
        const m = r.model || {};
        const from = m.appliedFrom || m.path || '?';
        st.textContent = '적용: ' + from +
          ' → ' + (m.path || '?') +
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

    async function restartServer() {
      const btn = document.getElementById('restartBtn');
      const st = document.getElementById('restartStatus');
      if (!confirm('비전 서버를 재시작할까요?\\n스트림이 잠시 끊긴 뒤 자동으로 다시 연결됩니다.')) return;
      btn.disabled = true;
      st.textContent = '재시작 요청 중…';
      try {
        const r = await fetch('/api/restart-server', { method: 'POST' }).then(x => x.json());
        if (!r.ok) {
          st.textContent = '실패: ' + (r.message || '알 수 없는 오류');
          btn.disabled = false;
          return;
        }
        st.textContent = r.message || '재시작 중…';
        waitForServerBack(st, btn);
      } catch (e) {
        // 응답 전에 프로세스가 죽으면 fetch 실패 → 복구 폴링
        st.textContent = '재시작 중… (연결 끊김, 복구 대기)';
        waitForServerBack(st, btn);
      }
    }

    async function waitForServerBack(st, btn) {
      const t0 = Date.now();
      for (let i = 0; i < 90; i++) {
        await new Promise(res => setTimeout(res, 1500));
        const elapsed = Math.round((Date.now() - t0) / 1000);
        try {
          const r = await fetch('/health', { cache: 'no-store' }).then(x => x.json());
          if (r && r.ok) {
            st.textContent = '재시작 완료 · 연결됨 (' + elapsed + 's)';
            btn.disabled = false;
            document.querySelectorAll('img').forEach(im => {
              const base = im.src.split('?')[0];
              im.src = base + '?t=' + Date.now();
            });
            return;
          }
        } catch (e) {
          st.textContent = '재시작 대기 중… (' + elapsed + 's)';
        }
      }
      st.textContent = '복구 확인 시간 초과 · 수동으로 페이지를 새로고침하세요';
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
    global CELL_W, CELL_H, PERSON_MIN_CONF, SERVER_ARGV

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
        choices=["both", "sahi", "yolo", "light"],
        default=DEFAULT_DETECTOR,
        help="both=light∪SAHI(권장), sahi=SAHI만, light=YOLO만, yolo=멀티스케일",
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
    SERVER_ARGV = list(sys.argv[1:])

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
    if args.detector == "both":
        eprint(
            f"fast     = light∪SAHI slice={FAST_SAHI_SLICE} "
            f"overlap={FAST_SAHI_OVERLAP} sahi_every={HYBRID_SAHI_EVERY_SEC:.0f}s"
        )
        eprint("pipeline = FAST(light + SAHI-256 합집합)")
    elif args.detector == "sahi":
        eprint(
            f"fast     = SAHI slice={FAST_SAHI_SLICE} "
            f"upscale={FAST_SAHI_UPSCALE} overlap={FAST_SAHI_OVERLAP}"
        )
        eprint("pipeline = FAST(SAHI-256)")
    else:
        eprint(f"fast     = detector={args.detector}")
        eprint("pipeline = FAST + 선택 PRECISE")
    eprint("telecom  = SK 지오비전 퍼즐 장소 혼잡도 (보조, 10분 캐시)")
    eprint(f"stream   = http://127.0.0.1:{args.port}/stream")
    eprint(f"precise  = http://127.0.0.1:{args.port}/stream/precise")
    eprint(f"yolo     = http://127.0.0.1:{args.port}/stream/yolo  (FAST 모니터)")
    eprint(f"sahi256  = http://127.0.0.1:{args.port}/stream/sahi256  (256×256 모니터)")
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

    t_collect = threading.Thread(
        target=collect_raw_loop, args=(COLLECT_RAW_EVERY_SEC,), daemon=True
    )
    t_collect.start()
    if COLLECT_RAW_ENABLED:
        eprint(
            f"collect  = 내장 raw 저장 every={COLLECT_RAW_EVERY_SEC:.0f}s "
            f"→ {COLLECT_RAW_DIR} (끄기: VISION_COLLECT_RAW=0)"
        )
    ensure_autolabel_watch()

    t_sk = threading.Thread(target=sk_refresh_loop, daemon=True)
    t_sk.start()

    if CROWD_ENABLED:
        t_crowd = threading.Thread(target=crowd_count_loop, daemon=True)
        t_crowd.start()
        eprint(f"crowd    = {CROWD_MODEL}/{CROWD_WEIGHTS} 병행 (밀도추정, {CROWD_INTERVAL_SEC:.0f}s)")

    if args.detector in ("both", "sahi", "light"):
        backend = args.detector  # both | sahi | light
        t_fast = threading.Thread(
            target=fast_analyze_loop,
            args=(model_path, FAST_EVERY_SEC, args.cell_w, args.cell_h, FAST_CONF),
            kwargs={"fast_backend": backend},
            daemon=True,
        )
        t_fast.start()
        eprint(f"fast     = backend={backend}")
        # both/sahi 는 이미 SAHI를 알람에 씀 → 비교 모니터만 옵션
        if SAHI256_ENABLED:
            t_s256 = threading.Thread(
                target=sahi256_analyze_loop,
                args=(model_path, args.conf),
                daemon=True,
            )
            t_s256.start()
            eprint(
                f"sahi256  = 모니터 전용 256×256/overlap={SAHI256_OVERLAP} "
                f"(쿨다운 {SAHI256_COOLDOWN_SEC:.0f}s). 끄기: VISION_SAHI256=0"
            )
        else:
            set_sahi256_meta(state="disabled", enabled=False)
            eprint("sahi256  = 비활성 (VISION_SAHI256=0)")
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
                "precise  = 비활성(기본). "
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
