"""라벨 교정 완료 후 train/val 분할 + data.yaml 생성 + 학습용 zip 패키징.

입력: finetune/dataset/images/*.jpg, finetune/dataset/labels/*.txt (교정 완료)
출력: finetune/dataset/{train,val}/{images,labels}/ + data.yaml + gwangalli_dataset.zip

zip을 Google Drive에 올린 뒤 Colab 노트북에서 학습한다.

사용:
    python finetune/make_dataset.py                # val 20%
    python finetune/make_dataset.py --val 0.15 --seed 42
"""
from __future__ import annotations

import argparse
import random
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "finetune" / "dataset"


def main():
    ap = argparse.ArgumentParser(description="Split dataset and package for Colab")
    ap.add_argument("--root", default=str(DATASET))
    ap.add_argument("--val", type=float, default=0.2, help="검증 비율")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    root = Path(args.root)
    img_dir = root / "images"
    lbl_dir = root / "labels"
    images = sorted(img_dir.glob("*.jpg"))
    # 라벨 파일이 존재하는 이미지만 사용(빈 라벨=사람 없음도 유효 → 허용)
    pairs = [(im, lbl_dir / (im.stem + ".txt")) for im in images
             if (lbl_dir / (im.stem + ".txt")).exists()]
    if not pairs:
        print(f"[dataset] no labeled pairs in {root}. prelabel/교정 먼저.")
        return

    random.Random(args.seed).shuffle(pairs)
    n_val = max(1, int(len(pairs) * args.val))
    splits = {"val": pairs[:n_val], "train": pairs[n_val:]}

    for split, items in splits.items():
        (root / split / "images").mkdir(parents=True, exist_ok=True)
        (root / split / "labels").mkdir(parents=True, exist_ok=True)
        for im, lb in items:
            shutil.copy2(im, root / split / "images" / im.name)
            shutil.copy2(lb, root / split / "labels" / lb.name)
    print(f"[dataset] train={len(splits['train'])} val={len(splits['val'])}")

    # data.yaml (Colab에서 dataset 루트 기준 상대경로)
    data_yaml = (
        "path: .\n"
        "train: train/images\n"
        "val: val/images\n"
        "names:\n"
        "  0: person\n"
    )
    (root / "data.yaml").write_text(data_yaml, encoding="utf-8")
    print(f"[dataset] wrote {root / 'data.yaml'}")

    # zip: train/, val/, data.yaml
    zip_path = ROOT / "finetune" / "gwangalli_dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for split in ("train", "val"):
            for sub in ("images", "labels"):
                for f in (root / split / sub).glob("*"):
                    z.write(f, f"{split}/{sub}/{f.name}")
        z.write(root / "data.yaml", "data.yaml")
    print(f"[dataset] packaged → {zip_path}")
    print("[dataset] 이 zip을 Google Drive에 올리고 Colab 노트북을 실행하세요.")


if __name__ == "__main__":
    main()
