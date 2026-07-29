# ============================================================
# 해변 사진에서 '사람'만 찾아 개수를 출력하고,
# 네모 박스가 그려진 결과 이미지를 저장하는 초보자용 스크립트입니다.
# 사용 모델: Ultralytics YOLOv8 (COCO 사전학습 가중치)
# ============================================================

# ultralytics 패키지에서 YOLO 클래스를 가져옵니다. (YOLOv8 모델 사용)
from ultralytics import YOLO

# 파일/폴더 경로를 다루기 쉽게 해주는 표준 라이브러리입니다.
from pathlib import Path

# OpenCV: 이미지 읽기/쓰기와 박스 그리기에 사용합니다. (cv2로 부릅니다)
import cv2


# ------------------------------------------------------------
# 1) 사용자가 바꿀 설정값
# ------------------------------------------------------------

# 탐지할 해변 사진 경로입니다. (본인 사진 파일명으로 바꿔주세요)
IMAGE_PATH = Path("input/beach.jpg")

# 결과 이미지를 저장할 폴더입니다.
OUTPUT_DIR = Path("output")

# 사용할 YOLOv8 가중치 파일입니다.
# yolov8n.pt = nano(가장 가볍고 빠름), s/m/l/x 로 갈수록 정확도↑·속도↓
MODEL_NAME = "yolov8n.pt"

# 이 점수(0~1)보다 낮은 탐지는 버립니다. 낮출수록 더 많이 잡히지만 오탐도 늘어납니다.
CONFIDENCE = 0.25

# COCO 데이터셋에서 'person' 클래스 번호는 0입니다. (사람만 탐지)
PERSON_CLASS_ID = 0


# ------------------------------------------------------------
# 2) 메인 실행 함수
# ------------------------------------------------------------
def main():
    # 입력 사진이 실제로 있는지 확인합니다.
    if not IMAGE_PATH.exists():
        # 사진이 없으면 초보자가 바로 이해할 수 있게 안내하고 종료합니다.
        print(f"[오류] 사진을 찾을 수 없습니다: {IMAGE_PATH.resolve()}")
        print("→ vision/input/ 폴더에 해변 사진을 넣고, IMAGE_PATH를 맞춰주세요.")
        return

    # 결과 저장 폴더가 없으면 새로 만듭니다. (있으면 그대로 둡니다)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # YOLOv8 모델을 불러옵니다. (처음 실행 시 .pt 파일을 자동 다운로드합니다)
    print(f"[1/4] 모델 로딩 중... ({MODEL_NAME})")
    model = YOLO(MODEL_NAME)

    # 사진에서 사람(class=0)만 탐지합니다.
    # classes=[0]  → person만 남김
    # conf=...     → 신뢰도 임계값
    # verbose=False → 불필요한 로그를 줄임
    print(f"[2/4] 사람 탐지 중... ({IMAGE_PATH})")
    results = model.predict(
        source=str(IMAGE_PATH),
        classes=[PERSON_CLASS_ID],
        conf=CONFIDENCE,
        verbose=False,
    )

    # predict()는 결과 리스트를 반환합니다. 사진은 1장이므로 첫 결과만 사용합니다.
    result = results[0]

    # 탐지된 박스(사람) 개수를 셉니다.
    # result.boxes가 None이면 0명으로 처리합니다.
    person_count = 0 if result.boxes is None else len(result.boxes)

    # 찾은 사람 수를 화면에 출력합니다.
    print(f"[3/4] 찾은 사람 수: {person_count}명")

    # Ultralytics가 그린 박스 이미지를 NumPy 배열로 받습니다. (BGR 색상)
    # plot()은 박스 + 라벨(person, 점수)이 그려진 이미지를 반환합니다.
    annotated_bgr = result.plot()

    # 저장할 파일 이름을 만듭니다. 예: beach_people.jpg
    output_name = f"{IMAGE_PATH.stem}_people.jpg"
    output_path = OUTPUT_DIR / output_name

    # OpenCV로 결과 이미지를 파일로 저장합니다.
    # cv2.imwrite는 True/False를 반환합니다.
    saved = cv2.imwrite(str(output_path), annotated_bgr)

    # 저장 성공/실패를 알려줍니다.
    if saved:
        print(f"[4/4] 결과 이미지 저장 완료: {output_path.resolve()}")
    else:
        print(f"[오류] 결과 이미지 저장 실패: {output_path.resolve()}")

    # 각 사람 박스의 신뢰도도 함께 보고 싶을 때 참고용으로 출력합니다.
    if person_count > 0:
        print("--- 탐지 상세 ---")
        # enumerate로 몇 번째 사람인지 번호를 붙입니다. (1부터 시작)
        for i, box in enumerate(result.boxes, start=1):
            # box.conf는 텐서라서 float로 변환합니다.
            score = float(box.conf[0])
            # xyxy = [x1, y1, x2, y2] 좌상단~우하단 좌표입니다.
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            print(
                f"  사람 {i}: 신뢰도={score:.2f}, "
                f"박스=({int(x1)}, {int(y1)}) ~ ({int(x2)}, {int(y2)})"
            )


# 이 파일을 직접 실행했을 때만 main()을 호출합니다.
# (다른 파일에서 import할 때는 자동 실행되지 않습니다)
if __name__ == "__main__":
    main()
