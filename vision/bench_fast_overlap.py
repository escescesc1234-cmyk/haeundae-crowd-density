"""FAST SAHI overlap A/B: 재현율(확정 박스 수) vs 추론시간.

사용:
  python bench_fast_overlap.py
  python bench_fast_overlap.py --img output/crowd_input.jpg --overlaps 0.22,0.30,0.40
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

import realtime_safety_map as R

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=str(ROOT / "output" / "crowd_input.jpg"))
    ap.add_argument("--overlaps", default="0.22,0.30,0.40,0.50")
    args = ap.parse_args()

    img_path = Path(args.img)
    frame = cv2.imread(str(img_path))
    if frame is None:
        print(f"[bench] no image: {img_path}")
        return
    # crowd_input 은 이미 ROI 크롭일 수 있음 → 마스크는 전체
    h, w = frame.shape[:2]
    # 원본(하늘 포함)이면 LIVE_ROI, 이미 ROI 크롭(가로로 매우 납작)이면 전체
    if h / max(w, 1) < 0.45:
        import numpy as np

        roi_mask = np.ones((h, w), dtype="uint8") * 255
    else:
        roi_mask, _ = R.make_live_roi_mask(h, w)

    model_path = R.resolve_fast_sahi_model()
    print(f"[bench] model={model_path} img={img_path} shape={w}x{h}")
    sahi = R.AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=model_path,
        confidence_threshold=R.FAST_CONF,
        device=R._sahi_autodm_device(),
        image_size=R.FAST_SAHI_IMGSZ,
    )

    overlaps = [float(x) for x in args.overlaps.split(",") if x.strip()]
    print(f"{'overlap':>8}  {'confirmed':>10}  {'rejected':>9}  {'ms':>8}")
    best = None
    for ov in overlaps:
        t0 = time.perf_counter()
        _c, confirmed, rejected = R.detect_people_sahi_fast(
            sahi, frame, roi_mask, overlap=ov
        )
        ms = (time.perf_counter() - t0) * 1000.0
        n = len(confirmed)
        nr = len(rejected)
        print(f"{ov:8.2f}  {n:10d}  {nr:9d}  {ms:8.0f}")
        # 점수: 확정↑ 우선, 시간이 기준(0.22) 대비 2.5배 넘으면 페널티
        if best is None:
            best = (ov, n, ms)
        else:
            base_ms = best[2] if best[0] == overlaps[0] else best[2]
            # 재평가: 더 많은 확정 + 시간 합리적
            if n > best[1] and ms < best[2] * 2.2:
                best = (ov, n, ms)
            elif n == best[1] and ms < best[2]:
                best = (ov, n, ms)
    print(f"[bench] 추천 overlap≈{best[0]:.2f} (confirmed={best[1]}, {best[2]:.0f}ms)")


if __name__ == "__main__":
    main()
