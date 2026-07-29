# ============================================================
# YOLO 단독 vs SAHI+YOLO 사람 탐지 비교 테스트 (초보자용)
# ============================================================

from pathlib import Path
import time

import cv2
from ultralytics import YOLO

# SAHI: 이미지를 조각내어 작은 객체 탐지를 돕는 라이브러리입니다.
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction
from sahi.utils.cv import visualize_object_predictions


# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------
INPUT_DIR = Path("input/screenshots")
OUTPUT_DIR = Path("output/compare_test")
MODEL_NAME = "yolov8n.pt"
CONFIDENCE = 0.25
PERSON_CLASS_ID = 0

# SAHI 슬라이스 크기 (작을수록 소형 객체에 유리, 대신 느려짐)
SLICE_SIZE = 256
OVERLAP_RATIO = 0.2


def draw_yolo_boxes(image_bgr, boxes):
    """YOLO 박스를 OpenCV로 직접 그립니다."""
    out = image_bgr.copy()
    if boxes is None:
        return out
    for box in boxes:
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
        score = float(box.conf[0])
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            out,
            f"person {score:.2f}",
            (x1, max(15, y1 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    return out


def run_yolo(model, image_path: Path):
    """YOLO 단독 탐지."""
    t0 = time.perf_counter()
    results = model.predict(
        source=str(image_path),
        classes=[PERSON_CLASS_ID],
        conf=CONFIDENCE,
        verbose=False,
    )
    elapsed = time.perf_counter() - t0
    result = results[0]
    count = 0 if result.boxes is None else len(result.boxes)
    image = cv2.imread(str(image_path))
    annotated = draw_yolo_boxes(image, result.boxes)
    return count, elapsed, annotated


def run_sahi(detection_model, image_path: Path):
    """SAHI + YOLO 슬라이스 탐지."""
    t0 = time.perf_counter()
    result = get_sliced_prediction(
        str(image_path),
        detection_model,
        slice_height=SLICE_SIZE,
        slice_width=SLICE_SIZE,
        overlap_height_ratio=OVERLAP_RATIO,
        overlap_width_ratio=OVERLAP_RATIO,
        verbose=0,
    )
    elapsed = time.perf_counter() - t0

    # person만 남깁니다. (모델에서 이미 필터해도 안전하게 한 번 더)
    person_preds = [
        p
        for p in result.object_prediction_list
        if str(p.category.name).lower() == "person"
    ]
    count = len(person_preds)

    image = cv2.imread(str(image_path))
    # SAHI 공식 시각화 유틸로 박스를 그립니다.
    vis = visualize_object_predictions(
        image=image,
        object_prediction_list=person_preds,
    )
    # 버전마다 dict 또는 ndarray를 반환할 수 있어 둘 다 처리합니다.
    annotated = vis["image"] if isinstance(vis, dict) else vis
    return count, elapsed, annotated


def main():
    images = sorted(INPUT_DIR.glob("*.png"))
    if not images:
        print(f"[오류] 이미지가 없습니다: {INPUT_DIR.resolve()}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1] YOLO 모델 로딩: {MODEL_NAME}")
    yolo_model = YOLO(MODEL_NAME)

    print("[2] SAHI DetectionModel 로딩")
    sahi_model = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=MODEL_NAME,
        confidence_threshold=CONFIDENCE,
        device="cpu",
    )

    print(f"[3] 테스트 이미지 {len(images)}장")
    print("-" * 72)
    print(f"{'파일':<34} {'YOLO':>6} {'초':>7} {'SAHI':>6} {'초':>7} {'차이':>6}")
    print("-" * 72)

    total_yolo = 0
    total_sahi = 0

    for image_path in images:
        yolo_count, yolo_sec, yolo_img = run_yolo(yolo_model, image_path)
        sahi_count, sahi_sec, sahi_img = run_sahi(sahi_model, image_path)

        total_yolo += yolo_count
        total_sahi += sahi_count
        diff = sahi_count - yolo_count

        stem = image_path.stem
        cv2.imwrite(str(OUTPUT_DIR / f"{stem}_yolo.jpg"), yolo_img)
        cv2.imwrite(str(OUTPUT_DIR / f"{stem}_sahi.jpg"), sahi_img)

        print(
            f"{image_path.name:<34} {yolo_count:>6} {yolo_sec:>7.2f} "
            f"{sahi_count:>6} {sahi_sec:>7.2f} {diff:>+6}"
        )

    print("-" * 72)
    print(f"{'합계':<34} {total_yolo:>6} {'':>7} {total_sahi:>6} {'':>7} {total_sahi - total_yolo:>+6}")
    print(f"\n결과 이미지 저장 위치: {OUTPUT_DIR.resolve()}")
    print(f"설정: conf={CONFIDENCE}, slice={SLICE_SIZE}, overlap={OVERLAP_RATIO}")


if __name__ == "__main__":
    main()
