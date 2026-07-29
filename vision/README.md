# Vision bridge (YOLOv8 + SAHI)

Haeundae / Gwangalli **crowd-density** Node 앱과 연동되는 비전 파이프라인입니다.

## 흐름

```
이미지 → (2x 확대 + SAHI 256/25%) → ROI 안 인원
      → (선택) 호모그래피 m²
      → JSON stdout
      → src/adapters/visionAdapter.ts
      → CrowdDensityService.analyze()
      → 관광객/관리자 API·알림
```

## 설치

```bash
pip install -r requirements.txt
```

`.env`에 `VISION_PYTHON`을 넣으면 Node가 그 인터프리터를 사용합니다.

## 앱 연동 실행

```bash
# 프로젝트 루트
npm run vision:analyze -- input/screenshots/01_wide_full_beach.png

# 또는 API
curl -X POST http://localhost:3780/api/analyze/vision ^
  -H "Content-Type: application/json" ^
  -d "{\"imagePath\":\"vision/input/screenshots/01_wide_full_beach.png\"}"
```

## 면적(m²) 정책

- 기본: 앱 `config/zones.*.json`의 **유효 면적** 사용
- `vision/config/calibration.json`에서 `fieldVerified: true` + API/`--use-homography-area`일 때만 호모그래피 ROI 면적 사용

## 주요 파일

| 파일 | 역할 |
|------|------|
| `analyze_for_app.py` | 앱용 JSON CLI |
| `detect_sahi_2x.py` | 탐지 실험 |
| `density_heatmap.py` | JET 밀도맵 |
| `homography_density.py` | 픽셀→미터 |
| `config/calibration.json` | 기준점 4개 |
