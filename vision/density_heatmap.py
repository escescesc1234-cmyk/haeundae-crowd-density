# ============================================================
# ROI(해변·물놀이) 안 사람만 → 가우시안 밀도맵 → JET 열지도 저장
# 탐지: 2배 확대 + SAHI 256x256 / 겹침 25%
# ============================================================

from pathlib import Path
import time

import cv2
import numpy as np
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction


# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------
INPUT_DIR = Path("input/screenshots")
OUTPUT_DIR = Path("output/density_heatmap")
MODEL_NAME = "yolov8n.pt"
CONFIDENCE = 0.25
UPSCALE = 2.0
SLICE_SIZE = 256
OVERLAP_RATIO = 0.25

# 가우시안 커널 크기(홀수). 클수록 밀도가 더 넓게 퍼집니다.
GAUSSIAN_KERNEL = 51
# 열지도를 원본 위에 섞을 때 투명도 (0=원본만, 1=열지도만)
HEATMAP_ALPHA = 0.45


# ------------------------------------------------------------
# 이미지별 ROI (정규화 좌표 0~1, 시계방향 다각형)
# 해변(모래) + 물놀이(물가/얕은 바다)만 포함하고
# 하늘·광안대교·먼 배경은 제외합니다.
# ------------------------------------------------------------
ROI_POLYGONS_NORM = {
    # 전체 와이드: 위쪽 먼 바다/하늘 제외, 모래+물가 중심
    "01_wide_full_beach": [
        (0.00, 0.28),
        (1.00, 0.28),
        (1.00, 1.00),
        (0.00, 1.00),
    ],
    # 다리 보이는 구역: 하늘+다리 제외, 바다 하단~모래
    "02_zone_left_bridge": [
        (0.00, 0.48),
        (1.00, 0.48),
        (1.00, 1.00),
        (0.00, 1.00),
    ],
    "03_zone_center_bridge": [
        (0.00, 0.48),
        (1.00, 0.48),
        (1.00, 1.00),
        (0.00, 1.00),
    ],
    "04_zone_right_bridge": [
        (0.00, 0.48),
        (1.00, 0.48),
        (1.00, 1.00),
        (0.00, 1.00),
    ],
    # 이미 물가·모래 중심 크롭: 맨 위 얇은 하늘/수평선만 제외
    "05_shore_sand_focus_left": [
        (0.00, 0.08),
        (1.00, 0.08),
        (1.00, 1.00),
        (0.00, 1.00),
    ],
    "06_shore_sand_focus_center": [
        (0.00, 0.08),
        (1.00, 0.08),
        (1.00, 1.00),
        (0.00, 1.00),
    ],
    "07_shore_sand_focus_right": [
        (0.00, 0.08),
        (1.00, 0.08),
        (1.00, 1.00),
        (0.00, 1.00),
    ],
}


def upscale_image(image_bgr, scale: float):
    """이미지를 scale배 확대합니다."""
    h, w = image_bgr.shape[:2]
    return cv2.resize(
        image_bgr,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_CUBIC,
    )


def make_roi_mask(h: int, w: int, polygon_norm):
    """정규화 다각형 → 원본 크기 ROI 마스크(255=관심, 0=제외)."""
    pts = np.array(
        [[int(x * w), int(y * h)] for x, y in polygon_norm],
        dtype=np.int32,
    )
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    return mask, pts


def detect_people(detection_model, image_bgr):
    """SAHI 슬라이스 탐지 후 person만 반환합니다."""
    result = get_sliced_prediction(
        image_bgr,
        detection_model,
        slice_height=SLICE_SIZE,
        slice_width=SLICE_SIZE,
        overlap_height_ratio=OVERLAP_RATIO,
        overlap_width_ratio=OVERLAP_RATIO,
        verbose=0,
    )
    return [
        p
        for p in result.object_prediction_list
        if str(p.category.name).lower() == "person"
    ]


def person_centers_in_roi(person_preds, scale: float, roi_mask):
    """
    확대본 박스 중심을 원본 좌표로 변환하고,
    ROI 안에 있는 점만 (x, y) 리스트로 반환합니다.
    """
    h, w = roi_mask.shape[:2]
    centers = []
    for p in person_preds:
        cx = ((p.bbox.minx + p.bbox.maxx) / 2.0) / scale
        cy = ((p.bbox.miny + p.bbox.maxy) / 2.0) / scale
        ix, iy = int(round(cx)), int(round(cy))
        # 이미지 범위 밖이면 제외
        if ix < 0 or iy < 0 or ix >= w or iy >= h:
            continue
        # ROI 밖(하늘·건물 등)이면 제외
        if roi_mask[iy, ix] == 0:
            continue
        centers.append((cx, cy))
    return centers


def build_gaussian_density(h: int, w: int, centers, kernel_size: int):
    """
    사람 위치에 점(1.0)을 찍고 가우시안으로 부드럽게 퍼뜨립니다.
    """
    density = np.zeros((h, w), dtype=np.float32)
    for x, y in centers:
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < w and 0 <= iy < h:
            density[iy, ix] += 1.0

    # 커널 크기는 홀수여야 합니다.
    k = kernel_size if kernel_size % 2 == 1 else kernel_size + 1
    # 시그마를 커널에 맞춰 자동 설정 (0이면 OpenCV가 계산)
    density = cv2.GaussianBlur(density, (k, k), sigmaX=0, sigmaY=0)
    return density


def density_to_jet_overlay(image_bgr, density, roi_mask, alpha: float):
    """
    밀도맵을 0~255로 정규화 → JET 컬러맵 → 원본 위에 겹칩니다.
    ROI 밖은 열지도를 그리지 않습니다.
    """
    out = image_bgr.copy()
    if density.max() <= 0:
        return out, None

    # ROI 밖 밀도는 0으로 만들어 하늘/건물에 색이 안  overlays 되게 합니다.
    density_roi = density.copy()
    density_roi[roi_mask == 0] = 0.0

    norm = density_roi / density_roi.max()
    heat_u8 = (norm * 255.0).astype(np.uint8)
    heat_color = cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET)

    # 밀도가 거의 0인 곳은 원본을 유지 (파란 배경 과다 방지)
    active = norm > 0.05
    blended = out.copy()
    blended[active] = (
        (1.0 - alpha) * out[active].astype(np.float32)
        + alpha * heat_color[active].astype(np.float32)
    ).astype(np.uint8)

    # ROI 밖은 원본 유지
    blended[roi_mask == 0] = out[roi_mask == 0]
    return blended, heat_color


def draw_roi_outline(image_bgr, roi_pts):
    """ROI 경계를 노란색 선으로 표시합니다."""
    out = image_bgr.copy()
    cv2.polylines(out, [roi_pts], isClosed=True, color=(0, 255, 255), thickness=2)
    return out


def main():
    images = sorted(INPUT_DIR.glob("*.png"))
    if not images:
        print(f"[오류] 이미지가 없습니다: {INPUT_DIR.resolve()}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1] 모델 로딩: {MODEL_NAME}")
    detection_model = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=MODEL_NAME,
        confidence_threshold=CONFIDENCE,
        device="cpu",
    )

    print(
        f"[2] 탐지 설정: {UPSCALE}배 → {SLICE_SIZE}x{SLICE_SIZE}, "
        f"겹침 {int(OVERLAP_RATIO * 100)}%"
    )
    print(f"[3] 이미지 {len(images)}장 | 가우시안 커널={GAUSSIAN_KERNEL}")
    print("-" * 70)
    print(f"{'파일':<34} {'전체탐지':>8} {'ROI내':>6} {'초':>7}")
    print("-" * 70)

    for image_path in images:
        stem = image_path.stem
        polygon = ROI_POLYGONS_NORM.get(stem)
        if polygon is None:
            print(f"[건너뜀] ROI 미정의: {stem}")
            continue

        original = cv2.imread(str(image_path))
        if original is None:
            print(f"[건너뜀] 읽기 실패: {image_path.name}")
            continue

        h, w = original.shape[:2]
        roi_mask, roi_pts = make_roi_mask(h, w, polygon)

        # 2배 확대 후 SAHI 탐지
        upscaled = upscale_image(original, UPSCALE)
        t0 = time.perf_counter()
        preds = detect_people(detection_model, upscaled)
        elapsed = time.perf_counter() - t0

        centers = person_centers_in_roi(preds, UPSCALE, roi_mask)
        density = build_gaussian_density(h, w, centers, GAUSSIAN_KERNEL)
        heatmap_img, heat_only = density_to_jet_overlay(
            original, density, roi_mask, HEATMAP_ALPHA
        )

        # ROI 경계 + 사람 점 표시한 미리보기
        preview = draw_roi_outline(heatmap_img, roi_pts)
        for x, y in centers:
            cv2.circle(preview, (int(x), int(y)), 3, (255, 255, 255), -1)

        # 저장
        cv2.imwrite(str(OUTPUT_DIR / f"{stem}_heatmap.jpg"), heatmap_img)
        cv2.imwrite(str(OUTPUT_DIR / f"{stem}_heatmap_roi.jpg"), preview)
        if heat_only is not None:
            # ROI만 남긴 순수 열지도도 참고용 저장
            heat_roi = heat_only.copy()
            heat_roi[roi_mask == 0] = 0
            cv2.imwrite(str(OUTPUT_DIR / f"{stem}_heat_only.jpg"), heat_roi)

        # ROI 마스크 자체도 저장 (검=제외, 흰=관심)
        cv2.imwrite(str(OUTPUT_DIR / f"{stem}_roi_mask.png"), roi_mask)

        print(
            f"{image_path.name:<34} {len(preds):>8} {len(centers):>6} {elapsed:>7.2f}"
        )

    print("-" * 70)
    print(f"저장 폴더: {OUTPUT_DIR.resolve()}")
    print("  *_heatmap.jpg      = 원본 + JET 열지도")
    print("  *_heatmap_roi.jpg  = 열지도 + ROI 노란선 + 사람 점")
    print("  *_heat_only.jpg    = 열지도만")
    print("  *_roi_mask.png     = ROI 마스크")


if __name__ == "__main__":
    main()
