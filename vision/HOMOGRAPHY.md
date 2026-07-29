# 픽셀 → 미터(호모그래피) + 명/m²

## 한 줄 요약
사진의 점 4개와, 그 점들의 **실제 미터 좌표 4개**로 `cv2.findHomography` 변환식을 만들고  
`(ROI 안 사람 수) / (ROI 면적 m²)` 로 **명/m²** 을 구합니다.

## 실행
```bash
cd vision
python homography_density.py
```

## 꼭 바꿔야 하는 값 (`homography_density.py` 상단)
1. `SRC_PIXELS` — 사진에서 찍은 기준점 4개 (픽셀 x,y)
2. `DST_METERS` — 같은 점의 실제 좌표 (미터). **지금은 예시값**
3. `ROI_PIXEL_POLYGON` — 밀도를 잴 해변·물놀이 구역
4. `PERSON_PIXELS` — 사람 위치 (나중에 YOLO/SAHI 탐지 결과로 교체)

## 기준점 잡는 팁
- 모래사장처럼 **평면에 가까운 곳**에서 잡기
- 네 점이 **넓게 펴지게** (한 직선 금지)
- 줄자/도면으로 A→B, A→D 거리를 재서 미터 좌표 작성

## 결과물
- `output/homography_density/*_metric_density.jpg` — ROI·기준점·격자 밀도 시각화
- `output/homography_density/*_metric_density.json` — 면적·명/m² 숫자

## 주의
호모그래피는 바닥이 평평하다고 가정합니다.  
파도·둑·높은 구조물에는 오차가 커질 수 있습니다.
