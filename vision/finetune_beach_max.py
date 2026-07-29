# ============================================================
# 해변 스크린샷 전체로 의사라벨 파인튜닝 (정확도 맥스용)
#
# - input/screenshots/*.png 전부 사용
# - SAHI(yolov8m)로 고품질 의사라벨 생성 (conf>=0.25)
# - yolov8m 을 짧게 파인튜닝 → models/yolov8m_beach_ft.pt
# - realtime_safety_map 이 이 가중치를 우선 로드
# ============================================================

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent
SCREEN_DIR = ROOT / "input" / "screenshots"
DATASET_DIR = ROOT / "datasets" / "beach_person_ft_max"
OUT_WEIGHTS = ROOT / "models" / "yolov8m_beach_ft.pt"
LABEL_MODEL = ROOT / "yolov8m.pt"
TRAIN_BASE = ROOT / "yolov8m.pt"

UPSCALE = 2.0
SLICE = 256
OVERLAP = 0.25
LABEL_CONF = 0.25
EPOCHS = 12
IMGSZ = 640
BATCH = 2
ROI = [(0.0, 0.45), (1.0, 0.45), (1.0, 1.0), (0.0, 1.0)]


def eprint(*args):
    print(*args, file=sys.stderr)


def resolve_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "0"
    except Exception:
        pass
    return "cpu"


def ensure_weights(path: Path, name: str) -> Path:
    if path.exists():
        return path
    eprint(f"[download] {name}")
    YOLO(name)  # triggers download into CWD
    local = ROOT / name
    return local if local.exists() else Path(name)


def in_roi(cx: float, cy: float, w: int, h: int) -> bool:
    # LIVE_ROI 하단 해변만
    return cy >= 0.45 * h


def auto_label(det, image_path: Path, labels_dir: Path, images_dir: Path) -> int:
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    h, w = image.shape[:2]
    up = cv2.resize(
        image, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC
    )
    result = get_sliced_prediction(
        up,
        det,
        slice_height=SLICE,
        slice_width=SLICE,
        overlap_height_ratio=OVERLAP,
        overlap_width_ratio=OVERLAP,
        verbose=0,
    )
    lines = []
    for p in result.object_prediction_list:
        if str(p.category.name).lower() != "person":
            continue
        if float(p.score.value) < LABEL_CONF:
            continue
        x1 = p.bbox.minx / UPSCALE
        y1 = p.bbox.miny / UPSCALE
        x2 = p.bbox.maxx / UPSCALE
        y2 = p.bbox.maxy / UPSCALE
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        if not in_roi(cx, cy, w, h):
            continue
        lines.append(
            "0 "
            f"{np.clip(cx / w, 0, 1):.6f} "
            f"{np.clip(cy / h, 0, 1):.6f} "
            f"{np.clip(bw / w, 1e-6, 1):.6f} "
            f"{np.clip(bh / h, 1e-6, 1):.6f}"
        )

    stem = image_path.stem
    cv2.imwrite(str(images_dir / f"{stem}.jpg"), image)
    (labels_dir / f"{stem}.txt").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )
    return len(lines)


def main():
    images = sorted(SCREEN_DIR.glob("*.png")) + sorted(SCREEN_DIR.glob("*.jpg"))
    if len(images) < 3:
        eprint(f"[오류] 학습 이미지가 부족합니다: {SCREEN_DIR}")
        sys.exit(1)

    label_w = ensure_weights(LABEL_MODEL, "yolov8m.pt")
    train_w = ensure_weights(TRAIN_BASE, "yolov8m.pt")
    device = resolve_device()

    if DATASET_DIR.exists():
        shutil.rmtree(DATASET_DIR)
    train_img = DATASET_DIR / "images" / "train"
    train_lbl = DATASET_DIR / "labels" / "train"
    val_img = DATASET_DIR / "images" / "val"
    val_lbl = DATASET_DIR / "labels" / "val"
    for d in (train_img, train_lbl, val_img, val_lbl):
        d.mkdir(parents=True, exist_ok=True)

    eprint(f"[1] SAHI 의사라벨 생성 ({len(images)}장, conf>={LABEL_CONF})")
    det = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=str(label_w),
        confidence_threshold=LABEL_CONF,
        device=("cuda:0" if device != "cpu" else "cpu"),
    )
    total = 0
    for path in images:
        n = auto_label(det, path, train_lbl, train_img)
        total += n
        eprint(f"  - {path.name}: {n}")
        shutil.copy2(train_img / f"{path.stem}.jpg", val_img / f"{path.stem}.jpg")
        shutil.copy2(train_lbl / f"{path.stem}.txt", val_lbl / f"{path.stem}.txt")
    eprint(f"  합계 박스={total}")

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

    eprint(f"[2] Fine-tune yolov8m epochs={EPOCHS} device={device}")
    model = YOLO(str(train_w))
    model.train(
        data=str(data_yaml),
        epochs=EPOCHS,
        imgsz=IMGSZ,
        batch=BATCH,
        project=str(ROOT / "runs" / "detect"),
        name="beach_ft_max",
        exist_ok=True,
        patience=8,
        device=device,
        verbose=True,
        # 소량 데이터: 초반 백본 고정에 가까운 효과
        freeze=10,
    )
    best = ROOT / "runs" / "detect" / "beach_ft_max" / "weights" / "best.pt"
    if not best.exists():
        eprint(f"[오류] best.pt 없음: {best}")
        sys.exit(1)
    OUT_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, OUT_WEIGHTS)
    eprint(f"[3] 저장: {OUT_WEIGHTS}")
    eprint("realtime_safety_map 재시작 시 이 가중치를 자동 우선 사용합니다.")


if __name__ == "__main__":
    main()
