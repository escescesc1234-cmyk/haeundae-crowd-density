# ============================================================
# 2배 확대 + 256x256 슬라이스(겹침 25%)로 사람 탐지 후 결과 합치기
# (SAHI + YOLOv8, 초보자용 주석)
# ============================================================

from pathlib import Path
import time

import cv2
from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction
from sahi.utils.cv import visualize_object_predictions


# ------------------------------------------------------------
# 설정값
# ------------------------------------------------------------
INPUT_DIR = Path("input/screenshots")
OUTPUT_DIR = Path("output/sahi_2x_256")
MODEL_NAME = "yolov8n.pt"
CONFIDENCE = 0.25

# 이미지를 가로·세로 각각 2배로 키웁니다. (면적은 4배)
UPSCALE = 2.0

# 한 조각 크기와 겹침 비율입니다. (25% = 0.25)
# (테스트 결과 이 데이터에서는 128보다 256이 더 나았습니다)
SLICE_SIZE = 256
OVERLAP_RATIO = 0.25


def upscale_image(image_bgr, scale: float):
    """이미지를 scale배 확대합니다. (보간: INTER_CUBIC)"""
    h, w = image_bgr.shape[:2]
    new_w = int(w * scale)
    new_h = int(h * scale)
    return cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


def scale_boxes_to_original(person_preds, scale: float):
    """확대본에서 나온 박스 좌표를 원본 크기로 되돌립니다."""
    scaled = []
    for p in person_preds:
        # SAHI ObjectPrediction의 bbox는 minx, miny, maxx, maxy 입니다.
        bbox = p.bbox
        scaled.append(
            {
                "x1": bbox.minx / scale,
                "y1": bbox.miny / scale,
                "x2": bbox.maxx / scale,
                "y2": bbox.maxy / scale,
                "score": float(p.score.value),
            }
        )
    return scaled


def draw_boxes_on_original(image_bgr, boxes):
    """원본 이미지 위에 박스를 그립니다."""
    out = image_bgr.copy()
    for b in boxes:
        x1, y1, x2, y2 = int(b["x1"]), int(b["y1"]), int(b["x2"]), int(b["y2"])
        score = b["score"]
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


def detect_people_sahi(detection_model, image_bgr):
    """
    확대된 이미지에서
    256x256 / 겹침 25% 슬라이스 탐지 → 결과를 하나로 합칩니다.
    """
    result = get_sliced_prediction(
        image_bgr,
        detection_model,
        slice_height=SLICE_SIZE,
        slice_width=SLICE_SIZE,
        overlap_height_ratio=OVERLAP_RATIO,
        overlap_width_ratio=OVERLAP_RATIO,
        verbose=0,
    )

    # 클래스 이름이 person인 것만 남깁니다.
    person_preds = [
        p
        for p in result.object_prediction_list
        if str(p.category.name).lower() == "person"
    ]
    return person_preds


def main():
    images = sorted(INPUT_DIR.glob("*.png"))
    if not images:
        print(f"[오류] 이미지가 없습니다: {INPUT_DIR.resolve()}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1] SAHI+YOLO 로딩: {MODEL_NAME}")
    detection_model = AutoDetectionModel.from_pretrained(
        model_type="yolov8",
        model_path=MODEL_NAME,
        confidence_threshold=CONFIDENCE,
        device="cpu",
    )

    print(
        f"[2] 설정: {UPSCALE}배 확대 → {SLICE_SIZE}x{SLICE_SIZE}, "
        f"겹침 {int(OVERLAP_RATIO * 100)}%"
    )
    print(f"[3] 이미지 {len(images)}장 처리 시작")
    print("-" * 64)
    print(f"{'파일':<34} {'인원':>6} {'초':>8}")
    print("-" * 64)

    total = 0
    for image_path in images:
        original = cv2.imread(str(image_path))
        if original is None:
            print(f"[건너뜀] 읽기 실패: {image_path.name}")
            continue

        # 1) 2배 확대
        upscaled = upscale_image(original, UPSCALE)

        # 2) 슬라이스 탐지 + 합치기
        t0 = time.perf_counter()
        person_preds = detect_people_sahi(detection_model, upscaled)
        elapsed = time.perf_counter() - t0
        count = len(person_preds)
        total += count

        # 3) 확대본에 박스 그린 결과 저장
        vis = visualize_object_predictions(
            image=upscaled,
            object_prediction_list=person_preds,
        )
        upscaled_vis = vis["image"] if isinstance(vis, dict) else vis
        cv2.imwrite(
            str(OUTPUT_DIR / f"{image_path.stem}_2x_sahi.jpg"),
            upscaled_vis,
        )

        # 4) 좌표를 원본 크기로 되돌린 뒤 원본에도 박스 저장
        boxes_orig = scale_boxes_to_original(person_preds, UPSCALE)
        original_vis = draw_boxes_on_original(original, boxes_orig)
        cv2.imwrite(
            str(OUTPUT_DIR / f"{image_path.stem}_orig_mapped.jpg"),
            original_vis,
        )

        print(f"{image_path.name:<34} {count:>6} {elapsed:>8.2f}")

    print("-" * 64)
    print(f"{'합계':<34} {total:>6}")
    print(f"\n저장 폴더: {OUTPUT_DIR.resolve()}")
    print("  *_2x_sahi.jpg     = 2배 확대본 + 박스")
    print("  *_orig_mapped.jpg = 원본 크기에 박스 투영")


if __name__ == "__main__":
    main()
