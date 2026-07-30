"""의심 프레임 자동 선별 → 한 폴더(review/)에 모아 검수.

자동 라벨(prelabel/autolabel_watch) 결과 중 '파도 오탐' 같은 이상 프레임을
휴리스틱으로 골라 finetune/review/ 에 preview 사본을 모은다. 사용자는 이 한
폴더만 넘겨보며 '진짜 나쁜' 이미지를 삭제하면 된다.

휴리스틱(하나라도 걸리면 의심):
  - 사람 수가 통계적 이상치(중앙값 + z*MAD)이고 절대 하한(--min-count) 이상
  - 물 구역(세로 0.45~0.78) 박스가 과다(--water-min 이상)  ← 파도 오탐 전형

2단계 워크플로:
  1) python finetune/flag_suspect.py          # review/ 에 의심 프레임 모으기
  2) review/ 폴더를 열어 '나쁜' 이미지를 삭제 (좋은 것은 그대로 둠)
  3) python finetune/flag_suspect.py --apply   # 삭제한(=나쁜) 것만 데이터셋에서 제거

--apply 는 review/ 에서 사라진(=사용자가 지운) 프레임을 데이터셋
(images/labels/preview)에서 함께 제거한다. review/ 에 남긴 것은 그대로 유지.

검수를 건너뛰고 싶으면:
  python finetune/flag_suspect.py --purge   # 의심 프레임을 즉시 전부 삭제
주의: --purge 는 '진짜로 붐비는' 정상 프레임까지 지울 수 있다(사람 많음=의심).
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
# 기록은 review 밖에 둔다: 사용자가 review 안을 전부 지워도 --apply가 동작하도록
MANIFEST = ROOT / "finetune" / "_flagged.json"

# 광안리 고정 캠 구도의 물 구역 세로 범위(참고: realtime_safety_map.WATER_Y_TOP/BOT)
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


def do_flag(args):
    lbl_dir = DATASET / "labels"
    prev_dir = DATASET / "preview"
    labels = sorted(lbl_dir.glob("*.txt"))
    if not labels:
        print(f"[flag] no labels in {lbl_dir}. prelabel 먼저 실행.")
        return

    stats = {}
    for lp in labels:
        total, water = read_counts(lp)
        stats[lp.stem] = (total, water)

    counts = [t for (t, _w) in stats.values()]
    med = statistics.median(counts)
    mad = statistics.median([abs(c - med) for c in counts]) or 1.0
    hi_thresh = max(args.min_count, med + args.z * 1.4826 * mad)
    print(f"[flag] frames={len(counts)}  median={med:.0f}  MAD={mad:.1f}  "
          f"high_thresh={hi_thresh:.0f}  water_min={args.water_min}")

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
        flagged[stem] = {"total": total, "water": water, "reasons": reasons}
        # preview(박스) 우선, 없으면 원본이라도 복사해 검수 폴더에 항상 남긴다
        for src in (prev_dir / f"{stem}.jpg",
                    DATASET / "images" / f"{stem}.jpg",
                    ROOT / "finetune" / "raw" / f"{stem}.jpg"):
            if src.exists():
                shutil.copy2(src, REVIEW / f"{stem}.jpg")
                break

    MANIFEST.write_text(json.dumps(flagged, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[flag] 의심 {len(flagged)}장 → {REVIEW}")
    for stem, info in sorted(flagged.items(),
                             key=lambda kv: kv[1]["total"], reverse=True)[:15]:
        print(f"  - {stem}.jpg  {info['reasons']}")

    if args.purge:
        img_dir = DATASET / "images"
        removed = 0
        for stem in flagged:
            for p in (img_dir / f"{stem}.jpg", lbl_dir / f"{stem}.txt",
                      prev_dir / f"{stem}.jpg"):
                if p.exists():
                    p.unlink()
            removed += 1
        # 검수 사본·매니페스트도 정리
        for f in REVIEW.glob("*.jpg"):
            f.unlink()
        if MANIFEST.exists():
            MANIFEST.unlink()
        print(f"[flag] --purge: 의심 {removed}장을 데이터셋에서 즉시 제거. "
              "이후 make_dataset.py 실행.")
    else:
        print("[flag] 다음: review/ 폴더에서 '나쁜' 이미지를 삭제한 뒤, "
              "python finetune/flag_suspect.py --apply")


def do_sync(args):
    """preview/ 에서 사용자가 직접 지운 프레임을 데이터셋·수집원본에서도 제거.

    사용자가 dataset/preview 폴더를 보다가 나쁜 프레임을 Del로 지웠을 때:
        python finetune/flag_suspect.py --sync
    를 실행하면 해당 프레임의 images/labels/raw 파일까지 함께 삭제된다.
    (raw 까지 지워야 Colab 재라벨링에도 그 프레임이 다시 들어가지 않음)
    """
    import time

    img_dir = DATASET / "images"
    lbl_dir = DATASET / "labels"
    prev_dir = DATASET / "preview"
    raw_dir = ROOT / "finetune" / "raw"

    removed = 0
    now = time.time()
    for lbl in sorted(lbl_dir.glob("*.txt")):
        stem = lbl.stem
        if (prev_dir / f"{stem}.jpg").exists():
            continue  # preview 있음 = 유지
        # 감시기가 지금 막 처리 중인 최신 프레임 오삭제 방지(2.5분 유예)
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
    flagged = json.loads(MANIFEST.read_text(encoding="utf-8"))
    img_dir = DATASET / "images"
    lbl_dir = DATASET / "labels"
    prev_dir = DATASET / "preview"

    removed = 0
    kept = 0
    for stem in flagged:
        still_in_review = (REVIEW / f"{stem}.jpg").exists()
        if still_in_review:
            kept += 1
            continue  # 사용자가 남김 = 괜찮음
        # review 에서 지움 = 나쁨 → 데이터셋에서 제거
        for p in (img_dir / f"{stem}.jpg", lbl_dir / f"{stem}.txt",
                  prev_dir / f"{stem}.jpg"):
            if p.exists():
                p.unlink()
        removed += 1
        print(f"[apply] removed {stem}")

    print(f"[apply] 제거 {removed}장, 유지 {kept}장.")
    # 정리: 남은 review 사본과 manifest 삭제(다음 라운드 깨끗하게)
    for f in REVIEW.glob("*.jpg"):
        f.unlink()
    if MANIFEST.exists():
        MANIFEST.unlink()
    print("[apply] review/ 정리 완료. 이후 make_dataset.py 로 재분할하세요.")


def main():
    ap = argparse.ArgumentParser(description="Flag suspicious auto-labeled frames")
    ap.add_argument("--z", type=float, default=3.0, help="이상치 임계(중앙값+z*MAD)")
    ap.add_argument("--min-count", type=int, default=40,
                    help="'사람 과다'로 볼 절대 최소 인원")
    ap.add_argument("--water-min", type=int, default=25,
                    help="물 구역 박스 과다 임계(파도 오탐 의심)")
    ap.add_argument("--purge", action="store_true",
                    help="검수 없이 의심 프레임을 즉시 전부 삭제(주의: 진짜 혼잡 장면도 삭제될 수 있음)")
    ap.add_argument("--apply", action="store_true",
                    help="review/ 에서 지운 프레임을 데이터셋에서 제거")
    ap.add_argument("--sync", action="store_true",
                    help="dataset/preview 에서 직접 지운 프레임을 데이터셋·raw에서 제거")
    args = ap.parse_args()

    if args.apply:
        do_apply(args)
    elif args.sync:
        do_sync(args)
    else:
        do_flag(args)


if __name__ == "__main__":
    main()
