"""사전 라벨 생성기 (반자동 라벨링 1단계).

현재 FAST 파이프라인(원근 밴드 SAHI, yolo26s)으로 수집 프레임에 사람 박스를
자동으로 그려 YOLO 포맷 라벨 초안을 만든다. 사람은 이 초안을 '교정'만 하면 되므로
라벨링 시간이 크게 준다.

출력 구조 (Ultralytics 표준):
    finetune/dataset/images/*.jpg   (원본 복사)
    finetune/dataset/labels/*.txt   (YOLO: 'class cx cy w h', 정규화, class=0=person)
    finetune/dataset/preview/*.jpg  (검수용 시각화)

교정: LabelImg(YOLO 모드) 또는 Roboflow로 images/를 열어 labels/를 수정.
      → 이후 make_dataset.py 로 train/val 분할 + data.yaml 생성.

사용:
    python finetune/prelabel.py                 # finetune/raw 전체
    python finetune/prelabel.py --src finetune/raw --limit 600
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2

import realtime_safety_map as R
from sahi import AutoDetectionModel

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "finetune" / "raw"
DEFAULT_OUT = ROOT / "finetune" / "dataset"


def build_model() -> AutoDetectionModel:
    path = R.resolve_fast_sahi_model()
    print(f"[prelabel] model={path}")
    return AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=path,
        confidence_threshold=R.PERSON_PROPOSAL_CONF,
        device="cpu",
        image_size=R.FAST_SAHI_IMGSZ,
    )


def to_yolo_line(box, w: int, h: int) -> str:
    x1, y1, x2, y2, _s = box
    cx = ((x1 + x2) / 2.0) / w
    cy = ((y1 + y2) / 2.0) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def main():
    ap = argparse.ArgumentParser(description="Auto pre-label collected frames")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0, help="처리 최대 장수(0=전체)")
    ap.add_argument("--preview", action="store_true", default=True)
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    img_dir = out / "images"
    lbl_dir = out / "labels"
    prev_dir = out / "preview"
    for d in (img_dir, lbl_dir, prev_dir):
        d.mkdir(parents=True, exist_ok=True)

    frames = sorted(src.glob("*.jpg"))
    if args.limit:
        frames = frames[: args.limit]
    if not frames:
        print(f"[prelabel] no frames in {src}")
        return

    model = build_model()
    print(f"[prelabel] {len(frames)} frames → {out}")

    for i, fp in enumerate(frames, 1):
        img = cv2.imread(str(fp))
        if img is None:
            continue
        h, w = img.shape[:2]
        roi_mask, _ = R.make_roi_mask(h, w, R.LIVE_ROI)
        _c, confirmed, _rej = R.detect_people_sahi_fast(model, img, roi_mask)

        shutil.copy2(fp, img_dir / fp.name)
        lines = [to_yolo_line(b, w, h) for b in confirmed]
        (lbl_dir / (fp.stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")

        if args.preview:
            vis = img.copy()
            for (x1, y1, x2, y2, s) in confirmed:
                cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)),
                              (0, 200, 255), 2)
            cv2.imwrite(str(prev_dir / fp.name), vis)

        print(f"[prelabel] {i}/{len(frames)} {fp.name} persons={len(confirmed)}")

    print(f"[prelabel] done. 교정 후 make_dataset.py 실행하세요.")


if __name__ == "__main__":
    main()
