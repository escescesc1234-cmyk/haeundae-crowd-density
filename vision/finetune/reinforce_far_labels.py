"""자동 라벨 교정 + 원거리(물) person 라벨 보강.

문제: 기존 auto-label은 프레임당 평균 ~23박스라 원거리·밀집 수영자를 많이 놓침.
또한 먼 바다 부표가 tube/person으로 들어간 경우가 있음.

동작:
  1) 교정(모델 불필요, 빠름)
     - cy < FAR 구간의 tube 전부 제거, 작은 person 제거(부표)
     - 클래스별 IoU NMS(중복 박스)
     - 비정상 종횡비·극소 면적 제거
     - 물 구역 박스의 foam(고명도·저채도) 비율이 높으면 제거
  2) 원거리 보강(YOLO teacher)
     - 물 밴드를 크롭·확대한 뒤 person만 탐지(회수 우선, conf 낮게)
     - 기존 person과 IoU가 낮을 때만 추가(안전한 합집합)
     - tube 줄은 교정 결과 유지(이 스크립트는 tube를 새로 넣지 않음)

입력/출력: finetune/dataset/{images,labels}/  (제자리 갱신)
사이드카: finetune/_reinforce_done.txt 로 이어하기

사용:
  python finetune/reinforce_far_labels.py --correct-only
  python finetune/reinforce_far_labels.py --limit 30
  python finetune/reinforce_far_labels.py
  python finetune/reinforce_far_labels.py --rebuild   # 끝나면 make_dataset 호출
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FINETUNE = ROOT / "finetune"
DATASET = FINETUNE / "dataset"
IMG_DIR = DATASET / "images"
LBL_DIR = DATASET / "labels"
PREV_DIR = DATASET / "preview"
DONE_FILE = FINETUNE / "_reinforce_done.txt"
STATS_FILE = FINETUNE / "_reinforce_stats.json"

# 고정캠 구도(realtime_safety_map 과 동일 비율)
WATER_TOP = 0.45
FAR_Y = 0.55
WATER_BOT = 0.78

# 교정
PERSON_FAR_MAX_AREA = 0.0012
NMS_IOU = 0.65
MAX_W_OVER_H = 2.8
MIN_AREA = 1.5e-5
FOAM_V_MIN = 0.70
FOAM_S_MAX = 0.28
FOAM_REJECT = 0.55

# 보강
TEACHER_CANDIDATES = (
    "yolo26m.pt",
    "yolov8m.pt",
    "yolo26s.pt",
    "models/yolov8m_beach_ft.pt",
    "models/best.pt",
)
FAR_CONF = 0.10
MID_CONF = 0.12
FAR_UPSCALE = 3.6
MID_UPSCALE = 2.8
IMGSZ = 1280
MERGE_IOU = 0.45  # 기존과 이 이상 겹치면 추가 안 함

# 머리만(물) · 모래 서있음 타깃 보강 (--heads-stand)
HEAD_FAR_UPSCALE = 4.5
HEAD_MID_UPSCALE = 3.8
HEAD_CONF = 0.08
STAND_UPSCALE = 3.0
STAND_CONF = 0.12
STAND_MIN_H_OVER_W = 0.85   # 세로형(서있는 사람)
STAND_MIN_BH = 0.045        # 프레임 대비 최소 높이(전신·상반신)
HEAD_MAX_BH = 0.08          # 머리·상반신 후보 상한


def resolve_teacher(override: str | None) -> str:
    if override:
        p = Path(override)
        return str(p if p.is_absolute() else ROOT / p)
    for rel in TEACHER_CANDIDATES:
        p = ROOT / rel
        if p.exists():
            return str(p)
    return "yolo26s.pt"


def iou_xyxy(a, b) -> float:
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


def nms_keep(boxes, thr: float):
    boxes = sorted(boxes, key=lambda b: b[4], reverse=True)
    keep = []
    for b in boxes:
        if all(iou_xyxy(b, k) < thr for k in keep):
            keep.append(b)
    return keep


def yolo_to_xyxy(cls: int, cx: float, cy: float, bw: float, bh: float, w: int, h: int):
    x1 = (cx - bw / 2.0) * w
    y1 = (cy - bh / 2.0) * h
    x2 = (cx + bw / 2.0) * w
    y2 = (cy + bh / 2.0) * h
    return cls, x1, y1, x2, y2, cx, cy, bw, bh


def xyxy_to_yolo(cls: int, x1, y1, x2, y2, w: int, h: int) -> str:
    cx = ((x1 + x2) / 2.0) / w
    cy = ((y1 + y2) / 2.0) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def foam_frac(img_bgr: np.ndarray, x1, y1, x2, y2) -> float:
    h, w = img_bgr.shape[:2]
    xa, ya = max(0, int(x1)), max(0, int(y1))
    xb, yb = min(w, int(x2)), min(h, int(y2))
    if xb <= xa or yb <= ya:
        return 0.0
    crop = img_bgr[ya:yb, xa:xb]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # OpenCV: H 0-180, S/V 0-255
    s = hsv[:, :, 1].astype(np.float32) / 255.0
    v = hsv[:, :, 2].astype(np.float32) / 255.0
    mask = (v >= FOAM_V_MIN) & (s <= FOAM_S_MAX)
    return float(mask.mean())


def read_labels(lbl_path: Path, w: int, h: int):
    """→ list of (cls, x1,y1,x2,y2, conf_proxy, cx,cy,bw,bh)"""
    out = []
    if not lbl_path.exists():
        return out
    for ln in lbl_path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        p = ln.split()
        if len(p) < 5:
            continue
        cls = int(float(p[0]))
        cx, cy, bw, bh = map(float, p[1:5])
        conf = float(p[5]) if len(p) >= 6 else 0.5
        _, x1, y1, x2, y2, *_ = yolo_to_xyxy(cls, cx, cy, bw, bh, w, h)
        out.append((cls, x1, y1, x2, y2, conf, cx, cy, bw, bh))
    return out


def write_labels(lbl_path: Path, boxes, w: int, h: int):
    lines = []
    for b in boxes:
        cls, x1, y1, x2, y2 = int(b[0]), b[1], b[2], b[3], b[4]
        lines.append(xyxy_to_yolo(cls, x1, y1, x2, y2, w, h))
    lbl_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def correct_boxes(boxes, img_bgr: np.ndarray | None):
    """기하·foam 교정. 반환: (kept, stats_dict)"""
    stats = {
        "removed_far_tube": 0,
        "removed_far_small_person": 0,
        "removed_aspect": 0,
        "removed_tiny": 0,
        "removed_foam": 0,
        "removed_nms": 0,
    }
    kept = []
    for b in boxes:
        cls, x1, y1, x2, y2, conf, cx, cy, bw, bh = b
        area = bw * bh
        wh = (bw / bh) if bh > 1e-9 else 99.0
        if area < MIN_AREA:
            stats["removed_tiny"] += 1
            continue
        if cy < FAR_Y:
            if cls == 1:
                stats["removed_far_tube"] += 1
                continue
            if cls == 0 and area <= PERSON_FAR_MAX_AREA:
                stats["removed_far_small_person"] += 1
                continue
        if cls == 0 and wh > MAX_W_OVER_H and cy < WATER_BOT:
            stats["removed_aspect"] += 1
            continue
        if img_bgr is not None and cls == 0 and cy < WATER_BOT and area < 0.004:
            if foam_frac(img_bgr, x1, y1, x2, y2) >= FOAM_REJECT:
                stats["removed_foam"] += 1
                continue
        kept.append(b)

    # 클래스별 NMS
    final = []
    before = len(kept)
    for cls in (0, 1):
        group = [b for b in kept if b[0] == cls]
        # nms expects (x1,y1,x2,y2,score,...)
        scored = [(b[1], b[2], b[3], b[4], b[5], b) for b in group]
        scored = nms_keep(scored, NMS_IOU)
        final.extend([s[5] for s in scored])
    stats["removed_nms"] = before - len(final)
    return final, stats


def detect_band_persons(model, img_bgr, y0f, y1f, upscale: float, conf: float, device: str):
    """물 밴드 크롭→확대→YOLO → 원본 좌표 person 박스."""
    h, w = img_bgr.shape[:2]
    y0, y1 = int(y0f * h), int(y1f * h)
    if y1 <= y0:
        return []
    band = img_bgr[y0:y1, :]
    bh, bw = band.shape[:2]
    if bh < 8 or bw < 8:
        return []
    big = cv2.resize(
        band,
        (max(1, int(bw * upscale)), max(1, int(bh * upscale))),
        interpolation=cv2.INTER_CUBIC,
    )
    res = model.predict(
        big, conf=conf, imgsz=IMGSZ, verbose=False, device=device, classes=[0]
    )
    out = []
    if not res:
        return out
    r0 = res[0]
    if r0.boxes is None or len(r0.boxes) == 0:
        return out
    xyxy = r0.boxes.xyxy.cpu().numpy()
    confs = r0.boxes.conf.cpu().numpy()
    for (x1, y1, x2, y2), c in zip(xyxy, confs):
        # big → band → full
        fx1 = x1 / upscale
        fy1 = y1 / upscale + y0
        fx2 = x2 / upscale
        fy2 = y2 / upscale + y0
        # ROI: 밴드 밖으로 거의 나간 박스 버림
        cy = ((fy1 + fy2) / 2.0) / h
        if cy < WATER_TOP or cy > 0.95:
            continue
        out.append((0, float(fx1), float(fy1), float(fx2), float(fy2), float(c)))
    return out


def merge_persons(existing, new_boxes):
    """existing: full tuples; new: (cls,x1,y1,x2,y2,conf). 겹치지 않는 것만 추가."""
    persons = [b for b in existing if b[0] == 0]
    tubes = [b for b in existing if b[0] == 1]
    added = 0
    for nb in new_boxes:
        if any(iou_xyxy(nb, (p[1], p[2], p[3], p[4])) >= MERGE_IOU for p in persons):
            continue
        # pad to same tuple shape
        cls, x1, y1, x2, y2, conf = nb
        persons.append((cls, x1, y1, x2, y2, conf, 0.0, 0.0, 0.0, 0.0))
        added += 1
    return persons + tubes, added


def filter_head_candidates(boxes, h: int):
    """물 위 작은 박스(머리·상반신)만."""
    out = []
    for b in boxes:
        cls, x1, y1, x2, y2, conf = b[:6]
        bh = (y2 - y1) / float(h)
        bw = max(1e-6, (x2 - x1) / float(h))  # 대략 비
        w_over_h = ((x2 - x1) / max(1e-6, y2 - y1))
        if bh <= 0 or bh > HEAD_MAX_BH:
            continue
        if w_over_h > 1.8:
            continue
        cy = ((y1 + y2) / 2.0) / float(h)
        if cy < WATER_TOP or cy > WATER_BOT:
            continue
        out.append(b)
    return out


def filter_stand_candidates(boxes, h: int):
    """모래 위 세로형(서있는 사람)만."""
    out = []
    for b in boxes:
        cls, x1, y1, x2, y2, conf = b[:6]
        bh = (y2 - y1) / float(h)
        bw = max(1e-6, x2 - x1)
        hh = max(1e-6, y2 - y1)
        h_over_w = hh / bw
        cy = ((y1 + y2) / 2.0) / float(h)
        if cy < WATER_BOT:
            continue
        if bh < STAND_MIN_BH:
            continue
        if h_over_w < STAND_MIN_H_OVER_W:
            continue
        out.append(b)
    return out


def draw_preview(img, boxes, path: Path):
    vis = img.copy()
    n_p = n_t = 0
    for b in boxes:
        cls, x1, y1, x2, y2 = int(b[0]), b[1], b[2], b[3], b[4]
        color = (255, 200, 0) if cls == 1 else (255, 0, 255)
        cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        if cls == 1:
            n_t += 1
        else:
            n_p += 1
    txt = f"person={n_p} tube={n_t}"
    cv2.rectangle(vis, (0, 0), (360, 54), (0, 0, 0), -1)
    cv2.putText(
        vis, txt, (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 3, cv2.LINE_AA
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), vis)


def load_done() -> set[str]:
    if not DONE_FILE.exists():
        return set()
    return {ln.strip() for ln in DONE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}


def mark_done(name: str):
    with DONE_FILE.open("a", encoding="utf-8") as f:
        f.write(name + "\n")


def count_label_stats(lbl_dir: Path) -> dict:
    files = sorted(lbl_dir.glob("*.txt"))
    n_p = n_t = 0
    per_file = []
    for lp in files:
        lines = [ln for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        bp = bt = 0
        for ln in lines:
            cls = int(float(ln.split()[0]))
            if cls == 0:
                bp += 1
            elif cls == 1:
                bt += 1
        n_p += bp
        n_t += bt
        per_file.append(bp + bt)
    per_file.sort()
    n = len(per_file) or 1
    return {
        "files": len(files),
        "person": n_p,
        "tube": n_t,
        "boxes": n_p + n_t,
        "mean": round((n_p + n_t) / n, 2),
        "median": per_file[n // 2] if per_file else 0,
        "max": per_file[-1] if per_file else 0,
        "ge50": sum(1 for x in per_file if x >= 50),
        "ge80": sum(1 for x in per_file if x >= 80),
    }


def main():
    ap = argparse.ArgumentParser(description="Auto-correct + far-water label reinforce")
    ap.add_argument("--correct-only", action="store_true", help="교정만(모델 없음)")
    ap.add_argument(
        "--heads-stand",
        action="store_true",
        help="물 머리·모래 서있음만 고배율 보강",
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default=None)
    ap.add_argument("--reset-done", action="store_true", help="이어하기 기록 초기화")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="종료 후 make_dataset.py")
    ap.add_argument("--device", default=None, help="cpu / cuda:0")
    ap.add_argument("--done-file", default=None, help="이어하기 파일 경로")
    args = ap.parse_args()

    if not IMG_DIR.is_dir() or not LBL_DIR.is_dir():
        raise SystemExit(f"[reinforce] dataset 없음: {DATASET}")

    # 파이프/백그라운드에서도 진행 로그가 바로 보이게
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    before = count_label_stats(LBL_DIR)
    print("[reinforce] BEFORE", json.dumps(before, ensure_ascii=False), flush=True)

    done_path = Path(args.done_file) if args.done_file else (
        FINETUNE / "_heads_done.txt" if args.heads_stand else DONE_FILE
    )
    global DONE_FILE
    DONE_FILE = done_path

    if args.reset_done and DONE_FILE.exists():
        DONE_FILE.unlink()

    frames = sorted(IMG_DIR.glob("*.jpg"))
    done = load_done()
    if not args.correct_only:
        frames = [f for f in frames if f.name not in done]
    if args.limit:
        frames = frames[: args.limit]
    print(
        f"[reinforce] target frames={len(frames)} "
        f"correct_only={args.correct_only} heads_stand={args.heads_stand}",
        flush=True,
    )

    model = None
    device = args.device or "cpu"
    if not args.correct_only:
        from ultralytics import YOLO

        path = resolve_teacher(args.model)
        print(f"[reinforce] teacher={path} device={device}")
        model = YOLO(path)
        # beach_ft(2-class)도 person=0 이므로 classes=[0] 로 통일

    sum_stats = {
        "removed_far_tube": 0,
        "removed_far_small_person": 0,
        "removed_aspect": 0,
        "removed_tiny": 0,
        "removed_foam": 0,
        "removed_nms": 0,
        "added_person": 0,
        "files": 0,
    }
    t0 = time.perf_counter()

    for i, fp in enumerate(frames, 1):
        img = cv2.imread(str(fp))
        if img is None:
            continue
        h, w = img.shape[:2]
        lbl = LBL_DIR / (fp.stem + ".txt")
        boxes = read_labels(lbl, w, h)
        boxes, st = correct_boxes(boxes, img)
        for k, v in st.items():
            sum_stats[k] = sum_stats.get(k, 0) + v

        added = 0
        if model is not None:
            if args.heads_stand:
                far = detect_band_persons(
                    model, img, WATER_TOP, FAR_Y, HEAD_FAR_UPSCALE, HEAD_CONF, device
                )
                mid = detect_band_persons(
                    model, img, FAR_Y, WATER_BOT, HEAD_MID_UPSCALE, HEAD_CONF, device
                )
                heads = filter_head_candidates(far + mid, h)
                beach = detect_band_persons(
                    model, img, WATER_BOT, 0.98, STAND_UPSCALE, STAND_CONF, device
                )
                stands = filter_stand_candidates(beach, h)
                merged_new = nms_keep(heads + stands, 0.5)
                sum_stats["added_heads"] = sum_stats.get("added_heads", 0)
                sum_stats["added_stands"] = sum_stats.get("added_stands", 0)
            else:
                far = detect_band_persons(
                    model, img, WATER_TOP, FAR_Y, FAR_UPSCALE, FAR_CONF, device
                )
                mid = detect_band_persons(
                    model, img, FAR_Y, WATER_BOT, MID_UPSCALE, MID_CONF, device
                )
                # 모래 상단도 약하게 (앉은 사람)
                beach = detect_band_persons(
                    model, img, WATER_BOT, 0.95, 2.2, 0.15, device
                )
                merged_new = nms_keep(far + mid + beach, 0.5)
            boxes, added = merge_persons(boxes, merged_new)
            sum_stats["added_person"] += added
            if args.heads_stand:
                # 대략 분류(추가분 추적용)
                n_h = len(heads) if args.heads_stand else 0
                n_s = len(stands) if args.heads_stand else 0
                sum_stats["cand_heads"] = sum_stats.get("cand_heads", 0) + n_h
                sum_stats["cand_stands"] = sum_stats.get("cand_stands", 0) + n_s

        write_labels(lbl, boxes, w, h)
        if not args.no_preview:
            draw_preview(img, boxes, PREV_DIR / fp.name)
        if model is not None:
            mark_done(fp.name)
        sum_stats["files"] += 1

        if i % 10 == 0 or i == len(frames):
            elapsed = time.perf_counter() - t0
            rate = elapsed / max(1, i)
            eta = rate * (len(frames) - i)
            print(
                f"[reinforce] {i}/{len(frames)} {fp.name} "
                f"+person={added} boxes={len(boxes)} "
                f"elapsed={elapsed:.0f}s eta={eta:.0f}s"
            )

    after = count_label_stats(LBL_DIR)
    report = {
        "before": before,
        "after": after,
        "delta": {
            "person": after["person"] - before["person"],
            "tube": after["tube"] - before["tube"],
            "boxes": after["boxes"] - before["boxes"],
            "mean": round(after["mean"] - before["mean"], 2),
            "max": after["max"] - before["max"],
        },
        "ops": sum_stats,
        "correct_only": args.correct_only,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    STATS_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[reinforce] AFTER ", json.dumps(after, ensure_ascii=False))
    print("[reinforce] DELTA ", json.dumps(report["delta"], ensure_ascii=False))
    print("[reinforce] OPS   ", json.dumps(sum_stats, ensure_ascii=False))
    print(f"[reinforce] stats → {STATS_FILE}")

    if args.rebuild:
        print("[reinforce] make_dataset.py …")
        subprocess.check_call([sys.executable, str(FINETUNE / "make_dataset.py")], cwd=str(ROOT))


if __name__ == "__main__":
    main()
