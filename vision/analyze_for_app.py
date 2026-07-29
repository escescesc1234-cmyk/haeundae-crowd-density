# ============================================================
# Haeundae / Gwangalli crowd-density 앱 연동용 분석 CLI
# stdout 에는 JSON 만 출력합니다. (로그는 stderr)
#
# 예시:
#   python analyze_for_app.py --image input/screenshots/01_wide_full_beach.png
#   python analyze_for_app.py --image ... --zone-id GWANGALLI-ZONE-CENTER
# ============================================================

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction

# 같은 폴더의 호모그래피 유틸을 재사용합니다.
from homography_density import (
    DST_METERS,
    SRC_PIXELS,
    build_homography,
    compute_density_per_m2,
    pixels_to_meters,
    polygon_area_m2,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "output" / "app_bridge"
MODEL_NAME = "yolov8n.pt"
CONFIDENCE = 0.25
UPSCALE = 2.0
SLICE_SIZE = 256
OVERLAP_RATIO = 0.25
GAUSSIAN_KERNEL = 51
HEATMAP_ALPHA = 0.45
DEFAULT_ZONE_ID = "GWANGALLI-ZONE-CENTER"
CAMERA_ID = "vision-sahi-yolov8"


# 이미지 stem → ROI (정규화 0~1). density_heatmap.py 와 동일 계열
ROI_POLYGONS_NORM: dict[str, list[tuple[float, float]]] = {
    "01_wide_full_beach": [(0.0, 0.28), (1.0, 0.28), (1.0, 1.0), (0.0, 1.0)],
    "02_zone_left_bridge": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "03_zone_center_bridge": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "04_zone_right_bridge": [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)],
    "05_shore_sand_focus_left": [(0.0, 0.08), (1.0, 0.08), (1.0, 1.0), (0.0, 1.0)],
    "06_shore_sand_focus_center": [(0.0, 0.08), (1.0, 0.08), (1.0, 1.0), (0.0, 1.0)],
    "07_shore_sand_focus_right": [(0.0, 0.08), (1.0, 0.08), (1.0, 1.0), (0.0, 1.0)],
}


def eprint(*args):
    print(*args, file=sys.stderr)


def load_calibration(path: Path | None):
    """캘리브레이션 JSON이 있으면 사용, 없으면 모듈 예시값 + unverified."""
    if path and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        src = np.array(data["srcPixels"], dtype=np.float64)
        dst = np.array(data["dstMeters"], dtype=np.float64)
        verified = bool(data.get("fieldVerified", False))
        return src, dst, verified, str(path)
    return SRC_PIXELS.copy(), DST_METERS.copy(), False, None


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


def detect_people(model, image_bgr):
    result = get_sliced_prediction(
        image_bgr,
        model,
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


def centers_in_roi(preds, scale: float, roi_mask):
    h, w = roi_mask.shape[:2]
    centers = []
    scores = []
    for p in preds:
        cx = ((p.bbox.minx + p.bbox.maxx) / 2.0) / scale
        cy = ((p.bbox.miny + p.bbox.maxy) / 2.0) / scale
        ix, iy = int(round(cx)), int(round(cy))
        if ix < 0 or iy < 0 or ix >= w or iy >= h:
            continue
        if roi_mask[iy, ix] == 0:
            continue
        centers.append([float(cx), float(cy)])
        scores.append(float(p.score.value))
    return centers, scores


def build_heatmap(image_bgr, centers, roi_mask):
    h, w = image_bgr.shape[:2]
    density = np.zeros((h, w), dtype=np.float32)
    for x, y in centers:
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < w and 0 <= iy < h:
            density[iy, ix] += 1.0
    k = GAUSSIAN_KERNEL if GAUSSIAN_KERNEL % 2 == 1 else GAUSSIAN_KERNEL + 1
    density = cv2.GaussianBlur(density, (k, k), 0)
    density[roi_mask == 0] = 0
    out = image_bgr.copy()
    if density.max() <= 0:
        return out
    norm = density / density.max()
    heat = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)
    active = norm > 0.05
    out[active] = (
        (1.0 - HEATMAP_ALPHA) * out[active].astype(np.float32)
        + HEATMAP_ALPHA * heat[active].astype(np.float32)
    ).astype(np.uint8)
    out[roi_mask == 0] = image_bgr[roi_mask == 0]
    return out


def analyze_image(
    image_path: Path,
    zone_id: str,
    calibration_path: Path | None,
    output_dir: Path,
    use_homography_area: bool,
):
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"이미지를 읽을 수 없습니다: {image_path}")

    h, w = image.shape[:2]
    stem = image_path.stem
    polygon_norm = ROI_POLYGONS_NORM.get(
        stem, [(0.0, 0.25), (1.0, 0.25), (1.0, 1.0), (0.0, 1.0)]
    )
    roi_mask, roi_pts = make_roi_mask(h, w, polygon_norm)
    roi_polygon_px = roi_pts.astype(np.float64)

    eprint(f"[vision] model load: {MODEL_NAME}")
    model = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=str(ROOT / MODEL_NAME) if (ROOT / MODEL_NAME).exists() else MODEL_NAME,
        confidence_threshold=CONFIDENCE,
        device="cpu",
    )

    upscaled = upscale(image, UPSCALE)
    t0 = time.perf_counter()
    preds = detect_people(model, upscaled)
    elapsed = time.perf_counter() - t0
    centers, scores = centers_in_roi(preds, UPSCALE, roi_mask)
    person_count = len(centers)
    mean_conf = float(np.mean(scores)) if scores else 0.0

    src, dst, field_verified, calib_used = load_calibration(calibration_path)
    H, _ = build_homography(src, dst)
    persons_m = pixels_to_meters(np.array(centers, dtype=np.float64), H) if centers else np.zeros((0, 2))

    metric = compute_density_per_m2(
        np.array(centers, dtype=np.float64) if centers else np.zeros((0, 2)),
        roi_polygon_px,
        H,
    )
    roi_area_m2 = float(metric["roi_area_m2"])
    density_per_m2 = float(metric["density_per_m2"])

    output_dir.mkdir(parents=True, exist_ok=True)
    heatmap = build_heatmap(image, centers, roi_mask)
    cv2.polylines(heatmap, [roi_pts], True, (0, 255, 255), 2)
    for x, y in centers:
        cv2.circle(heatmap, (int(x), int(y)), 3, (255, 255, 255), -1)
    heatmap_name = f"{stem}_app_heatmap.jpg"
    heatmap_path = output_dir / heatmap_name
    cv2.imwrite(str(heatmap_path), heatmap)

    measured_at = datetime.now(timezone.utc).isoformat()

    # 앱 DensityInput / CctvFramePayload 호환 필드
    payload = {
        "zoneId": zone_id,
        "measuredAt": measured_at,
        "detectedPeople": person_count,
        "confidence": round(mean_conf, 4),
        "dataSource": "vision_yolo_sahi",
        "isTestData": True,
        "cameras": [
            {
                "cameraId": CAMERA_ID,
                "detectedPeople": person_count,
                "confidence": round(mean_conf, 4),
                "measuredAt": measured_at,
            }
        ],
        "vision": {
            "imagePath": str(image_path.resolve()),
            "imageStem": stem,
            "rawDetections": len(preds),
            "roiPersonCount": person_count,
            "meanConfidence": round(mean_conf, 4),
            "inferenceSeconds": round(elapsed, 3),
            "upscale": UPSCALE,
            "sliceSize": SLICE_SIZE,
            "overlapRatio": OVERLAP_RATIO,
            "roiAreaM2Homography": roi_area_m2,
            "densityPerM2Homography": density_per_m2,
            "homographyFieldVerified": field_verified,
            "calibrationPath": calib_used,
            "heatmapPath": str(heatmap_path.resolve()),
            "heatmapRelativePath": f"vision/output/app_bridge/{heatmap_name}",
            "personCentersPixels": centers,
            "personCentersMeters": persons_m.tolist(),
            "note": (
                "호모그래피 면적은 fieldVerified=true 일 때만 앱 유효면적으로 사용하세요. "
                "미검증이면 앱 구역 카탈로그 면적을 사용합니다."
            ),
        },
    }

    # 실측 캘리브레이션이 검증된 경우에만 앱에 면적을 넘김
    if use_homography_area and field_verified and roi_area_m2 > 0:
        payload["effectiveAreaSquareMeters"] = roi_area_m2

    return payload


def main():
    parser = argparse.ArgumentParser(description="Vision bridge for haeundae-crowd-density")
    parser.add_argument("--image", required=True, help="분석할 이미지 경로")
    parser.add_argument("--zone-id", default=DEFAULT_ZONE_ID)
    parser.add_argument(
        "--calibration",
        default=str(ROOT / "config" / "calibration.json"),
        help="호모그래피 캘리브레이션 JSON 경로",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--use-homography-area",
        action="store_true",
        help="fieldVerified 캘리브레이션일 때 ROI m2 를 앱에 전달",
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_absolute():
        # vision/ 또는 프로젝트 루트 기준 모두 허용
        candidates = [Path.cwd() / image_path, ROOT / image_path, ROOT.parent / image_path]
        for c in candidates:
            if c.exists():
                image_path = c
                break

    calib = Path(args.calibration)
    if not calib.is_absolute():
        for c in [Path.cwd() / calib, ROOT / calib, ROOT / "config" / calib.name]:
            if c.exists():
                calib = c
                break

    try:
        payload = analyze_image(
            image_path=image_path,
            zone_id=args.zone_id,
            calibration_path=calib if calib.exists() else None,
            output_dir=Path(args.output_dir),
            use_homography_area=bool(args.use_homography_area),
        )
    except Exception as exc:
        eprint(f"[vision] ERROR: {exc}")
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)

    # stdout = JSON only (Node 어댑터가 파싱)
    print(json.dumps({"ok": True, **payload}, ensure_ascii=False))


if __name__ == "__main__":
    main()
