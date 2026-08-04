"""의심 프레임 자동 선별 → 한 폴더(review/)에 모아 검수.

자동 라벨(prelabel/autolabel_watch) 결과 중 '파도 오탐' 같은 이상 프레임을
휴리스틱으로 골라 finetune/review/ 에 preview 사본을 모은다. 사용자는 이 한
폴더만 넘겨보며 '진짜 나쁜' 이미지를 삭제하면 된다.

휴리스틱(하나라도 걸리면 의심):
  - 사람 수가 통계적 이상치(중앙값 + z*MAD)이고 절대 하한(--min-count) 이상
  - 물 구역(세로 0.45~0.78) 박스가 과다(--water-min 이상)  ← 파도 오탐 전형

지원 데이터셋 구조:
  A) finetune/dataset/{images,labels}          (로컬 prelabel)
  B) .../{train,val}/{images,labels}           (Colab gwangalli_dataset.zip)

2단계 워크플로:
  1) python finetune/flag_suspect.py
  2) review/ 에서 나쁜 이미지 삭제
  3) python finetune/flag_suspect.py --apply

검수 없이 전부 삭제:
  python finetune/flag_suspect.py --purge
주의: --purge 는 진짜로 붐비는 정상 프레임까지 지울 수 있다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "finetune" / "dataset"
REVIEW = ROOT / "finetune" / "review"
MANIFEST = ROOT / "finetune" / "_flagged.json"

WATER_Y_TOP = 0.45
WATER_Y_BOT = 0.78


def read_counts(lbl_path: Path):
    """라벨 파일에서 (총 사람수, 물구역 사람수) 반환. class 0(person)만 계수."""
    total = 0
    water = 0
    try:
        for line in lbl_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] != "0":
                continue
            cy = float(parts[2])
            total += 1
            if WATER_Y_TOP <= cy <= WATER_Y_BOT:
                water += 1
    except Exception:
        pass
    return total, water


def resolve_dataset(root: Path) -> Path:
    root = root.resolve()
    if (root / "labels").is_dir() or (root / "train" / "labels").is_dir():
        return root
    if (root / "dataset" / "labels").is_dir() or (
        root / "dataset" / "train" / "labels"
    ).is_dir():
        return root / "dataset"
    return root


def iter_label_files(ds: Path) -> list[tuple[str, Path, str]]:
    """[(stem, label_path, split)] split='' | 'train' | 'val'."""
    out: list[tuple[str, Path, str]] = []
    flat = ds / "labels"
    if flat.is_dir():
        for lp in sorted(flat.glob("*.txt")):
            out.append((lp.stem, lp, ""))
    for split in ("train", "val"):
        d = ds / split / "labels"
        if not d.is_dir():
            continue
        for lp in sorted(d.glob("*.txt")):
            out.append((lp.stem, lp, split))
    # stem 중복 시 train/val 우선(분할본), flat만 있으면 flat
    by_stem: dict[str, tuple[str, Path, str]] = {}
    for stem, lp, split in out:
        prev = by_stem.get(stem)
        if prev is None or (prev[2] == "" and split != ""):
            by_stem[stem] = (stem, lp, split)
    return list(by_stem.values())


def image_candidates(ds: Path, stem: str, split: str) -> list[Path]:
    cands: list[Path] = []
    if split:
        cands.append(ds / split / "images" / f"{stem}.jpg")
    cands.extend(
        [
            ds / "preview" / f"{stem}.jpg",
            ds / "images" / f"{stem}.jpg",
            ROOT / "finetune" / "raw" / f"{stem}.jpg",
        ]
    )
    return cands


def remove_stem(ds: Path, stem: str, split: str = "") -> int:
    """이미지·라벨·preview 삭제. 삭제된 파일 수."""
    n = 0
    paths = [
        ds / "images" / f"{stem}.jpg",
        ds / "labels" / f"{stem}.txt",
        ds / "preview" / f"{stem}.jpg",
        ROOT / "finetune" / "raw" / f"{stem}.jpg",
        REVIEW / f"{stem}.jpg",
    ]
    for sp in ((split,) if split else ("train", "val", "")):
        if not sp:
            continue
        paths.extend(
            [
                ds / sp / "images" / f"{stem}.jpg",
                ds / sp / "labels" / f"{stem}.txt",
            ]
        )
    # split 비어 있어도 train/val 둘 다 시도
    if not split:
        for sp in ("train", "val"):
            paths.extend(
                [
                    ds / sp / "images" / f"{stem}.jpg",
                    ds / sp / "labels" / f"{stem}.txt",
                ]
            )
    seen: set[Path] = set()
    for p in paths:
        rp = p.resolve() if p.exists() else p
        if rp in seen:
            continue
        seen.add(rp)
        if p.exists():
            p.unlink()
            n += 1
    return n


def do_flag(args):
    ds = resolve_dataset(Path(args.root) if args.root else DATASET)
    labels = iter_label_files(ds)
    if not labels:
        print(f"[flag] no labels under {ds} (images/labels 또는 train|val/labels).")
        return

    stats = {}
    split_of = {}
    for stem, lp, split in labels:
        total, water = read_counts(lp)
        stats[stem] = (total, water)
        split_of[stem] = split

    counts = [t for (t, _w) in stats.values()]
    med = statistics.median(counts)
    mad = statistics.median([abs(c - med) for c in counts]) or 1.0
    hi_thresh = max(args.min_count, med + args.z * 1.4826 * mad)
    print(
        f"[flag] root={ds}  frames={len(counts)}  median={med:.0f}  "
        f"MAD={mad:.1f}  high_thresh={hi_thresh:.0f}  water_min={args.water_min}"
    )

    REVIEW.mkdir(parents=True, exist_ok=True)
    flagged = {}
    for stem, (total, water) in stats.items():
        reasons = []
        if total >= hi_thresh:
            reasons.append(f"many_persons({total})")
        if water >= args.water_min:
            reasons.append(f"many_water({water})")
        if not reasons:
            continue
        flagged[stem] = {
            "total": total,
            "water": water,
            "reasons": reasons,
            "split": split_of.get(stem, ""),
        }
        for src in image_candidates(ds, stem, split_of.get(stem, "")):
            if src.exists():
                shutil.copy2(src, REVIEW / f"{stem}.jpg")
                break

    MANIFEST.write_text(
        json.dumps(flagged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[flag] 의심 {len(flagged)}장 → {REVIEW}")
    for stem, info in sorted(
        flagged.items(), key=lambda kv: kv[1]["total"], reverse=True
    )[:15]:
        print(f"  - {stem}.jpg  {info['reasons']}")

    if args.purge:
        removed = 0
        for stem, info in flagged.items():
            remove_stem(ds, stem, info.get("split", ""))
            removed += 1
        for f in REVIEW.glob("*.jpg"):
            f.unlink()
        if MANIFEST.exists():
            MANIFEST.unlink()
        # 남은 장수
        left = len(iter_label_files(ds))
        print(
            f"[flag] --purge: 의심 {removed}장 삭제 완료. 남은 라벨≈{left}. "
            "Colab이면 바로 학습, 로컬 flat이면 make_dataset.py 재실행."
        )
    else:
        print(
            "[flag] 다음: review/ 에서 나쁜 이미지를 삭제한 뒤 "
            "python finetune/flag_suspect.py --apply\n"
            "       또는 전부 삭제: python finetune/flag_suspect.py --purge "
            f"--root {ds}"
        )


def do_sync(args):
    """preview/ 에서 사용자가 직접 지운 프레임을 데이터셋·수집원본에서도 제거."""
    import time

    ds = resolve_dataset(Path(args.root) if args.root else DATASET)
    img_dir = ds / "images"
    lbl_dir = ds / "labels"
    prev_dir = ds / "preview"
    raw_dir = ROOT / "finetune" / "raw"

    removed = 0
    now = time.time()
    if not lbl_dir.is_dir():
        print(f"[sync] flat labels 없음: {lbl_dir} (train/val 구조면 --purge 사용)")
        return
    for lbl in sorted(lbl_dir.glob("*.txt")):
        stem = lbl.stem
        if (prev_dir / f"{stem}.jpg").exists():
            continue
        if now - lbl.stat().st_mtime < 150:
            continue
        for p in (img_dir / f"{stem}.jpg", lbl, raw_dir / f"{stem}.jpg"):
            if p.exists():
                p.unlink()
        removed += 1
        print(f"[sync] removed {stem}")
    print(f"[sync] preview에서 지운 {removed}장을 데이터셋·raw에서 제거 완료.")


def do_apply(args):
    if not MANIFEST.exists():
        print(f"[apply] {MANIFEST} 없음. 먼저 flag 단계 실행.")
        return
    ds = resolve_dataset(Path(args.root) if args.root else DATASET)
    flagged = json.loads(MANIFEST.read_text(encoding="utf-8"))

    removed = 0
    kept = 0
    for stem, info in flagged.items():
        still_in_review = (REVIEW / f"{stem}.jpg").exists()
        if still_in_review:
            kept += 1
            continue
        remove_stem(ds, stem, info.get("split", "") if isinstance(info, dict) else "")
        removed += 1
        print(f"[apply] removed {stem}")

    print(f"[apply] 제거 {removed}장, 유지 {kept}장.")
    for f in REVIEW.glob("*.jpg"):
        f.unlink()
    if MANIFEST.exists():
        MANIFEST.unlink()
    print("[apply] review/ 정리 완료.")


def main():
    ap = argparse.ArgumentParser(description="Flag suspicious auto-labeled frames")
    ap.add_argument(
        "--root",
        default="",
        help="데이터셋 루트 (기본: finetune/dataset). "
        "Colab 예: /content/gwangalli",
    )
    ap.add_argument("--z", type=float, default=3.0, help="이상치 임계(중앙값+z*MAD)")
    ap.add_argument(
        "--min-count",
        type=int,
        default=40,
        help="'사람 과다'로 볼 절대 최소 인원",
    )
    ap.add_argument(
        "--water-min",
        type=int,
        default=25,
        help="물 구역 박스 과다 임계(파도 오탐 의심)",
    )
    ap.add_argument(
        "--purge",
        action="store_true",
        help="검수 없이 의심 프레임을 즉시 전부 삭제",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="review/ 에서 지운 프레임을 데이터셋에서 제거",
    )
    ap.add_argument(
        "--sync",
        action="store_true",
        help="dataset/preview 에서 직접 지운 프레임을 데이터셋·raw에서 제거",
    )
    args = ap.parse_args()

    if args.apply:
        do_apply(args)
    elif args.sync:
        do_sync(args)
    else:
        do_flag(args)


if __name__ == "__main__":
    main()
