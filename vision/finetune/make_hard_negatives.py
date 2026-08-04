"""하드 네거티브(배경) 프레임 생성 — Ultralytics 권장 5~10%% FP 감소법.

물 구역만 있는 프레임 / 파도·거품 위주 프레임을 labels 없이 복사해
학습 시 '여기는 사람·튜브 없음'으로 가르친다.

사용:
  python finetune/make_hard_negatives.py --count 120
  # → finetune/dataset/images 에 bg_*.jpg + 빈 labels/bg_*.txt
  # 이후 make_dataset.py 또는 ZIP → Colab 재학습
"""
from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "finetune" / "raw"
IMG = ROOT / "finetune" / "dataset" / "images"
LBL = ROOT / "finetune" / "dataset" / "labels"

WATER_Y_TOP, WATER_Y_BOT = 0.45, 0.78


def foam_score(img) -> float:
    h, w = img.shape[:2]
    # 빠른 스캔용 축소
    if w > 480:
        scale = 480.0 / w
        img = cv2.resize(img, (480, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    y0, y1 = int(WATER_Y_TOP * h), int(WATER_Y_BOT * h)
    patch = img[y0:y1, :]
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    return float(np.mean((v >= 0.65) & (s <= 0.32)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=120, help="추가할 배경 장수")
    ap.add_argument("--min-foam", type=float, default=0.12)
    ap.add_argument(
        "--max-scan",
        type=int,
        default=400,
        help="foam 스캔할 raw 최대 장수 (속도)",
    )
    ap.add_argument(
        "--replace",
        action="store_true",
        help="기존 bg_hardneg_* 덮어쓰기 (기본은 건너뛰고 이어쓰기)",
    )
    args = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    LBL.mkdir(parents=True, exist_ok=True)

    existing = sorted(IMG.glob("bg_hardneg_*.jpg"))
    start_i = 0
    if existing and not args.replace:
        # 이미 충분하면 스킵
        if len(existing) >= args.count:
            print(f"[hardneg] already have {len(existing)} bg_hardneg_* - skip")
            return
        start_i = len(existing)
        need = args.count - start_i
        print(f"[hardneg] keep {start_i} existing, add {need} more")
    else:
        need = args.count

    paths = sorted(RAW.glob("*.jpg"))
    if not paths:
        print("[hardneg] no raw/*.jpg — abort")
        return
    # 균등 샘플로 스캔량 제한
    if len(paths) > args.max_scan:
        step = max(1, len(paths) // args.max_scan)
        paths = paths[::step][: args.max_scan]

    cands = []
    for i, p in enumerate(paths):
        im = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if im is None:
            continue
        sc = foam_score(im)
        if sc >= args.min_foam:
            cands.append((sc, p))
        if (i + 1) % 50 == 0:
            print(f"[hardneg] scanned {i + 1}/{len(paths)} foam_hits={len(cands)}")
    cands.sort(key=lambda x: -x[0])
    if not cands:
        cands = [(0.0, p) for p in paths[: max(need * 2, 20)]]
    random.Random(42).shuffle(cands)
    pick = cands[:need]
    n = 0
    for j, (_sc, src) in enumerate(pick):
        idx = start_i + j
        name = f"bg_hardneg_{idx:04d}.jpg"
        dst_i = IMG / name
        dst_l = LBL / (Path(name).stem + ".txt")
        shutil.copy2(src, dst_i)
        dst_l.write_text("", encoding="utf-8")  # 빈 라벨 = 전부 배경
        n += 1
    total = len(list(IMG.glob("*.jpg")))
    print(f"[hardneg] added {n} background images (empty labels)")
    print(f"[hardneg] dataset images now ≈ {total} (배경 권장 5~10%)")
    print("[hardneg] 다음: make_dataset.py 또는 웹 ZIP → Colab 재학습")


if __name__ == "__main__":
    main()
