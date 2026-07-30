"""INT8(OpenVINO) 추론만 별도 측정. 기본 보정셋(coco8) 사용."""
from __future__ import annotations

import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent
FRAME = ROOT / "output" / "bench_frame.jpg"
IMGSZ = 960
CONF = 0.15
RUNS = 3

MODELS = ["yolov8m.pt", "yolo11s.pt", "yolo26s.pt"]


def bench(label, model_path, img):
    from ultralytics import YOLO

    m = YOLO(model_path)
    m.predict(img, classes=[0], conf=CONF, imgsz=IMGSZ, verbose=False)
    t0 = time.perf_counter()
    for _ in range(RUNS):
        r = m.predict(img, classes=[0], conf=CONF, imgsz=IMGSZ, verbose=False)[0]
    ms = (time.perf_counter() - t0) / RUNS * 1000.0
    n = len(r.boxes) if r.boxes is not None else 0
    print(f"{label:<28}{ms:>9.1f}ms  boxes={n}")
    return ms


def main():
    from ultralytics import YOLO

    img = cv2.imread(str(FRAME))
    print(f"frame={img.shape} imgsz={IMGSZ}\n")
    for name in MODELS:
        try:
            base = bench(f"{name} PyTorch-CPU", name, img)
            m = YOLO(name)
            # 기본 coco8 보정 (data 생략) → INT8
            ov = m.export(format="openvino", int8=True, imgsz=IMGSZ)
            ms = bench(f"{name} OpenVINO-INT8", str(ov), img)
            print(f"    → {base / ms:.2f}x vs PyTorch\n")
        except Exception as exc:
            print(f"[skip] {name}: {exc}\n")


if __name__ == "__main__":
    main()
