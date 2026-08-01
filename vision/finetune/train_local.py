"""광안리 beach-ft 로컬 학습 (CUDA GPU 권장).

CPU에서는 비현실적으로 느려 Colab(train_yolo26_colab.ipynb)을 권장.
GPU가 있으면 이 스크립트로 models/yolo26s_beach_ft.pt 를 갱신할 수 있다.

사용:
  python finetune/make_dataset.py          # train/val + zip 갱신
  python finetune/train_local.py           # GPU 학습
  python finetune/train_local.py --epochs 30 --imgsz 960
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent
DATASET_YAML = ROOT / "finetune" / "dataset" / "data.yaml"
OUT_DIR = ROOT / "finetune" / "runs" / "beach_ft"
DEPLOY = ROOT / "models" / "yolo26s_beach_ft.pt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATASET_YAML))
    ap.add_argument("--base", default="yolo26s.pt", help="시작 가중치")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default=None, help="0 / cpu (기본=자동)")
    ap.add_argument("--name", default="yolo26s_beach")
    ap.add_argument("--deploy", action="store_true", default=True,
                    help="best.pt 를 models/yolo26s_beach_ft.pt 로 복사")
    ap.add_argument("--no-deploy", dest="deploy", action="store_false")
    args = ap.parse_args()

    if not Path(args.data).exists():
        raise SystemExit(f"[train] data.yaml 없음: {args.data} (make_dataset.py 먼저)")

    device = args.device
    if device is None:
        device = "0" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("[train] WARNING: CPU 학습은 수 시간~수십 시간 걸릴 수 있습니다.")
        print("[train] Google Colab GPU 노트북(finetune/train_yolo26_colab.ipynb)을 권장합니다.")

    print(f"[train] base={args.base} data={args.data} device={device} "
          f"epochs={args.epochs} imgsz={args.imgsz}")
    model = YOLO(args.base)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        project=str(OUT_DIR.parent),
        name=args.name,
        exist_ok=True,
        patience=15,
        close_mosaic=10,
        hsv_h=0.015,
        hsv_s=0.5,
        hsv_v=0.3,
        degrees=5.0,
        translate=0.08,
        scale=0.4,
        fliplr=0.5,
        mosaic=0.8,
    )
    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.exists():
        raise SystemExit(f"[train] best.pt 없음: {best}")
    print(f"[train] best={best}")
    if args.deploy:
        DEPLOY.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, DEPLOY)
        print(f"[train] deployed → {DEPLOY}")
        print("[train] 서버 UI의 '모델 재적용' 또는 재시작으로 반영하세요.")


if __name__ == "__main__":
    main()
