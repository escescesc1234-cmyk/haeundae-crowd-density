# ============================================================
# 새 해변 사진으로 YOLOv8 추가 학습(fine-tune) 후 안전지도 생성
#
# [초보용 설명]
# - 진짜 지도 학습에는 "사람 박스 라벨"이 필요합니다.
# - 라벨이 아직 없으므로, 기존 모델(SAHI)로 박스를 자동 생성하고
#   그 결과로 짧게 추가 학습합니다. (= 의사라벨 / pseudo-label fine-tune)
# - 학습이 끝나면 새 가중치로 동일 규칙의지도를 만듭니다.
# ============================================================

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction
from ultralytics import YOLO

from safety_map import (
    CELL_PX,
    COLOR_CAUTION,
    COLOR_DANGER,
    COLOR_SAFE,
    OVERLAY_ALPHA,
    THRESH_CAUTION,
    THRESH_DANGER,
    UPSCALE,
    SLICE_SIZE,
    OVERLAP_RATIO,
    CONFIDENCE,
    build_density_grid_per_m2,
    build_warning_messages,
    count_danger_cells,
    draw_legend,
    draw_warning_banners,
    make_roi_mask,
    render_safety_map,
    upscale,
)
from homography_density import SRC_PIXELS, DST_METERS, build_homography


ROOT = Path(__file__).resolve().parent
NEW_IMAGES = [
    ROOT / "input" / "screenshots" / "08_new_zone_left.png",
    ROOT / "input" / "screenshots" / "09_new_zone_center.png",
    ROOT / "input" / "screenshots" / "10_new_zone_right.png",
]

DATASET_DIR = ROOT / "datasets" / "beach_person_ft"
RUNS_DIR = ROOT / "runs" / "detect"
OUTPUT_DIR = ROOT / "output" / "safety_map_finetuned"
BASE_WEIGHTS = ROOT / "yolov8n.pt"
FINETUNED_WEIGHTS = ROOT / "models" / "yolov8n_beach_ft.pt"

# 소량 데이터라 epoch 은 짧게 (과적합 주의)
EPOCHS = 15
IMGSZ = 640
BATCH = 2

# 새 사진 ROI: 하늘·다리 제외, 해변·물가
NEW_ROI = [(0.0, 0.48), (1.0, 0.48), (1.0, 1.0), (0.0, 1.0)]


def eprint(*args):
    print(*args, file=sys.stderr)


def ensure_base_weights() -> Path:
    if BASE_WEIGHTS.exists():
        return BASE_WEIGHTS
    # ultralytics 가 자동 다운로드하도록 이름만 반환
    return Path("yolov8n.pt")


def auto_label_image(detection_model, image_path: Path, labels_dir: Path, images_dir: Path):
    """
    SAHI로 person 박스를 찾아 YOLO txt 라벨로 저장합니다.
    형식: class x_center y_center width height  (모두 0~1 정규화)
    """
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    h, w = image.shape[:2]

    up = upscale(image, UPSCALE)
    result = get_sliced_prediction(
        up,
        detection_model,
        slice_height=SLICE_SIZE,
        slice_width=SLICE_SIZE,
        overlap_height_ratio=OVERLAP_RATIO,
        overlap_width_ratio=OVERLAP_RATIO,
        verbose=0,
    )

    lines = []
    for p in result.object_prediction_list:
        if str(p.category.name).lower() != "person":
            continue
        # 확대본 좌표 → 원본 좌표
        x1 = p.bbox.minx / UPSCALE
        y1 = p.bbox.miny / UPSCALE
        x2 = p.bbox.maxx / UPSCALE
        y2 = p.bbox.maxy / UPSCALE
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)
        cx = (x1 + x2) / 2.0 / w
        cy = (y1 + y2) / 2.0 / h
        nw = bw / w
        nh = bh / h
        # 범위 클램프
        cx = float(np.clip(cx, 0, 1))
        cy = float(np.clip(cy, 0, 1))
        nw = float(np.clip(nw, 1e-6, 1))
        nh = float(np.clip(nh, 1e-6, 1))
        lines.append(f"0 {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

    # train 폴더에 복사
    stem = image_path.stem
    out_img = images_dir / f"{stem}.jpg"
    cv2.imwrite(str(out_img), image)
    (labels_dir / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def prepare_dataset(image_paths: list[Path]) -> Path:
    """의사라벨 데이터셋 폴더를 만듭니다."""
    if DATASET_DIR.exists():
        shutil.rmtree(DATASET_DIR)

    train_images = DATASET_DIR / "images" / "train"
    train_labels = DATASET_DIR / "labels" / "train"
    val_images = DATASET_DIR / "images" / "val"
    val_labels = DATASET_DIR / "labels" / "val"
    for d in [train_images, train_labels, val_images, val_labels]:
        d.mkdir(parents=True, exist_ok=True)

    eprint("[1] 기존 모델로 자동 라벨 생성 (pseudo-label)")
    det = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=str(ensure_base_weights()),
        confidence_threshold=CONFIDENCE,
        device="cpu",
    )

    counts = []
    for i, path in enumerate(image_paths):
        if not path.exists():
            raise FileNotFoundError(path)
        n = auto_label_image(det, path, train_labels, train_images)
        counts.append((path.name, n))
        eprint(f"  - {path.name}: person 박스 {n}개")

        # 데이터가 적어서 같은 이미지를 val 에도 복사 (형식만 맞춤)
        shutil.copy2(train_images / f"{path.stem}.jpg", val_images / f"{path.stem}.jpg")
        shutil.copy2(train_labels / f"{path.stem}.txt", val_labels / f"{path.stem}.txt")

    data_yaml = DATASET_DIR / "data.yaml"
    data_yaml.write_text(
        yaml.dump(
            {
                "path": str(DATASET_DIR.resolve()),
                "train": "images/train",
                "val": "images/val",
                "names": {0: "person"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return data_yaml


def finetune(data_yaml: Path) -> Path:
    """yolov8n 을 해변 의사라벨로 추가 학습하고 best.pt 를 복사합니다."""
    eprint(f"[2] Fine-tune 시작 (epochs={EPOCHS}, imgsz={IMGSZ})")
    model = YOLO(str(ensure_base_weights()))
    model.train(
        data=str(data_yaml),
        epochs=EPOCHS,
        imgsz=IMGSZ,
        batch=BATCH,
        project=str(ROOT / "runs" / "detect"),
        name="beach_ft",
        exist_ok=True,
        patience=10,
        device="cpu",
        verbose=True,
    )

    best = ROOT / "runs" / "detect" / "beach_ft" / "weights" / "best.pt"
    if not best.exists():
        # 일부 버전은 last.pt 만 남김
        best = ROOT / "runs" / "detect" / "beach_ft" / "weights" / "last.pt"
    if not best.exists():
        raise FileNotFoundError("학습 가중치를 찾지 못했습니다.")

    FINETUNED_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, FINETUNED_WEIGHTS)
    eprint(f"[2] 저장: {FINETUNED_WEIGHTS}")
    return FINETUNED_WEIGHTS


def detect_centers_with_weights(weights: Path, image_bgr, roi_mask):
    det = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=str(weights),
        confidence_threshold=CONFIDENCE,
        device="cpu",
    )
    up = upscale(image_bgr, UPSCALE)
    result = get_sliced_prediction(
        up,
        det,
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
        cx = ((p.bbox.minx + p.bbox.maxx) / 2.0) / UPSCALE
        cy = ((p.bbox.miny + p.bbox.maxy) / 2.0) / UPSCALE
        ix, iy = int(round(cx)), int(round(cy))
        if 0 <= ix < w and 0 <= iy < h and roi_mask[iy, ix] > 0:
            centers.append((cx, cy))
    return centers


def make_safety_with_finetuned(
    weights: Path,
    image_paths: list[Path],
    fallback_weights: Path | None = None,
):
    """업데이트된 모델로 안전지도 JPG 생성. 탐지 0이면 fallback 가중치 사용."""
    eprint("[3] 업데이트 모델로 안전지도 생성")
    H, _ = build_homography(SRC_PIXELS, DST_METERS)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fallback = Path(fallback_weights) if fallback_weights else ensure_base_weights()

    results = []
    for path in image_paths:
        image = cv2.imread(str(path))
        h, w = image.shape[:2]
        roi_mask, _ = make_roi_mask(h, w, NEW_ROI)
        centers = detect_centers_with_weights(weights, image, roi_mask)
        used = Path(weights)
        if len(centers) == 0 and fallback.resolve() != used.resolve():
            eprint(f"  ! {path.name}: fine-tune 탐지 0 → 기본 가중치로 재시도")
            centers = detect_centers_with_weights(fallback, image, roi_mask)
            used = fallback

        density, counts, _ = build_density_grid_per_m2(
            image.shape, centers, roi_mask, H, CELL_PX
        )
        safety = render_safety_map(image, density, CELL_PX, OVERLAY_ALPHA)
        safety = draw_legend(safety)
        danger_cells = count_danger_cells(density)
        alerts = build_warning_messages(danger_cells)
        if alerts["hasDanger"]:
            eprint(f"[경고][관광객] {alerts['touristMessage']}")
            eprint(f"[경고][관리자] {alerts['managerMessage']}")
            safety = draw_warning_banners(safety, alerts)

        out = OUTPUT_DIR / f"{path.stem}_safety_map_ft.jpg"
        cv2.imwrite(str(out), safety)

        preview = image.copy()
        for x, y in centers:
            cv2.circle(preview, (int(x), int(y)), 4, (0, 255, 255), -1)
        cv2.imwrite(str(OUTPUT_DIR / f"{path.stem}_detections_ft.jpg"), preview)

        meta = {
            "image": str(path),
            "weights": str(used),
            "finetunedWeights": str(weights),
            "output": str(out),
            "personCount": len(centers),
            "maxDensityPerM2": float(np.nanmax(density)) if np.any(~np.isnan(density)) else 0.0,
            "thresholds": {"caution": THRESH_CAUTION, "danger": THRESH_DANGER},
            "colors": {
                "safe": "#00FF00",
                "caution": "#FFFF00",
                "danger": "#FF0000",
                "alpha": OVERLAY_ALPHA,
            },
            "alerts": alerts,
            "note": (
                "새 사진은 datasets/beach_person_ft 에 의사라벨로 추가됨. "
                "샘플이 매우 적으면 fine-tune 후 탐지가 붕괴할 수 있어 기본 가중치로 fallback 합니다."
            ),
        }
        (OUTPUT_DIR / f"{path.stem}_safety_map_ft.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results.append(meta)
        eprint(f"  - {path.name}: {len(centers)}명 → {out.name} (weights={used.name})")

    return results


def main():
    missing = [p for p in NEW_IMAGES if not p.exists()]
    if missing:
        eprint("[오류] 새 사진이 없습니다:")
        for p in missing:
            eprint(f"  {p}")
        sys.exit(1)

    eprint("=" * 60)
    eprint("추가 학습(pseudo-label fine-tune) + 안전지도")
    eprint("=" * 60)
    eprint(f"입력 사진 {len(NEW_IMAGES)}장")
    for p in NEW_IMAGES:
        eprint(f"  - {p.name}")

    data_yaml = prepare_dataset(NEW_IMAGES)
    weights = finetune(data_yaml)
    results = make_safety_with_finetuned(
        weights, NEW_IMAGES, fallback_weights=ensure_base_weights()
    )

    print("-" * 64)
    print(f"{'파일':<28} {'인원':>6} {'최대명/m2':>10} {'출력'}")
    print("-" * 64)
    for r in results:
        print(
            f"{Path(r['image']).name:<28} {r['personCount']:>6} "
            f"{r['maxDensityPerM2']:>10.4f} {Path(r['output']).name}"
        )
    print("-" * 64)
    print(f"업데이트 모델: {FINETUNED_WEIGHTS}")
    print(f"결과 폴더    : {OUTPUT_DIR}")
    print("최종 출력    : *_safety_map_ft.jpg")


if __name__ == "__main__":
    main()
