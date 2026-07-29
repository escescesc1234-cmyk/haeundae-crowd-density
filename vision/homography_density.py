# ============================================================
# 픽셀 → 실제 거리(미터) 변환 + 명/m² 밀도 계산 (초보자용)
#
# [한눈에 보는 원리]
# 1) 사진에서 "위치를 아는" 기준점 4개를 고릅니다.
# 2) 각 점이 현실에서 몇 미터 위치인지 적어 둡니다. (줄자/도면/GPS 등)
# 3) cv2.findHomography 가 "사진 좌표 → 바닥면 미터 좌표" 변환식을 만듭니다.
# 4) 사람 픽셀 위치와 구역 다각형을 미터로 바꾼 뒤
#    (사람 수) / (구역 면적 m²) 로 명/m² 을 구합니다.
#
# [중요]
# - 호모그래피는 "거의 평평한 바닥(모래/지면)" 가정입니다.
# - 아래 SRC/DST 좌표는 예시입니다. 반드시 현장 실측값으로 바꾸세요.
# ============================================================

from pathlib import Path
import json

import cv2
import numpy as np


# ------------------------------------------------------------
# 1) 사용자가 채워야 하는 설정
# ------------------------------------------------------------

# 캘리브레이션에 사용할 사진 (ROI·탐지와 같은 구도 권장)
IMAGE_PATH = Path("input/screenshots/01_wide_full_beach.png")
OUTPUT_DIR = Path("output/homography_density")

# ---- 기준점 4개 (픽셀 좌표) ----
# 이미지에서 찍은 (x, y). x=가로, y=세로. 왼쪽 위가 (0, 0)
# 네 점은 한 줄로 늘지 않게, 넓은 사각형처럼 잡으세요.
SRC_PIXELS = np.array(
    [
        [120.0, 80.0],   # 점 A: 예) 왼쪽 앞쪽 모래
        [900.0, 80.0],   # 점 B: 예) 오른쪽 앞쪽 모래
        [950.0, 180.0],  # 점 C: 예) 오른쪽 뒤쪽(물가 쪽)
        [80.0, 180.0],   # 점 D: 예) 왼쪽 뒤쪽(물가 쪽)
    ],
    dtype=np.float64,
)

# ---- 같은 4개의 실제 좌표 (미터) ----
# 임의 원점(0,0)을 하나 정한 뒤, 그로부터 가로=X, 세로=Y (미터)
# 아래는 "예시" 값입니다. 실제 측정값으로 반드시 교체하세요!
DST_METERS = np.array(
    [
        [0.0, 0.0],    # A → (0m, 0m)
        [40.0, 0.0],   # B → 오른쪽 40m
        [40.0, 25.0],  # C → 오른쪽 40m, 앞쪽(바다 방향) 25m
        [0.0, 25.0],   # D → 왼쪽, 앞쪽 25m
    ],
    dtype=np.float64,
)

# 밀도를 계산할 관심 구역(ROI) — 픽셀 다각형 (시계방향)
# 비어 있으면 이미지 전체 사각형을 사용합니다.
ROI_PIXEL_POLYGON = np.array(
    [
        [0.0, 57.0],     # 01_wide 기준: 대략 해변·물가 (y≈28%부터)
        [1024.0, 57.0],
        [1024.0, 204.0],
        [0.0, 204.0],
    ],
    dtype=np.float64,
)

# 사람 위치(픽셀). 실제로는 YOLO/SAHI 탐지 중심점을 넣으면 됩니다.
# 지금은 초보자 확인용 예시 좌표입니다.
PERSON_PIXELS = np.array(
    [
        [400.0, 120.0],
        [420.0, 125.0],
        [700.0, 110.0],
        [710.0, 140.0],
        [800.0, 160.0],
    ],
    dtype=np.float64,
)

# 위험 기준 (이전에 정한 값)
DENSITY_CAUTION = 4.0   # 명/m² 주의
DENSITY_DANGER = 6.0    # 명/m² 위험


# ------------------------------------------------------------
# 2) 호모그래피 만들기
# ------------------------------------------------------------
def build_homography(src_pixels: np.ndarray, dst_meters: np.ndarray):
    """
    픽셀 4점 → 미터 4점으로 변환 행렬 H 를 계산합니다.

    반환:
      H: 3x3 행렬.  [X, Y, 1]^T ~ H @ [x, y, 1]^T
      ok: 성공 여부
    """
    if src_pixels.shape != (4, 2) or dst_meters.shape != (4, 2):
        raise ValueError("기준점은 정확히 4개, 각 점은 (x,y) 이어야 합니다.")

    # method=0 은 기본(최소제곱). 점 4개면 보통 충분합니다.
    H, mask = cv2.findHomography(src_pixels, dst_meters, method=0)
    if H is None:
        raise RuntimeError("findHomography 실패. 기준점 배치/값을 확인하세요.")
    return H, mask


def pixels_to_meters(points_xy: np.ndarray, H: np.ndarray) -> np.ndarray:
    """
    여러 픽셀 점 (N,2) 을 미터 좌표 (N,2) 로 변환합니다.

    OpenCV perspectiveTransform 은 shape 를 (N,1,2) 로 받습니다.
    """
    pts = points_xy.reshape(-1, 1, 2).astype(np.float64)
    meters = cv2.perspectiveTransform(pts, H)
    return meters.reshape(-1, 2)


def polygon_area_m2(polygon_pixels: np.ndarray, H: np.ndarray) -> float:
    """
    픽셀 다각형을 미터로 변환한 뒤 면적(m²)을 구합니다.

    cv2.contourArea 는 꼭짓점 순서대로 이어진 다각형 면적을 계산합니다.
    (호모그래피 뒤 좌표계가 미터이므로 결과가 m²)
    """
    poly_m = pixels_to_meters(polygon_pixels, H)
    area = abs(cv2.contourArea(poly_m.astype(np.float32)))
    return float(area)


def pixel_footprint_m2(x: float, y: float, H: np.ndarray) -> float:
    """
    사진의 '한 픽셀'이 바닥에서 덮는 면적(m²)을 근사합니다.

    방법:
      픽셀 (x,y)의 네 모서리 (x,y)-(x+1,y)-(x+1,y+1)-(x,y+1)
      를 미터로 변환 → 그 사각형 면적.

    멀리 있는 픽셀일수록 보통 면적이 더 큽니다. (원근)
    """
    corners = np.array(
        [
            [x, y],
            [x + 1.0, y],
            [x + 1.0, y + 1.0],
            [x, y + 1.0],
        ],
        dtype=np.float64,
    )
    return polygon_area_m2(corners, H)


# ------------------------------------------------------------
# 3) 명/m² 계산
# ------------------------------------------------------------
def points_inside_polygon(points_xy: np.ndarray, polygon_xy: np.ndarray) -> np.ndarray:
    """
    각 점이 다각형 안에 있으면 True.
    cv2.pointPolygonTest: 내부>0, 경계=0, 외부<0
    """
    poly = polygon_xy.astype(np.float32)
    inside = []
    for x, y in points_xy:
        dist = cv2.pointPolygonTest(poly, (float(x), float(y)), measureDist=False)
        inside.append(dist >= 0)
    return np.array(inside, dtype=bool)


def compute_density_per_m2(
    person_pixels: np.ndarray,
    roi_polygon_pixels: np.ndarray,
    H: np.ndarray,
):
    """
    ROI 안 사람 수 / ROI 면적(m²) = 명/m²
    """
    area_m2 = polygon_area_m2(roi_polygon_pixels, H)
    if area_m2 <= 0:
        raise ValueError("ROI 면적이 0 이하입니다. 기준점/ROI를 확인하세요.")

    inside = points_inside_polygon(person_pixels, roi_polygon_pixels)
    count = int(inside.sum())
    density = count / area_m2
    return {
        "person_count_in_roi": count,
        "roi_area_m2": area_m2,
        "density_per_m2": density,
        "inside_mask": inside,
    }


def classify_density(density: float) -> str:
    """단순 3단계 판정."""
    if density >= DENSITY_DANGER:
        return "위험"
    if density >= DENSITY_CAUTION:
        return "주의"
    return "안전"


# ------------------------------------------------------------
# 4) 격자(그리드)별 명/m² — 위치마다 면적이 다름을 보여줌
# ------------------------------------------------------------
def grid_density_map(
    image_shape,
    person_pixels: np.ndarray,
    roi_polygon_pixels: np.ndarray,
    H: np.ndarray,
    cell_px: int = 40,
):
    """
    이미지를 cell_px 크기 칸으로 나누고,
    각 칸의 (사람 수 / 그 칸의 실제 m²) 를 계산합니다.

    반환:
      density_grid: (rows, cols) 명/m²
      area_grid: 각 칸 면적 m²
    """
    h, w = image_shape[:2]
    rows = max(1, h // cell_px)
    cols = max(1, w // cell_px)

    density_grid = np.zeros((rows, cols), dtype=np.float32)
    area_grid = np.zeros((rows, cols), dtype=np.float32)
    count_grid = np.zeros((rows, cols), dtype=np.int32)

    # 사람 → 어느 칸인지
    for x, y in person_pixels:
        if cv2.pointPolygonTest(
            roi_polygon_pixels.astype(np.float32), (float(x), float(y)), False
        ) < 0:
            continue
        c = min(cols - 1, int(x) // cell_px)
        r = min(rows - 1, int(y) // cell_px)
        count_grid[r, c] += 1

    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * cell_px, r * cell_px
            x1, y1 = min(w, x0 + cell_px), min(h, y0 + cell_px)
            cell_poly = np.array(
                [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                dtype=np.float64,
            )
            # 칸 중심이 ROI 밖이면 스킵
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if cv2.pointPolygonTest(
                roi_polygon_pixels.astype(np.float32), (cx, cy), False
            ) < 0:
                continue

            area = polygon_area_m2(cell_poly, H)
            area_grid[r, c] = area
            if area > 1e-6:
                density_grid[r, c] = count_grid[r, c] / area

    return density_grid, area_grid, count_grid, cell_px


def overlay_grid_density(image_bgr, density_grid, cell_px, alpha=0.45):
    """격자 명/m² 을 JET 색으로 겹칩니다. (시각 확인용)"""
    h, w = image_bgr.shape[:2]
    rows, cols = density_grid.shape
    heat = np.zeros((h, w), dtype=np.float32)

    vmax = float(density_grid.max()) if density_grid.max() > 0 else 1.0
    for r in range(rows):
        for c in range(cols):
            val = density_grid[r, c]
            if val <= 0:
                continue
            y0, x0 = r * cell_px, c * cell_px
            y1, x1 = min(h, y0 + cell_px), min(w, x0 + cell_px)
            heat[y0:y1, x0:x1] = val / vmax

    heat_u8 = (heat * 255).astype(np.uint8)
    color = cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET)
    active = heat > 0
    out = image_bgr.copy()
    out[active] = (
        (1 - alpha) * image_bgr[active].astype(np.float32)
        + alpha * color[active].astype(np.float32)
    ).astype(np.uint8)
    return out


def draw_calibration_and_people(image_bgr, src_pixels, person_pixels, roi_poly):
    """기준점·사람·ROI를 그려 초보자가 검수하기 쉽게 합니다."""
    out = image_bgr.copy()
    cv2.polylines(out, [roi_poly.astype(np.int32)], True, (0, 255, 255), 2)

    for i, (x, y) in enumerate(src_pixels):
        cv2.circle(out, (int(x), int(y)), 5, (255, 0, 0), -1)
        cv2.putText(
            out,
            f"P{i+1}",
            (int(x) + 4, int(y) - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 0, 0),
            1,
            cv2.LINE_AA,
        )

    for x, y in person_pixels:
        cv2.circle(out, (int(x), int(y)), 3, (0, 255, 0), -1)
    return out


# ------------------------------------------------------------
# 5) 메인
# ------------------------------------------------------------
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    image = cv2.imread(str(IMAGE_PATH))
    if image is None:
        raise FileNotFoundError(f"이미지를 찾을 수 없습니다: {IMAGE_PATH.resolve()}")

    h, w = image.shape[:2]
    print("=" * 60)
    print("픽셀 → 미터 호모그래피 + 명/m² 계산")
    print("=" * 60)
    print(f"이미지: {IMAGE_PATH} ({w}x{h})")
    print()
    print("[초보 체크]")
    print(" 1) SRC_PIXELS 4점이 사진에서 보이는 고정 위치에 맞는지")
    print(" 2) DST_METERS 가 실제 줄자로 잰 미터인지 (지금은 예시값!)")
    print(" 3) 네 점이 한 직선이 아닌지")
    print()

    # 1) 변환식 만들기
    H, _ = build_homography(SRC_PIXELS, DST_METERS)
    print("[1] findHomography 완료 (3x3 행렬 H)")
    print(H)
    print()

    # 2) 기준점이 미터로 잘 가는지 검증 (넣었던 DST와 비슷해야 함)
    check_m = pixels_to_meters(SRC_PIXELS, H)
    print("[2] 기준점 재변환 검증 (미터) - DST_METERS 와 거의 같아야 함")
    for i, ((sx, sy), (mx, my), (tx, ty)) in enumerate(
        zip(SRC_PIXELS, check_m, DST_METERS), start=1
    ):
        err = np.hypot(mx - tx, my - ty)
        print(
            f"  P{i}: px=({sx:.1f},{sy:.1f}) → m=({mx:.2f},{my:.2f}) "
            f"| 목표=({tx:.1f},{ty:.1f}) | 오차={err:.4f}m"
        )
    print()

    # 3) 픽셀 하나 면적이 위치에 따라 어떻게 다른지 예시
    sample_pixels = [(w // 4, int(h * 0.7)), (w // 2, int(h * 0.5)), (3 * w // 4, int(h * 0.35))]
    print("[3] 위치별 1픽셀이 덮는 면적(m^2) - 원근 때문에 값이 다름")
    for x, y in sample_pixels:
        a = pixel_footprint_m2(float(x), float(y), H)
        print(f"  픽셀({x},{y}) ~= {a:.6f} m^2")
    print()

    # 4) ROI 전체 명/m²
    roi = ROI_PIXEL_POLYGON.copy()
    result = compute_density_per_m2(PERSON_PIXELS, roi, H)
    level = classify_density(result["density_per_m2"])
    print("[4] ROI 밀도")
    print(f"  ROI 면적     : {result['roi_area_m2']:.2f} m^2")
    print(f"  ROI 안 인원  : {result['person_count_in_roi']} 명")
    print(f"  밀도         : {result['density_per_m2']:.4f} 명/m^2")
    print(f"  판정         : {level}  (주의>={DENSITY_CAUTION}, 위험>={DENSITY_DANGER})")
    print()

    # 5) 사람 좌표도 미터로 출력
    persons_m = pixels_to_meters(PERSON_PIXELS, H)
    print("[5] 사람 위치 (픽셀 -> 미터)")
    for i, ((px, py), (mx, my), inside) in enumerate(
        zip(PERSON_PIXELS, persons_m, result["inside_mask"]), start=1
    ):
        flag = "ROI안" if inside else "ROI밖"
        print(f"  사람{i}: px=({px:.1f},{py:.1f}) -> m=({mx:.2f},{my:.2f}) [{flag}]")
    print()

    # 6) 격자별 명/m² + 시각화 저장
    density_grid, area_grid, count_grid, cell_px = grid_density_map(
        image.shape, PERSON_PIXELS, roi, H, cell_px=40
    )
    overlay = overlay_grid_density(image, density_grid, cell_px)
    preview = draw_calibration_and_people(overlay, SRC_PIXELS, PERSON_PIXELS, roi)

    out_img = OUTPUT_DIR / f"{IMAGE_PATH.stem}_metric_density.jpg"
    cv2.imwrite(str(out_img), preview)

    # JSON으로 숫자 결과 저장 (앱/서버 연동용)
    summary = {
        "image": str(IMAGE_PATH),
        "note": "DST_METERS는 예시값일 수 있음. 실측 후 재계산 필요.",
        "roi_area_m2": result["roi_area_m2"],
        "person_count_in_roi": result["person_count_in_roi"],
        "density_per_m2": result["density_per_m2"],
        "level": level,
        "homography": H.tolist(),
        "src_pixels": SRC_PIXELS.tolist(),
        "dst_meters": DST_METERS.tolist(),
        "persons_meters": persons_m.tolist(),
        "grid_cell_px": cell_px,
        "grid_max_density_per_m2": float(density_grid.max()),
        "grid_max_cell_area_m2": float(area_grid.max()),
        "grid_min_positive_cell_area_m2": float(
            area_grid[area_grid > 0].min() if np.any(area_grid > 0) else 0.0
        ),
    }
    out_json = OUTPUT_DIR / f"{IMAGE_PATH.stem}_metric_density.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[6] 저장")
    print(f"  이미지: {out_img.resolve()}")
    print(f"  JSON  : {out_json.resolve()}")
    print()
    print("다음 단계: SRC_PIXELS / DST_METERS 를 현장 실측값으로 바꾼 뒤 다시 실행하세요.")


if __name__ == "__main__":
    main()
