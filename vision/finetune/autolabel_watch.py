"""자동 라벨 감시기 (수집과 동시에 박스 자동 생성).

finetune/raw/ 를 주기적으로 감시하여 아직 처리하지 않은 프레임이 생기면
자동으로 사람 박스를 그려 저장한다. 수집기(collect_finetune_frames.py)와 함께
켜 두면, 새 프레임이 들어올 때마다 박스가 씌워진 파일이 만들어진다.

출력(Ultralytics 표준):
    finetune/dataset/images/*.jpg   원본 복사
    finetune/dataset/labels/*.txt   YOLO 라벨(class cx cy w h)
    finetune/dataset/preview/*.jpg  박스 그린 검수 이미지  ← 여기서 박스 확인

이미 처리한 프레임(labels/에 .txt 존재)은 건너뛰므로, 시작 시 백로그를 먼저
처리한 뒤 새 프레임만 계속 이어서 처리한다.

라이브 기본값은 YOLO 단일 추론(--light, 빠름)이라 수집 속도를 따라간다.
더 정확한 라벨이 필요하면 --sahi(원근 밴드) 또는 --teacher(yolo26m)로 실행하되
CPU 경쟁이 심하면 수집 속도를 못 따라갈 수 있다(밀린 프레임은 나중에 처리됨).

사용:
    python finetune/autolabel_watch.py                 # 빠름(YOLO 단일, 라이브용)
    python finetune/autolabel_watch.py --sahi          # 원근 밴드 SAHI(정확·느림)
    python finetune/autolabel_watch.py --teacher       # yolo26m(가장 정확·가장 느림)
    python finetune/autolabel_watch.py --interval 30
    python finetune/autolabel_watch.py --once          # 백로그만 한 번 처리하고 종료
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import prelabel as P
import realtime_safety_map as R
from ultralytics import YOLO


def pending_frames(src: Path, lbl_dir: Path) -> list[Path]:
    """아직 라벨(.txt)이 없는 원본 프레임 목록."""
    out = []
    for fp in sorted(src.glob("*.jpg")):
        if not (lbl_dir / (fp.stem + ".txt")).exists():
            out.append(fp)
    return out


def main():
    ap = argparse.ArgumentParser(description="Watch raw/ and auto-box new frames")
    ap.add_argument("--src", default=str(P.DEFAULT_SRC))
    ap.add_argument("--out", default=str(P.DEFAULT_OUT))
    ap.add_argument("--interval", type=float, default=15.0, help="감시 주기(초)")
    ap.add_argument("--sahi", action="store_true",
                    help="원근 밴드 SAHI로 정확 라벨(느림)")
    ap.add_argument("--teacher", action="store_true",
                    help="큰 모델(yolo26m)+밴드로 가장 정확(가장 느림)")
    ap.add_argument("--upscale", type=float, default=1.0,
                    help="라이트 모드 YOLO 입력 확대배율(원본 1.0 권장)")
    ap.add_argument("--imgsz", type=int, default=1920,
                    help="라이트 모드 추론 해상도(원본 1920 권장)")
    ap.add_argument("--model", default=None, help="가중치 경로 강제 지정")
    ap.add_argument("--once", action="store_true", help="백로그만 처리하고 종료")
    args = ap.parse_args()

    use_bands = args.sahi or args.teacher
    src = Path(args.src)
    out = Path(args.out)
    img_dir = out / "images"
    lbl_dir = out / "labels"
    prev_dir = out / "preview"
    for d in (img_dir, lbl_dir, prev_dir):
        d.mkdir(parents=True, exist_ok=True)

    if use_bands:
        model = P.build_model(args.teacher, args.model)
        bands = P.TEACHER_BANDS if args.teacher else R.FAST_BANDS
        detect = P.make_sahi_detector(model, bands)
        mode = "TEACHER" if args.teacher else "SAHI"
    else:
        path = args.model or R.resolve_fast_sahi_model()
        print(f"[watch] light YOLO model={path}")
        detect = P.make_light_detector(YOLO(path), upscale=args.upscale,
                                       imgsz=args.imgsz)
        mode = f"LIGHT(up{args.upscale},imgsz{args.imgsz})"

    print(f"[watch] src={src}  interval={args.interval}s  mode={mode}")
    print(f"[watch] preview(박스) → {prev_dir}")

    total = 0
    while True:
        todo = pending_frames(src, lbl_dir)
        if todo:
            print(f"[watch] {len(todo)} new frame(s) to label")
            for fp in todo:
                n = P.process_frame(detect, fp, img_dir, lbl_dir, prev_dir)
                if n < 0:
                    print(f"[watch] skip(read fail) {fp.name}")
                    continue
                total += 1
                print(f"[watch] boxed {fp.name} persons={n} (total={total})")
        if args.once:
            print(f"[watch] once done. labeled={total}")
            break
        time.sleep(max(2.0, args.interval))


if __name__ == "__main__":
    main()
