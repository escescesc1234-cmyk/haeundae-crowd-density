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

## 실시간 안전지도 스트림

YouTube 라이브(기본: 광안대교 캠) → **40×15 격자** 안전지도 → MJPEG 스트림.

```bash
cd vision
pip install -r requirements.txt
python realtime_safety_map.py
# 격자 변경 예: python realtime_safety_map.py --cell-w 40 --cell-h 15
# 또는 프로젝트 루트
npm run vision:realtime
```

- UI: http://127.0.0.1:8790/  (영상 + 상태/경고만, 단순 UI)
- 스트림: http://127.0.0.1:8790/stream  (하단 KST 시각 표시)
- 상태: http://127.0.0.1:8790/api/status
- 앱 메타: GET http://localhost:3780/api/vision/realtime

빨강 격자 발생 시 관광객/관리자 경고 메시지를 콘솔·UI·스트림 오버레이에 표시합니다.

```bash
python finetune_and_safety_map.py
```

1. 새 원본을 SAHI로 자동 라벨(pseudo-label)
2. YOLOv8n fine-tune (소량 데이터면 탐지 붕괴 시 기본 가중치 fallback)
3. 동일 규칙 안전지도 출력 → `output/safety_map_finetuned/*_safety_map_ft.jpg`

```bash
python safety_map.py
```

- 출력: `output/safety_map/*_safety_map.jpg`
- 규칙: `<4` 초록 / `4~6` 노랑 / `≥6` 빨강 (각 50% 투명)
- 위험(빨강) 칸 ≥1 → 콘솔 + 이미지 하단에 관광객/관리자 경고
- 앱: `POST /api/analyze/vision` 응답 `alerts` 필드와 동일 문구

위험 배너 데모:

```bash
python demo_danger_alert.py
```

| 파일 | 역할 |
|------|------|
| `analyze_for_app.py` | 앱용 JSON CLI |
| `detect_sahi_2x.py` | 탐지 실험 |
| `density_heatmap.py` | JET 밀도맵 |
| `homography_density.py` | 픽셀→미터 |
| `config/calibration.json` | 기준점 4개 |
