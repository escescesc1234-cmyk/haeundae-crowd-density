"""고정 CCTV 정밀도 게이트 — 허공·파도 오탐 억제 (학습 불필요).

근거 (웹·논문):
  - Ultralytics: hard-negative / background 이미지로 FP↓
  - BEM (arXiv 2604.11714): 고정 카메라 배경 프로토타입으로 FP 재점수
  - PLOS One ASV: 반사·파도는 프레임 간 간헐적 → 시간 지속으로 제거
  - 수면 YOLO: HSV 거품·반사 억제 + 후처리

이 모듈은 배경차(러닝 평균) + 수면 텍스처 + 클래스별 규칙을 합친다.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

WATER_Y_TOP = 0.45
WATER_Y_BOT = 0.78


@dataclass
class GateConfig:
    enabled: bool = True
    # 배경차: 박스 내 |frame-bg| 평균이 이보다 작으면 '배경과 같음'
    fg_min: float = 12.0
    # 사람(물): 배경과 비슷 + 바다/거품이면 기각 (해변은 적용 안 함)
    person_fg_min: float = 12.0
    person_sea_frac: float = 0.48
    person_foam_frac: float = 0.32
    person_sea_hard: float = 0.65  # 이 이상 바다색이면 고확신도 기각
    # 튜브: 거품/무색이면 기각 (파도 FP↓ — 색 있는 실튜브만 남김)
    tube_foam_reject: float = 0.16
    tube_color_min: float = 0.22
    tube_fg_min: float = 14.0
    tube_color_hard_min: float = 0.16  # 이보다 낮으면 foam 없어도 기각
    # 배경 러닝 평균 학습률
    bg_alpha: float = 0.04
    foam_v_min: float = 0.65
    foam_s_max: float = 0.32


class WaterPrecisionGate:
    """물 구역 박스만 검사. 모래(해변) 사람은 통과시켜 회수 유지."""

    def __init__(self, cfg: GateConfig | None = None):
        self.cfg = cfg or GateConfig()
        self._bg: np.ndarray | None = None
        self._frames = 0

    def reset(self):
        self._bg = None
        self._frames = 0

    def update_background(self, frame_bgr: np.ndarray):
        if frame_bgr is None or not self.cfg.enabled:
            return
        f = frame_bgr.astype(np.float32)
        if self._bg is None or self._bg.shape != f.shape:
            self._bg = f.copy()
        else:
            a = self.cfg.bg_alpha
            self._bg = (1.0 - a) * self._bg + a * f
        self._frames += 1

    def _box_slice(self, frame, box):
        h, w = frame.shape[:2]
        x1 = max(0, int(round(box[0])))
        y1 = max(0, int(round(box[1])))
        x2 = min(w, int(round(box[2])))
        y2 = min(h, int(round(box[3])))
        if x2 - x1 < 3 or y2 - y1 < 3:
            return None, None, None
        return frame[y1:y2, x1:x2], (x1, y1, x2, y2), (h, w)

    def _cy(self, box, h: float) -> float:
        return (float(box[1]) + float(box[3])) * 0.5 / max(1.0, h)

    def _in_water(self, box, h: float) -> bool:
        return WATER_Y_TOP <= self._cy(box, h) < WATER_Y_BOT

    def _foam_frac(self, patch) -> float:
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        s = hsv[..., 1].astype(np.float32) / 255.0
        v = hsv[..., 2].astype(np.float32) / 255.0
        return float(np.mean((v >= self.cfg.foam_v_min) & (s <= self.cfg.foam_s_max)))

    def _sea_frac(self, patch) -> float:
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hh = hsv[..., 0]
        ss = hsv[..., 1].astype(np.float32) / 255.0
        vv = hsv[..., 2].astype(np.float32) / 255.0
        blue = (hh >= 85) & (hh <= 140) & (ss >= 0.12) & (vv >= 0.18) & (vv <= 0.92)
        gray = (ss <= 0.22) & (vv >= 0.25) & (vv <= 0.85) & (hh >= 70) & (hh <= 150)
        return float(np.mean(blue | gray))

    def _tube_color(self, patch) -> float:
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hh, ss, vv = hsv[..., 0], hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
        # 바다 청록과 구분: 주황·노랑·분홍·선명파랑만
        orange = (hh >= 5) & (hh <= 28) & (ss >= 0.40) & (vv >= 0.45)
        yellow = (hh >= 22) & (hh <= 38) & (ss >= 0.45) & (vv >= 0.50)
        blue = (hh >= 100) & (hh <= 120) & (ss >= 0.55) & (vv >= 0.45) & (vv <= 0.95)
        pink = ((hh >= 145) & (hh <= 175) & (ss >= 0.35) & (vv >= 0.45)) | (
            (hh <= 6) & (ss >= 0.40) & (vv >= 0.45)
        )
        return float(np.mean(orange | yellow | blue | pink))

    def _fg_score(self, frame, box) -> float:
        if self._bg is None or self._frames < 8:
            return 999.0  # 배경 미성숙 → 기각하지 않음
        patch, sl, _ = self._box_slice(frame, box)
        if patch is None or sl is None:
            return 999.0
        x1, y1, x2, y2 = sl
        bg = self._bg[y1:y2, x1:x2]
        return float(np.mean(np.abs(patch.astype(np.float32) - bg)))

    def reject_person(self, box, frame_bgr) -> bool:
        """True = 기각 (허공/파도 오탐). 해변은 항상 False."""
        if not self.cfg.enabled or frame_bgr is None:
            return False
        h = frame_bgr.shape[0]
        if not self._in_water(box, h):
            return False
        score = float(box[4])
        patch, _, _ = self._box_slice(frame_bgr, box)
        if patch is None:
            return False
        foam = self._foam_frac(patch)
        sea = self._sea_frac(patch)
        fg = self._fg_score(frame_bgr, box)
        std = float(patch.std())
        # 바다색 우세 + (밋밋/거품/배경동일) → 허공 (실수영자는 대비↑)
        if sea >= self.cfg.person_sea_hard and (std < 40.0 or foam >= 0.22 or fg < 16.0):
            return True
        if sea >= 0.58 and std < 38.0 and score < 0.50:
            return True
        if sea >= self.cfg.person_sea_frac and std < 34.0 and score < 0.45:
            return True
        # 배경과 거의 같고 바다/거품 → 허공
        if fg < self.cfg.person_fg_min and (
            sea >= self.cfg.person_sea_frac or foam >= 0.22
        ):
            return True
        # 강한 흰 거품
        if foam >= self.cfg.person_foam_frac and score < 0.45:
            return True
        if foam >= 0.28 and sea >= 0.45 and score < 0.42:
            return True
        # 밋밋한 수면
        if std < 26.0 and sea >= 0.45 and score < 0.48:
            return True
        return False

    def reject_tube(self, box, frame_bgr) -> bool:
        """True = 파도/허공 튜브 기각. 실튜브는 주황·분홍·파랑 색이 있어야 함."""
        if not self.cfg.enabled or frame_bgr is None:
            return False
        h = frame_bgr.shape[0]
        cy = self._cy(box, h)
        if cy < WATER_Y_TOP or cy > 0.90:
            return True
        patch, _, _ = self._box_slice(frame_bgr, box)
        if patch is None:
            return True
        foam = self._foam_frac(patch)
        tube_c = self._tube_color(patch)
        sea = self._sea_frac(patch)
        fg = self._fg_score(frame_bgr, box)
        # 튜브색이 거의 없으면 파도/거품으로 간주 (핵심)
        if tube_c < self.cfg.tube_color_hard_min:
            return True
        if foam >= self.cfg.tube_foam_reject and tube_c < self.cfg.tube_color_min:
            return True
        if foam >= 0.28 and tube_c < 0.28:
            return True
        if foam >= 0.18 and tube_c < 0.22:
            return True
        if sea >= 0.40 and tube_c < 0.24:
            return True
        if fg < self.cfg.tube_fg_min and tube_c < self.cfg.tube_color_min:
            return True
        # 잔물결 텍스처(고에지) + 약한 색
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 120)
        if float(np.mean(edges > 0)) >= 0.18 and tube_c < 0.24 and foam >= 0.12:
            return True
        return False

    def filter_boxes(self, boxes: list, frame_bgr) -> tuple[list, list]:
        """(kept, rejected) — cls 1=tube, 0/없음=person."""
        kept, rej = [], []
        for b in boxes:
            is_tube = len(b) >= 6 and int(b[5]) == 1
            if is_tube:
                if self.reject_tube(b, frame_bgr):
                    rej.append(b)
                else:
                    kept.append(b)
            else:
                if self.reject_person(b, frame_bgr):
                    rej.append(b)
                else:
                    kept.append(b)
        return kept, rej
