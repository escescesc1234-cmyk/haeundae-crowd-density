"""모델·포맷별 CPU 효율/정확도 벤치마크 (해운대/광안리 라이브 프레임 기준).

목적: 현재 YOLOv8 + PyTorch(CPU) + SAHI 구성 대비
      - OpenVINO(FP32/INT8) 가속
      - YOLO11 / YOLO26 (신형, 경량·고정확)
      어느 조합이 더 빠르고 정확한지 이 PC에서 직접 측정한다.

사용법:
    python benchmark_models.py            # 라이브 프레임 1장 캡처 후 측정
    python benchmark_models.py frame.jpg  # 지정 이미지로 측정

결과: output/model_benchmark.json + 콘솔 표
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "model_benchmark.json"
FRAME_CACHE = ROOT / "output" / "bench_frame.jpg"

# 측정할 모델 후보 (없으면 자동 다운로드, 실패 시 skip)
MODEL_CANDIDATES = [
    "yolov8m.pt",   # 현재 사용 중 (기준선)
    "yolov8s.pt",
    "yolo11s.pt",   # 신형 경량
    "yolo11m.pt",   # 신형 중형 (v8m보다 파라미터↓, 정확도↑)
    "yolo26s.pt",   # 2026 신형 (NMS-free, CPU 최적)
    "yolo26m.pt",
]

IMGSZ = 960          # 실사용에 가까운 입력 크기
CONF = 0.15
WARMUP = 1
RUNS = 3


def get_frame(arg: str | None) -> "cv2.typing.MatLike":
    if arg:
        img = cv2.imread(arg)
        if img is None:
            raise SystemExit(f"이미지를 못 읽음: {arg}")
        return img
    if FRAME_CACHE.exists():
        img = cv2.imread(str(FRAME_CACHE))
        if img is not None:
            print(f"[frame] 캐시 사용: {FRAME_CACHE}")
            return img
    # 라이브에서 1장 캡처
    print("[frame] 라이브 프레임 캡처 중...")
    from realtime_safety_map import (
        resolve_stream_url,
        open_capture,
        DEFAULT_YOUTUBE,
    )

    url = resolve_stream_url(DEFAULT_YOUTUBE)
    cap = open_capture(url)
    frame = None
    for _ in range(10):
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
    cap.release()
    if frame is None:
        raise SystemExit("라이브 프레임 캡처 실패")
    FRAME_CACHE.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(FRAME_CACHE), frame)
    print(f"[frame] 저장: {FRAME_CACHE} shape={frame.shape}")
    return frame


def ensure_model(name: str) -> str | None:
    """가중치를 확보(자동 다운로드). 실패하면 None."""
    from ultralytics import YOLO

    try:
        YOLO(name)  # 다운로드 트리거
        return name
    except Exception as exc:
        print(f"[skip] {name}: {exc}")
        return None


def export_openvino(name: str, int8: bool, calib_img: str | None) -> str | None:
    from ultralytics import YOLO

    try:
        m = YOLO(name)
        kwargs = dict(format="openvino", imgsz=IMGSZ, half=not int8)
        if int8:
            kwargs["int8"] = True
            if calib_img:
                kwargs["data"] = calib_img  # 간이 보정 (없으면 기본 coco8)
        path = m.export(**kwargs)
        return str(path)
    except Exception as exc:
        print(f"[skip] export {name} int8={int8}: {exc}")
        return None


def bench(label: str, model_path: str, img, device: str = "cpu") -> dict:
    from ultralytics import YOLO

    m = YOLO(model_path)
    for _ in range(WARMUP):
        m.predict(img, classes=[0], conf=CONF, imgsz=IMGSZ, verbose=False, device=device)
    t0 = time.perf_counter()
    for _ in range(RUNS):
        r = m.predict(
            img, classes=[0], conf=CONF, imgsz=IMGSZ, verbose=False, device=device
        )[0]
    ms = (time.perf_counter() - t0) / RUNS * 1000.0
    n = len(r.boxes) if r.boxes is not None else 0
    return {"label": label, "model": model_path, "msPerInfer": round(ms, 1), "personBoxes": n}


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    img = get_frame(arg)
    calib = str(FRAME_CACHE) if FRAME_CACHE.exists() else None

    report: dict = {"imgsz": IMGSZ, "conf": CONF, "runs": RUNS, "results": [], "notes": []}
    try:
        import openvino as ov

        report["openvinoDevices"] = ov.Core().available_devices
    except Exception as exc:
        report["openvinoDevices"] = f"unavailable: {exc}"

    for name in MODEL_CANDIDATES:
        got = ensure_model(name)
        if not got:
            continue
        # 1) PyTorch CPU (기준선)
        try:
            report["results"].append(bench(f"{name} PyTorch-CPU", got, img))
        except Exception as exc:
            report["notes"].append(f"{name} pytorch: {exc}")
        # 2) OpenVINO FP16
        ov_fp = export_openvino(name, int8=False, calib_img=None)
        if ov_fp:
            try:
                report["results"].append(bench(f"{name} OpenVINO-FP16", ov_fp, img))
            except Exception as exc:
                report["notes"].append(f"{name} ov-fp16: {exc}")
        # 3) OpenVINO INT8
        ov_i8 = export_openvino(name, int8=True, calib_img=calib)
        if ov_i8:
            try:
                report["results"].append(bench(f"{name} OpenVINO-INT8", ov_i8, img))
            except Exception as exc:
                report["notes"].append(f"{name} ov-int8: {exc}")
        # 중간 저장 (오래 걸리므로)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # 표 출력
    print("\n=== 벤치마크 결과 (imgsz={}, conf={}) ===".format(IMGSZ, CONF))
    print(f"{'구성':<28}{'ms/추론':>10}{'인원':>8}")
    print("-" * 46)
    base = None
    for r in report["results"]:
        if base is None:
            base = r["msPerInfer"]
        spd = base / r["msPerInfer"] if r["msPerInfer"] else 0
        print(f"{r['label']:<28}{r['msPerInfer']:>10}{r['personBoxes']:>8}  ({spd:.2f}x)")
    print("\n저장:", OUT)


if __name__ == "__main__":
    main()
