"""사전 라벨 생성기 (자동 라벨링 = 최대 자동화).

수집 프레임에 사람 박스를 자동으로 그려 YOLO 포맷 라벨을 만든다.
교정을 최소화/생략할 수 있도록 --teacher 모드(큰 모델·고배율·촘촘 슬라이스)를
제공한다. 이 라벨로 작은 빠른 모델(yolo26s)을 학습 = 지식 증류(자동).

  - 기본 모드: FAST와 동일(yolo26s, 빠름) — 빠른 초안
  - --teacher : yolo26m + 물 3.5배/모래 2.0배 (느리지만 정확) — 교정 최소화용

출력 구조 (Ultralytics 표준):
    finetune/dataset/images/*.jpg   (원본 복사)
    finetune/dataset/labels/*.txt   (YOLO: 'class cx cy w h', 정규화, class=0=person)
    finetune/dataset/preview/*.jpg  (검수용 시각화 — 눈으로 훑고 이상한 것만 삭제)

이후 make_dataset.py 로 train/val 분할 + data.yaml + zip 생성.

사용:
    python finetune/prelabel.py --teacher            # 권장(자동, 교정 최소)
    python finetune/prelabel.py --teacher --limit 800
    python finetune/prelabel.py                       # 빠른 초안(yolo26s)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2

# 상위 폴더(vision/)를 import 경로에 추가 → realtime_safety_map 사용 가능
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import realtime_safety_map as R
from sahi import AutoDetectionModel

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "finetune" / "raw"
DEFAULT_OUT = ROOT / "finetune" / "dataset"

# 교사(teacher) 프리셋: 오프라인이라 느려도 됨 → 물 고배율·촘촘 슬라이스로 recall↑
TEACHER_MODEL_CANDIDATES = ("models/yolo26m_beach_ft.pt", "yolo26m.pt", "yolo26s.pt")
TEACHER_BANDS = (
    # (y0,y1,upscale,slice[,overlap]) — 물은 overlap↑ 로 경계 놓침↓
    (R.WATER_Y_TOP, R.WATER_Y_BOT, 3.5, 320, 0.40),
    (R.WATER_Y_BOT, 1.0, 2.0, 384, 0.22),
)


def resolve_teacher_model() -> str:
    for rel in TEACHER_MODEL_CANDIDATES:
        p = ROOT / rel
        if p.exists():
            return str(p)
    return "yolo26m.pt"  # ultralytics 자동 다운로드


def build_model(teacher: bool, override: str | None) -> AutoDetectionModel:
    if override:
        path = override
    elif teacher:
        path = resolve_teacher_model()
    else:
        path = R.resolve_fast_sahi_model()
    device = R.resolve_device()  # Colab GPU면 cuda:0, 로컬이면 cpu
    print(f"[prelabel] model={path} (teacher={teacher}) device={device}")
    return AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=path,
        confidence_threshold=R.PERSON_PROPOSAL_CONF,
        device=device,
        image_size=R.FAST_SAHI_IMGSZ,
    )


def to_yolo_line(box, w: int, h: int) -> str:
    x1, y1, x2, y2 = box[0], box[1], box[2], box[3]
    cls = int(box[5]) if len(box) >= 6 else 0  # 0=person, 1=tube(파인튜닝 모델)
    cx = ((x1 + x2) / 2.0) / w
    cy = ((y1 + y2) / 2.0) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def make_sahi_detector(model, bands):
    """SAHI 원근 밴드 탐지기(정확·느림)."""
    def _detect(img, roi_mask):
        _c, confirmed, _rej = R.detect_people_sahi_fast(model, img, roi_mask, bands=bands)
        return confirmed
    return _detect


def make_light_detector(yolo_model, upscale: float = 1.0, imgsz: int = 1920):
    """YOLO 단일 추론 탐지기(빠름·라이브용). 슬라이싱 없음.

    원본 해상도(imgsz=1920)로 그대로 추론하는 게 upscale 후 축소보다
    작은 사람을 훨씬 잘 잡는다(실측 7→20명).
    """
    def _detect(img, roi_mask):
        _c, confirmed = R.detect_people_fast(
            yolo_model, img, roi_mask, upscale=upscale, imgsz=imgsz
        )
        return confirmed
    return _detect


def process_frame(detect, fp: Path, img_dir: Path, lbl_dir: Path,
                  prev_dir: Path | None) -> int:
    """프레임 1장을 자동 라벨링: 원본 복사 + YOLO 라벨 + (선택)박스 미리보기.

    detect: (img_bgr, roi_mask) -> [ (x1,y1,x2,y2,score), ... ]
    반환값: 확정된 사람 수. 실패 시 -1.
    """
    img = cv2.imread(str(fp))
    if img is None:
        return -1
    h, w = img.shape[:2]
    # 고정 오탐 제외 구역(EXCLUDE_ZONES) 반영 마스크 (우측 하단 에어바운스 등)
    roi_mask, _ = R.make_live_roi_mask(h, w)
    confirmed = detect(img, roi_mask)

    shutil.copy2(fp, img_dir / fp.name)
    lines = [to_yolo_line(b, w, h) for b in confirmed]
    (lbl_dir / (fp.stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")

    if prev_dir is not None:
        vis = img.copy()
        for b in confirmed:
            x1, y1, x2, y2 = b[0], b[1], b[2], b[3]
            is_tube = len(b) >= 6 and int(b[5]) == 1
            # 사람=마젠타, 튜브=하늘색: 모래·바다 어디서든 눈에 띔 (검수용)
            color = (255, 200, 0) if is_tube else (255, 0, 255)
            cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        # 좌상단 인원수 배너: 썸네일에서도 과탐(파도 오탐) 프레임을 바로 식별
        txt = f"persons={len(confirmed)}"
        cv2.rectangle(vis, (0, 0), (330, 54), (0, 0, 0), -1)
        cv2.putText(vis, txt, (12, 40), cv2.FONT_HERSHEY_SIMPLEX,
                    1.2, (0, 255, 255), 3, cv2.LINE_AA)
        cv2.imwrite(str(prev_dir / fp.name), vis)
    return len(confirmed)


def main():
    ap = argparse.ArgumentParser(description="Auto pre-label collected frames")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0, help="처리 최대 장수(0=전체)")
    ap.add_argument("--step", type=int, default=1,
                    help="N장마다 1장만 처리(1분 간격은 연속 프레임이 유사 → 2~3 권장)")
    ap.add_argument("--teacher", action="store_true",
                    help="큰 모델·고배율 자동 라벨(교정 최소화, 느림)")
    ap.add_argument("--model", default=None, help="가중치 경로 강제 지정")
    ap.add_argument("--no-preview", dest="preview", action="store_false")
    ap.set_defaults(preview=True)
    args = ap.parse_args()

    bands = TEACHER_BANDS if args.teacher else R.FAST_BANDS

    src = Path(args.src)
    out = Path(args.out)
    img_dir = out / "images"
    lbl_dir = out / "labels"
    prev_dir = out / "preview"
    for d in (img_dir, lbl_dir, prev_dir):
        d.mkdir(parents=True, exist_ok=True)

    frames = sorted(src.glob("*.jpg"))
    if args.step and args.step > 1:
        frames = frames[:: args.step]
    if args.limit:
        frames = frames[: args.limit]
    if not frames:
        print(f"[prelabel] no frames in {src}")
        return

    model = build_model(args.teacher, args.model)
    detect = make_sahi_detector(model, bands)
    print(f"[prelabel] {len(frames)} frames → {out}  bands={bands}")

    for i, fp in enumerate(frames, 1):
        n = process_frame(detect, fp, img_dir, lbl_dir,
                          prev_dir if args.preview else None)
        if n < 0:
            continue
        print(f"[prelabel] {i}/{len(frames)} {fp.name} persons={n}")

    print(f"[prelabel] done. 교정 후 make_dataset.py 실행하세요.")


if __name__ == "__main__":
    main()
