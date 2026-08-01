"""로컬 튜브(class 1) 자동 라벨러 — 물 구역 타일링으로 작은 튜브 recall↑.

배경: 파인튜닝 모델이 튜브를 전혀 예측하지 못함(데이터셋 tube 라벨=0).
원인: 라벨링이 전체 프레임 1회 추론이라 작은 튜브를 대부분 놓침.
해결: 물 구역을 좌우 타일로 잘라 확대 추론(YOLO-World) → 작은 튜브까지 라벨.

동작:
  - dataset/images/*.jpg 순회
  - 기존 라벨의 person(0) 줄은 보존, 기존 tube(1) 줄은 제거(재실행 안전)
  - 물 구역 4타일 @imgsz로 YOLO-World 추론(conf 낮게)
  - 기하 필터(비율/면적) + 물구역 + person 겹침 가드 + 튜브 NMS
  - class 1 줄을 라벨 파일에 추가, preview에 하늘색 박스 갱신
  - 진행상황 sidecar(_tube_done.txt)로 이어하기 지원

사용:
  python finetune/label_tubes_local.py            # 전체
  python finetune/label_tubes_local.py --limit 8  # 테스트
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from ultralytics import YOLOWorld

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "finetune" / "dataset"
IMG_DIR = DATASET / "images"
LBL_DIR = DATASET / "labels"
PREV_DIR = DATASET / "preview"
DONE_FILE = ROOT / "finetune" / "_tube_done.txt"

CLASSES = [
    "swim ring", "swim tube", "inflatable ring", "pool float",
    "inner tube", "rubber ring", "inflatable float", "beach float",
    "float tube", "donut float",
]
WATER_TOP, WATER_BOT = 0.55, 0.85  # 먼 바다 부표 띠(y<0.55)는 튜브 라벨에서 제외
CONF = 0.10
IMGSZ = 640
TILES = 4
TILE_OVERLAP = 0.15
# 기하 필터
R_MIN, R_MAX = 0.6, 3.5            # 가로/세로 비율 (부표·세로 노이즈 완화)
A_MIN, A_MAX = 3e-5, 0.08          # 너무 작은 점(부표) 제외
NMS_IOU = 0.5
PERSON_GUARD_IOU = 0.45            # 이미 사람 라벨과 이만큼 겹치면 튜브 제거
FAR_MIN_AREA = 8e-5                # 입수대에서도 극소 박스는 부표 가능성 → 제외


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def nms(boxes, thr):
    boxes = sorted(boxes, key=lambda b: b[4], reverse=True)
    keep = []
    for b in boxes:
        if all(iou(b, k) < thr for k in keep):
            keep.append(b)
    return keep


def read_person_boxes(lbl_path: Path, w: int, h: int):
    """라벨 파일에서 person(0) 줄 → (원본 텍스트줄, 픽셀 xyxy)."""
    person_lines, person_xyxy = [], []
    if lbl_path.exists():
        for ln in lbl_path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            p = ln.split()
            if p[0] != "0":
                continue  # tube(1) 등 기존 줄은 버림(재실행 안전)
            person_lines.append(ln)
            cx, cy, bw, bh = map(float, p[1:5])
            x1 = (cx - bw / 2) * w
            y1 = (cy - bh / 2) * h
            x2 = (cx + bw / 2) * w
            y2 = (cy + bh / 2) * h
            person_xyxy.append((x1, y1, x2, y2))
    return person_lines, person_xyxy


def detect_tubes(model, img, device="cpu"):
    H, W = img.shape[:2]
    y0, y1 = int(WATER_TOP * H), int(WATER_BOT * H)
    band = img[y0:y1, :]
    bw = band.shape[1]
    tw = bw // TILES
    cands = []
    for i in range(TILES):
        x0 = i * tw
        x1t = bw if i == TILES - 1 else min(bw, (i + 1) * tw + int(tw * TILE_OVERLAP))
        crop = band[:, x0:x1t]
        r = model.predict(crop, conf=CONF, imgsz=IMGSZ, verbose=False, device=device)[0]
        for b in r.boxes:
            cx1, cy1, cx2, cy2 = [float(v) for v in b.xyxy[0]]
            X1, Y1, X2, Y2 = cx1 + x0, cy1 + y0, cx2 + x0, cy2 + y0
            bw_, bh_ = X2 - X1, Y2 - Y1
            ratio = bw_ / max(1.0, bh_)
            af = (bw_ * bh_) / (W * H)
            if not (R_MIN <= ratio <= R_MAX):
                continue
            if not (A_MIN <= af <= A_MAX):
                continue
            if af < FAR_MIN_AREA:
                continue
            cands.append((X1, Y1, X2, Y2, float(b.conf[0])))
    return nms(cands, NMS_IOU)


def to_line(b, w, h):
    x1, y1, x2, y2 = b[:4]
    cx = ((x1 + x2) / 2) / w
    cy = ((y1 + y2) / 2) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"1 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fresh", action="store_true", help="_tube_done 무시하고 처음부터")
    ap.add_argument("--device", default="cpu", help="cpu 또는 0(Colab GPU)")
    args = ap.parse_args()

    done = set()
    if DONE_FILE.exists() and not args.fresh:
        done = set(DONE_FILE.read_text(encoding="utf-8").split())

    images = sorted(IMG_DIR.glob("*.jpg"))
    if args.limit:
        images = images[: args.limit]
    todo = [im for im in images if im.stem not in done]
    print(f"[tube] images={len(images)} done={len(done)} todo={len(todo)}")
    if not todo:
        print("[tube] nothing to do"); return

    model = YOLOWorld("yolov8l-worldv2.pt")
    model.set_classes(CLASSES)

    total_tubes = 0
    with DONE_FILE.open("a", encoding="utf-8") as done_fp:
        for i, im in enumerate(todo, 1):
            img = cv2.imread(str(im))
            if img is None:
                done_fp.write(im.stem + "\n"); done_fp.flush(); continue
            h, w = img.shape[:2]
            lbl = LBL_DIR / (im.stem + ".txt")
            person_lines, person_xyxy = read_person_boxes(lbl, w, h)
            tubes = detect_tubes(model, img, device=args.device)
            tubes = [t for t in tubes
                     if all(iou(t, p) < PERSON_GUARD_IOU for p in person_xyxy)]
            tube_lines = [to_line(t, w, h) for t in tubes]
            lbl.write_text("\n".join(person_lines + tube_lines), encoding="utf-8")

            prev = PREV_DIR / im.name
            if prev.exists():
                vis = cv2.imread(str(prev))
                if vis is not None and vis.shape[:2] == (h, w):
                    for t in tubes:
                        x1, y1, x2, y2 = [int(v) for v in t[:4]]
                        cv2.rectangle(vis, (x1, y1), (x2, y2), (255, 200, 0), 2)
                    cv2.imwrite(str(prev), vis)

            total_tubes += len(tubes)
            done_fp.write(im.stem + "\n"); done_fp.flush()
            if i % 20 == 0 or i == len(todo):
                print(f"[tube] {i}/{len(todo)} {im.name} tubes={len(tubes)} "
                      f"(누적 {total_tubes})", flush=True)
    print(f"[tube] done. 추가된 튜브 라벨 누적 {total_tubes}개. "
          f"다음: python finetune/make_dataset.py")


if __name__ == "__main__":
    main()
