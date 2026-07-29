# ============================================================
# 안전지도(Safety Map) 생성
#
# 원본 사진 + 명/m² 격자 배열 → 40px 칸마다
#   < 4      : 초록 #00FF00 (50% 투명)
#   4 ~ 6    : 노랑 #FFFF00 (50% 투명)
#   >= 6     : 빨강 #FF0000 (50% 투명)
# 원본 위에 반투명 오버레이로 합성한 이미지를 저장합니다.
# ============================================================

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction

from homography_density import (
    DST_METERS,
    SRC_PIXELS,
    build_homography,
    polygon_area_m2,
)


ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input" / "screenshots"
OUTPUT_DIR = ROOT / "output" / "safety_map"
MODEL_NAME = "yolov8n.pt"
CONFIDENCE = 0.25
UPSCALE = 2.0
SLICE_SIZE = 256
OVERLAP_RATIO = 0.25

# 격자 한 칸 크기 (픽셀) — 기본은 정사각 40
CELL_PX = 40
CELL_W = 40
CELL_H = 40
# 오버레이 불투명도: 0.5 = 50% 투명(원본과 1:1 혼합)
OVERLAY_ALPHA = 0.5

# 명/m² 임계값 (주의 4, 위험 6)
THRESH_CAUTION = 4.0
THRESH_DANGER = 6.0

# 위험(빨강) 격자 경고 메시지 — 앱·알림과 동일 문구
MSG_TOURIST = (
    "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요."
)
MSG_MANAGER = (
    "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요."
)

# BGR (OpenCV)
COLOR_SAFE = (0, 255, 0)       # #00FF00
COLOR_CAUTION = (0, 255, 255)  # #FFFF00
COLOR_DANGER = (0, 0, 255)     # #FF0000

ROI_POLYGONS_NORM: dict[str, list[tuple[float, float]]] = {
    "01_wide_full_beach": [(0.0, 0.28), (1.0, 0.28), (1.0, 1.0), (0.0, 1.0)],
    "02_zone_left_bridge": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "03_zone_center_bridge": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "04_zone_right_bridge": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "05_shore_sand_focus_left": [(0.0, 0.08), (1.0, 0.08), (1.0, 1.0), (0.0, 1.0)],
    "06_shore_sand_focus_center": [(0.0, 0.08), (1.0, 0.08), (1.0, 1.0), (0.0, 1.0)],
    "07_shore_sand_focus_right": [(0.0, 0.08), (1.0, 0.08), (1.0, 1.0), (0.0, 1.0)],
    "08_new_zone_left": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "09_new_zone_center": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "10_new_zone_right": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
}


def eprint(*args):
    print(*args, file=sys.stderr)


def make_roi_mask(h: int, w: int, polygon_norm):
    pts = np.array([[int(x * w), int(y * h)] for x, y in polygon_norm], dtype=np.int32)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    return mask, pts


def upscale(image_bgr, scale: float):
    h, w = image_bgr.shape[:2]
    return cv2.resize(
        image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC
    )


def detect_person_centers(model, image_bgr, scale: float, roi_mask):
    """SAHI 탐지 후 ROI 안 사람 중심점(원본 좌표) 반환."""
    up = upscale(image_bgr, scale)
    result = get_sliced_prediction(
        up,
        model,
        slice_height=SLICE_SIZE,
        slice_width=SLICE_SIZE,
        overlap_height_ratio=OVERLAP_RATIO,
        overlap_width_ratio=OVERLAP_RATIO,
        verbose=0,
    )
    h, w = roi_mask.shape[:2]
    centers = []
    for p in result.object_prediction_list:
        if str(p.category.name).lower() != "person":
            continue
        cx = ((p.bbox.minx + p.bbox.maxx) / 2.0) / scale
        cy = ((p.bbox.miny + p.bbox.maxy) / 2.0) / scale
        ix, iy = int(round(cx)), int(round(cy))
        if 0 <= ix < w and 0 <= iy < h and roi_mask[iy, ix] > 0:
            centers.append((cx, cy))
    return centers


def build_density_grid_per_m2(
    image_shape,
    centers,
    roi_mask,
    H,
    cell_px: int | None = None,
    cell_w: int | None = None,
    cell_h: int | None = None,
):
    """
    격자마다 명/m² 배열을 만듭니다.
    density_grid[r, c] = (그 칸 사람 수) / (그 칸의 실제 면적 m²)
    ROI 밖 칸은 NaN (색칠하지 않음)

    cell_w / cell_h 를 주면 직사각 격자(예: 40x20)를 사용합니다.
    둘 다 없으면 cell_px(기본 40) 정사각 격자입니다.
    """
    cw = int(cell_w if cell_w is not None else (cell_px if cell_px is not None else CELL_W))
    ch = int(cell_h if cell_h is not None else (cell_px if cell_px is not None else CELL_H))
    h, w = image_shape[:2]
    rows = int(np.ceil(h / ch))
    cols = int(np.ceil(w / cw))

    density = np.full((rows, cols), np.nan, dtype=np.float32)
    counts = np.zeros((rows, cols), dtype=np.int32)
    areas = np.zeros((rows, cols), dtype=np.float32)

    for x, y in centers:
        c = min(cols - 1, int(x) // cw)
        r = min(rows - 1, int(y) // ch)
        counts[r, c] += 1

    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * cw, r * ch
            x1, y1 = min(w, x0 + cw), min(h, y0 + ch)
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            ix, iy = int(cx), int(cy)
            if ix < 0 or iy < 0 or ix >= w or iy >= h or roi_mask[iy, ix] == 0:
                continue

            cell_poly = np.array(
                [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                dtype=np.float64,
            )
            area = polygon_area_m2(cell_poly, H)
            areas[r, c] = area
            if area > 1e-9:
                density[r, c] = counts[r, c] / area
            else:
                density[r, c] = 0.0

    return density, counts, areas


def density_to_color_bgr(value: float):
    """명/m² → 안전/주의/위험 색 (BGR)."""
    if value < THRESH_CAUTION:
        return COLOR_SAFE
    if value < THRESH_DANGER:
        return COLOR_CAUTION
    return COLOR_DANGER


def count_danger_cells(density_grid: np.ndarray) -> int:
    """위험(>=6 명/m²) 격자 개수."""
    valid = density_grid[~np.isnan(density_grid)]
    if valid.size == 0:
        return 0
    return int(np.sum(valid >= THRESH_DANGER))


def build_warning_messages(danger_cells: int) -> dict:
    """
    위험 격자가 하나라도 있으면 관광객/관리자 메시지를 반환합니다.
    없으면 hasDanger=False, 메시지 null.
    """
    if danger_cells <= 0:
        return {
            "hasDanger": False,
            "dangerCellCount": 0,
            "touristMessage": None,
            "managerMessage": None,
        }
    return {
        "hasDanger": True,
        "dangerCellCount": danger_cells,
        "touristMessage": MSG_TOURIST,
        "managerMessage": MSG_MANAGER,
    }


def _find_korean_font(size: int = 18):
    """Windows 한글 폰트를 찾아 PIL ImageFont 로 로드합니다."""
    from PIL import ImageFont

    candidates = [
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path(r"C:\Windows\Fonts\malgunbd.ttf"),
        Path(r"C:\Windows\Fonts\NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return ImageFont.truetype(str(p), size=size)
    return ImageFont.load_default()


def draw_warning_banners(image_bgr: np.ndarray, warnings: dict) -> np.ndarray:
    """
    위험 격자가 있을 때 안전지도 하단에
    관광객용 / 관리자용 경고 문구를 표시합니다.
    """
    if not warnings.get("hasDanger"):
        return image_bgr

    from PIL import Image, ImageDraw

    out = image_bgr.copy()
    h, w = out.shape[:2]
    banner_h = max(96, int(h * 0.28))
    overlay = out.copy()
    cv2.rectangle(overlay, (0, h - banner_h), (w, h), (0, 0, 0), -1)
    out = cv2.addWeighted(out, 0.45, overlay, 0.55, 0)

    rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    font_size = max(12, min(18, w // 28))
    font = _find_korean_font(size=font_size)
    font_small = _find_korean_font(size=max(11, font_size - 2))

    def wrap(text: str, max_chars: int) -> list[str]:
        if len(text) <= max_chars:
            return [text]
        lines = []
        cur = ""
        for ch in text:
            cur += ch
            if len(cur) >= max_chars:
                lines.append(cur)
                cur = ""
        if cur:
            lines.append(cur)
        return lines

    max_chars = max(12, w // max(8, font_size // 2))
    y = h - banner_h + 6
    draw.text((6, y), "[관광객]", fill=(255, 220, 80), font=font_small)
    y += font_small.size + 2
    for line in wrap(MSG_TOURIST, max_chars):
        draw.text((6, y), line, fill=(255, 255, 255), font=font)
        y += font.size + 2
    y += 4
    draw.text((6, y), "[관리자]", fill=(255, 100, 100), font=font_small)
    y += font_small.size + 2
    for line in wrap(MSG_MANAGER, max_chars):
        draw.text((6, y), line, fill=(255, 200, 200), font=font)
        y += font.size + 2

    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def render_safety_map(
    original_bgr: np.ndarray,
    density_grid: np.ndarray,
    cell_px: int | None = None,
    alpha: float = OVERLAY_ALPHA,
    cell_w: int | None = None,
    cell_h: int | None = None,
    draw_grid_lines: bool = True,
) -> np.ndarray:
    """
    명/m² 격자 배열을 색으로 바꾼 뒤
    원본 위에 50% 투명도로 합성합니다.
    draw_grid_lines=True 이면 합성 후 칸 경계를 그려 격자 크기가 보이게 합니다.
    """
    cw = int(cell_w if cell_w is not None else (cell_px if cell_px is not None else CELL_W))
    ch = int(cell_h if cell_h is not None else (cell_px if cell_px is not None else CELL_H))
    h, w = original_bgr.shape[:2]
    rows, cols = density_grid.shape
    overlay = original_bgr.copy()

    for r in range(rows):
        for c in range(cols):
            val = density_grid[r, c]
            if np.isnan(val):
                continue  # ROI 밖 → 원본 유지
            color = density_to_color_bgr(float(val))
            y0, x0 = r * ch, c * cw
            y1, x1 = min(h, y0 + ch), min(w, x0 + cw)
            cv2.rectangle(overlay, (x0, y0), (x1, y1), color, thickness=-1)

    # 합성: result = (1-alpha)*원본 + alpha*색칠본
    safety = cv2.addWeighted(original_bgr, 1.0 - alpha, overlay, alpha, 0)

    # 격자선은 합성 후에 그려야 반투명에 묻히지 않음 (크기 변화 체감용)
    if draw_grid_lines:
        for r in range(rows):
            for c in range(cols):
                if np.isnan(density_grid[r, c]):
                    continue
                y0, x0 = r * ch, c * cw
                y1, x1 = min(h, y0 + ch), min(w, x0 + cw)
                cv2.rectangle(
                    safety, (x0, y0), (x1 - 1, y1 - 1), (255, 255, 255), thickness=1
                )

    return safety


def draw_legend(image_bgr: np.ndarray) -> np.ndarray:
    """좌상단에 범례를 작게 그립니다."""
    out = image_bgr.copy()
    items = [
        (COLOR_SAFE, f"안전 <{THRESH_CAUTION}"),
        (COLOR_CAUTION, f"주의 {THRESH_CAUTION}~{THRESH_DANGER}"),
        (COLOR_DANGER, f"위험 >={THRESH_DANGER}"),
    ]
    x, y = 8, 8
    for color, label in items:
        cv2.rectangle(out, (x, y), (x + 14, y + 14), color, -1)
        cv2.putText(
            out,
            label,
            (x + 20, y + 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        y += 20
    return out


def process_image(image_path: Path, model, H) -> dict:
    original = cv2.imread(str(image_path))
    if original is None:
        raise FileNotFoundError(image_path)

    h, w = original.shape[:2]
    stem = image_path.stem
    polygon = ROI_POLYGONS_NORM.get(
        stem, [(0.0, 0.25), (1.0, 0.25), (1.0, 1.0), (0.0, 1.0)]
    )
    roi_mask, _ = make_roi_mask(h, w, polygon)

    t0 = time.perf_counter()
    centers = detect_person_centers(model, original, UPSCALE, roi_mask)
    density, counts, areas = build_density_grid_per_m2(
        original.shape, centers, roi_mask, H, CELL_PX
    )
    safety = render_safety_map(original, density, CELL_PX, OVERLAY_ALPHA)
    safety = draw_legend(safety)

    danger_cells = count_danger_cells(density)
    warnings = build_warning_messages(danger_cells)
    if warnings["hasDanger"]:
        print(f"[경고][관광객] {warnings['touristMessage']}")
        print(f"[경고][관리자] {warnings['managerMessage']}")
        safety = draw_warning_banners(safety, warnings)

    elapsed = time.perf_counter() - t0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{stem}_safety_map.jpg"
    cv2.imwrite(str(out_path), safety)

    # 격자 수치도 JSON으로 보관 (앱 연동용)
    meta = {
        "image": str(image_path),
        "output": str(out_path),
        "cellPx": CELL_PX,
        "overlayAlpha": OVERLAY_ALPHA,
        "thresholds": {"caution": THRESH_CAUTION, "danger": THRESH_DANGER},
        "personCount": len(centers),
        "inferenceSeconds": round(elapsed, 3),
        "densityGridPerM2": [
            [None if np.isnan(v) else float(v) for v in row] for row in density
        ],
        "maxDensityPerM2": float(np.nanmax(density)) if np.any(~np.isnan(density)) else 0.0,
        "cellCounts": counts.tolist(),
        "alerts": warnings,
    }
    (OUTPUT_DIR / f"{stem}_safety_map.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def main():
    images = sorted(INPUT_DIR.glob("*.png"))
    if not images:
        eprint(f"[오류] 이미지 없음: {INPUT_DIR}")
        sys.exit(1)

    eprint(f"[1] 모델 로딩: {MODEL_NAME}")
    model_path = ROOT / MODEL_NAME if (ROOT / MODEL_NAME).exists() else MODEL_NAME
    model = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=str(model_path),
        confidence_threshold=CONFIDENCE,
        device="cpu",
    )
    H, _ = build_homography(SRC_PIXELS, DST_METERS)

    eprint(
        f"[2] 안전지도: {CELL_PX}px 격자, alpha={OVERLAY_ALPHA}, "
        f"임계={THRESH_CAUTION}/{THRESH_DANGER}"
    )
    print("-" * 64)
    print(f"{'파일':<34} {'인원':>6} {'최대명/m2':>10} {'초':>7}")
    print("-" * 64)

    for path in images:
        meta = process_image(path, model, H)
        print(
            f"{path.name:<34} {meta['personCount']:>6} "
            f"{meta['maxDensityPerM2']:>10.4f} {meta['inferenceSeconds']:>7.2f}"
        )

    print("-" * 64)
    print(f"저장: {OUTPUT_DIR.resolve()}")
    print("  *_safety_map.jpg  = 합성된 안전지도 (최종 출력)")
    print("  *_safety_map.json = 격자 명/m^2 배열")


if __name__ == "__main__":
    main()
