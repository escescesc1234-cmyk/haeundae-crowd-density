"""먼 바다 부표 띠(y < FAR)에 있는 잘못된 person/tube 라벨 제거.

고정 캠에서 y≈0.45~0.55는 안전 부표 줄이 가로로 늘어선 구간이다.
기존 auto-label이 부표를 tube/person으로 넣었을 수 있어, 재학습 전에 청소한다.

사용:
  python finetune/purge_far_buoy_labels.py
  python finetune/purge_far_buoy_labels.py --far 0.55 --dry-run
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LBL = ROOT / "dataset" / "labels"
FAR_DEFAULT = 0.55
# 먼 바다에서 작은 person 박스만 제거 (큰 수영자는 유지)
PERSON_MAX_AREA = 0.0012


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--far", type=float, default=FAR_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    removed_tube = removed_person = kept = 0
    touched = 0
    for lp in sorted(LBL.glob("*.txt")):
        lines = [ln.strip() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        out = []
        changed = False
        for ln in lines:
            p = ln.split()
            if len(p) < 5:
                out.append(ln)
                continue
            cls = int(float(p[0]))
            cx, cy, bw, bh = map(float, p[1:5])
            area = bw * bh
            drop = False
            if cy < args.far:
                if cls == 1:
                    drop = True
                    removed_tube += 1
                elif cls == 0 and area <= PERSON_MAX_AREA:
                    drop = True
                    removed_person += 1
            if drop:
                changed = True
            else:
                out.append(ln)
                kept += 1
        if changed:
            touched += 1
            if not args.dry_run:
                lp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    mode = "DRY" if args.dry_run else "DONE"
    print(
        f"[{mode}] files_touched={touched} removed_tube={removed_tube} "
        f"removed_person_small={removed_person} kept={kept} far<{args.far}"
    )


if __name__ == "__main__":
    main()
