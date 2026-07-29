# GPU/CPU 추론 벤치마크 (동일 스크린샷, person 클래스)
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "device_benchmark.json"


def bench(label: str, model_path: str, device, img, runs: int = 3) -> dict:
    from ultralytics import YOLO

    m = YOLO(model_path)
    m.predict(img, classes=[0], conf=0.08, imgsz=640, verbose=False, device=device)
    t0 = time.perf_counter()
    for _ in range(runs):
        m.predict(img, classes=[0], conf=0.08, imgsz=640, verbose=False, device=device)
    ms = (time.perf_counter() - t0) / runs * 1000.0
    r = m.predict(img, classes=[0], conf=0.08, imgsz=640, verbose=False, device=device)[0]
    n = len(r.boxes) if r.boxes is not None else 0
    return {"label": label, "model": model_path, "device": str(device), "msPerInfer": ms, "personBoxes": n}


def main():
    imgs = sorted(ROOT.glob("input/screenshots/*.png"))
    if not imgs:
        print("no screenshots", file=sys.stderr)
        sys.exit(1)
    img = cv2.imread(str(imgs[0]))

    import torch

    report: dict = {
        "torch": torch.__version__,
        "cudaAvailable": bool(torch.cuda.is_available()),
        "cudaDevice": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "benchmarks": [],
        "errors": [],
    }
    try:
        import openvino as ov

        report["openvinoDevices"] = ov.Core().available_devices
    except Exception as exc:
        report["openvinoDevices"] = f"unavailable: {exc}"

    trials = [
        ("PyTorch CPU yolov8m", "yolov8m.pt", "cpu"),
    ]
    if torch.cuda.is_available():
        trials.append(("PyTorch CUDA yolov8m", "yolov8m.pt", "cuda:0"))
    ov_dir = ROOT / "yolov8m_openvino_model"
    if ov_dir.exists():
        trials.append(("OpenVINO CPU yolov8m", str(ov_dir), "cpu"))
        trials.append(("OpenVINO GPU yolov8m", str(ov_dir), "intel:gpu"))

    for label, path, dev in trials:
        try:
            report["benchmarks"].append(bench(label, path, dev, img))
        except Exception as exc:
            report["errors"].append({"label": label, "device": dev, "error": str(exc)})

    try:
        import torch_directml

        dml = torch_directml.device()
        report["directmlDevice"] = str(dml)
        report["benchmarks"].append(bench("PyTorch DirectML yolov8m", "yolov8m.pt", dml, img, runs=1))
    except Exception as exc:
        report["directmlDevice"] = f"skip: {exc}"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
