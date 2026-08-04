"""학습 라벨에서 파도→tube / 허공→person 오라벨을 제거.

이미지 패치를 보고 휴리스틱으로 나쁜 박스를 지운다.
(추론 STRICT_FP 와 동일한 색·거품·분산 기준)

사용:
  python finetune/clean_fp_labels.py --dry-run     # 삭제 예정만 출력
  python finetune/clean_fp_labels.py               # 라벨 파일 수정
  python finetune/clean_fp_labels.py --drop-empty  # 박스 0개가 된 프레임도 삭제
  python finetune/clean_fp_labels.py --purge-frames  # 의심 박스≥N개인 프레임 통째 삭제

이후: make_dataset.py 또는 웹 ZIP 생성 → Colab 재학습.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "finetune" / "dataset"

WATER_Y_TOP = 0.45
WATER_Y_BOT = 0.78
FOAM_V_MIN, FOAM_S_MAX = 0.62, 0.35
FOAM_REJECT = 0.32
TUBE_COLOR_NEED = 0.14
SEA_NEED = 0.55
STD_EMPTY = 36.0


def _patch(img, xc, yc, bw, bh):
    h, w = img.shape[:2]
    x1 = max(0, int((xc - bw / 2) * w))
    y1 = max(0, int((yc - bh / 2) * h))
    x2 = min(w, int((xc + bw / 2) * w))
    y2 = min(h, int((yc + bh / 2) * h))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return img[y1:y2, x1:x2]


def foam_frac(patch) -> float:
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    return float(np.mean((v >= FOAM_V_MIN) & (s <= FOAM_S_MAX)))


def tube_color_frac(patch) -> float:
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[..., 0], hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    orange = (hh >= 5) & (hh <= 35) & (ss >= 0.25) & (vv >= 0.35)
    blue = (hh >= 90) & (hh <= 130) & (ss >= 0.25) & (vv >= 0.25)
    pink = ((hh >= 140) & (hh <= 175) & (ss >= 0.20) & (vv >= 0.35)) | (
        (hh <= 8) & (ss >= 0.25) & (vv >= 0.35)
    )
    return float(np.mean(orange | blue | pink))


def sea_frac(patch) -> float:
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[..., 0], hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    blue = (hh >= 85) & (hh <= 140) & (ss >= 0.12) & (vv >= 0.18) & (vv <= 0.92)
    gray = (ss <= 0.22) & (vv >= 0.25) & (vv <= 0.85) & (hh >= 70) & (hh <= 150)
    return float(np.mean(blue | gray))


def is_bad_box(cls: int, xc: float, yc: float, bw: float, bh: float, img) -> str | None:
    """나쁘면 사유 문자열, 아니면 None."""
    if yc < WATER_Y_TOP - 0.02:
        return "sky_void"
    patch = _patch(img, xc, yc, bw, bh)
    if patch is None:
        return "tiny"
    in_water = WATER_Y_TOP <= yc < WATER_Y_BOT
    foam = foam_frac(patch)
    tube_c = tube_color_frac(patch)
    sea = sea_frac(patch)
    std = float(patch.std())

    if cls == 1:  # tube
        if not in_water and yc > WATER_Y_BOT + 0.05:
            return "tube_on_beach"
        if foam >= FOAM_REJECT and tube_c < 0.16:
            return "wave_as_tube"
        if sea >= SEA_NEED and tube_c < TUBE_COLOR_NEED and foam >= 0.12:
            return "sea_as_tube"
        if std < STD_EMPTY and tube_c < TUBE_COLOR_NEED:
            return "empty_as_tube"
        if tube_c < 0.08 and in_water:
            return "no_tube_color"
        return None

    # person
    if in_water:
        if foam >= FOAM_REJECT:
            return "wave_as_person"
        if std < STD_EMPTY and sea >= 0.45:
            return "empty_as_person"
        if sea >= 0.62 and std < 48.0 and bh < 0.04:
            return "tiny_sea_person"
    return None


def iter_label_dirs(root: Path):
    flat = root / "labels"
    if flat.is_dir():
        yield "", flat, root / "images"
    for split in ("train", "val"):
        ld = root / split / "labels"
        id_ = root / split / "images"
        if ld.is_dir():
            yield split, ld, id_


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DATASET))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drop-empty", action="store_true", help="박스 0개 프레임 파일 삭제")
    ap.add_argument(
        "--purge-frames",
        type=int,
        default=0,
        help="나쁜 박스 ≥N개면 프레임(이미지+라벨) 통째 삭제",
    )
    ap.add_argument("--backup", action="store_true", help="labels_bak 백업")
    args = ap.parse_args()
    root = Path(args.root)

    removed_boxes = 0
    touched = 0
    purged = 0
    emptied = 0
    reasons: dict[str, int] = {}

    for split, lbl_dir, img_dir in iter_label_dirs(root):
        if args.backup:
            bak = lbl_dir.parent / (lbl_dir.name + "_bak")
            if not bak.exists():
                shutil.copytree(lbl_dir, bak)
                print(f"[bak] {bak}")

        for lp in sorted(lbl_dir.glob("*.txt")):
            stem = lp.stem
            img_path = img_dir / f"{stem}.jpg"
            if not img_path.exists():
                continue
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            lines = lp.read_text(encoding="utf-8").splitlines()
            keep = []
            bad_n = 0
            for line in lines:
                parts = line.split()
                if len(parts) < 5:
                    continue
                cls = int(float(parts[0]))
                xc, yc, bw, bh = map(float, parts[1:5])
                why = is_bad_box(cls, xc, yc, bw, bh, img)
                if why:
                    bad_n += 1
                    removed_boxes += 1
                    reasons[why] = reasons.get(why, 0) + 1
                else:
                    keep.append(line)

            if args.purge_frames and bad_n >= args.purge_frames:
                purged += 1
                if not args.dry_run:
                    lp.unlink(missing_ok=True)
                    img_path.unlink(missing_ok=True)
                    prev = root / "preview" / f"{stem}.jpg"
                    prev.unlink(missing_ok=True)
                continue

            if len(keep) != len(lines):
                touched += 1
                if not args.dry_run:
                    lp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")

            if args.drop_empty and not keep:
                emptied += 1
                if not args.dry_run:
                    lp.unlink(missing_ok=True)
                    img_path.unlink(missing_ok=True)

    print(
        f"[clean_fp] dry={args.dry_run} touched_files={touched} "
        f"removed_boxes={removed_boxes} purged_frames={purged} emptied={emptied}"
    )
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    if args.dry_run:
        print("[clean_fp] 실제 적용: 옵션 없이 다시 실행")
    else:
        print("[clean_fp] 다음: python finetune/make_dataset.py 또는 웹 ZIP 생성 → Colab 재학습")


if __name__ == "__main__":
    main()
