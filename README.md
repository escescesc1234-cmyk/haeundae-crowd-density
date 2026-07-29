# 광안리 해수욕장 군중 밀집도 분석 모듈

AI LAB 해수욕장 안전 관리 애플리케이션에 연결하기 위한 **구역별 인구 밀도 계산·3단계 위험 등급 판정** 모듈입니다.

> **중요:** 밀도·등급은 절대적인 사고 예측이 아니라 **현장 판단을 돕는 참고 정보**입니다.  
> 초기 임계값 4.0 / 5.0 / 6.0명/㎡는 법적·공식적 절대 기준이 **아닙니다**.  
> 실제 운영 전 현장 실험과 안전 전문가 검토가 필요합니다.  
> 얼굴 인식·개인 신원 확인 기능은 포함하지 않습니다.

## 기능 요약

- 광안리 해수욕장을 구역별로 나누어 유효 면적(㎡) 관리
- 수동 입력 / 테스트 데이터 / CCTV 감지 어댑터로 인원수 반영
- 원본 밀도·시간 보정 밀도 계산
- 안전·혼잡·위험 3단계 판정 + 임계 밀도(5.0) 도달 시 사전 조치 트리거
- **실시간 알림**: 관광객/관리자 메시지 생성, 우선순위, 중복 방지, 에스컬레이션
- **방문객 예측**: 기상·이벤트·요일·과거 방문객 기반 시간대별 예상 인원/밀도
- **시뮬레이션·백테스트**: 날짜 선택 재현, 전날 데이터 기준 예측 검증
- 임계값 설정 파일·관리자 API로 조정 및 변경 이력 기록
- 히스테리시스·최소 지속 시간으로 등급 플리커링 완화
- 관광객용 / 관리자용 화면 및 JSON API 제공

## 실행 방법

```bash
npm install
npm test
npm run analyze
npm run dev
```

- 관광객 화면: http://localhost:3780/tourist.html
- 관리자 화면: http://localhost:3780/admin.html

## 기상청 API 환경 변수

프로젝트 루트에 `.env`를 두고 키를 넣으면 서버 시작 시 자동 로드됩니다.
키가 없으면 모의 데이터를 사용합니다.

```bash
copy .env.example .env
# .env 파일에 KMA_SERVICE_KEY=발급받은_인증키 입력 후
npm run dev
```

선택 변수: `KMA_API_BASE_URL`, `KMA_BASE_TIME`(비우면 현재 시각 기준 자동), `KMA_NX`, `KMA_NY`  
예측 상세 설계: `FORECAST.md`

## 실제 데이터 소스 연동

### CCTV

| 소스 | 설명 | ENV 키 | 신청처 |
|------|------|--------|--------|
| **부산 ITS 교통 CCTV** | 영상 스트림 URL + 위경도 제공 | `BUSAN_ITS_API_KEY` | [data.go.kr/15120867](https://www.data.go.kr/data/15120867/openapi.do) |
| **해운대구 CCTV API** | 구역별 CCTV 주소·위경도 (2,188대) | `HAEUNDAE_CCTV_API_KEY` | [data.go.kr/15070811](https://www.data.go.kr/data/15070811/openapi.do) |
| **AI-Hub 이안류 데이터셋** | 해운대 4대 카메라, HD 110,000장+ | — (다운로드) | [aihub.or.kr/71297](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71297) |
| **AI-Hub 군중 특성 데이터셋** | 228,195장 + MP4 400개, 밀집도 라벨 | — (다운로드) | [aihub.or.kr/71368](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71368) |
| **YouTube 4K 라이브캠** | 해운대 실시간 스트리밍 | — | [채널](https://www.youtube.com/channel/UCZsIhpfgnO7nNrDWu7dj89g) |

> 키를 `.env`에 입력하지 않으면 각 어댑터는 자동으로 **Mock 모드**로 동작합니다.

### 공영주차장

| 소스 | 설명 | ENV 키 | 신청처 |
|------|------|--------|--------|
| **부산시설공단 주차 API** | 실시간 주차가능대수 (7개소) | `BUSAN_PARKING_API_KEY` | [data.go.kr/15157490](https://www.data.go.kr/data/15157490/openapi.do) |

해운대 해수욕장 주변 공영주차장 (총 7개소, 751면):
미포(97) · 해운대광장(120) · 동백사거리(36) · 동백(71) · 동백공원(129) · 문탠로드(98) · 송림공원(200)

주차 만차율 ≥ 85% 주차장이 전체 70% 이상 → `AuxiliaryRiskFactors.entranceCongestion = true` 자동 설정

### 새로운 API 엔드포인트

```
GET /api/cctv/busan-its?beach=haeundae      # ITS 교통 CCTV 목록
GET /api/cctv/haeundae-district?type=parking # 해운대구 CCTV (주차장 필터)
GET /api/cctv/aihub-cameras?beach=haeundae  # AI-Hub 카메라 메타
GET /api/parking/status                      # 공영주차장 전체 현황
GET /api/parking/status/:parkingId           # 개별 주차장 현황
GET /api/parking/auxiliary-risk              # 주차 혼잡 → AuxiliaryRiskFactors
```

### 해운대 해수욕장 구역 설정

`config/zones.haeundae.json` — 해수욕장 7구역 + 공영주차장 3구역 포함.  
주차장 구역은 임계값 오버라이드 적용 (점유율 기준: 혼잡 0.6 / 임계 0.8 / 위험 1.0).

## 해운대 밀도 서비스 연동

이 저장소는 **밀도 API 서버**이면서, 관광객/관리자 UI가 `DensityApiClient`로 같은 HTTP 계약을 소비합니다.  
다른 앱에서도 엔진을 복사하지 말고 아래 API만 호출하세요.

### 실행 순서

1. 밀도 서비스 기동
   ```bash
   npm install
   copy .env.example .env   # DENSITY_API_BASE_URL=http://localhost:3780
   npm run dev
   ```
2. 헬스체크
   ```bash
   curl http://localhost:3780/api/health
   # → {"ok":true,"service":"haeundae-crowd-density",...}
   ```
3. 1차 연동(수동 분석)
   ```bash
   curl -X POST http://localhost:3780/api/analyze/manual ^
     -H "Content-Type: application/json" ^
     -d "{\"zoneId\":\"GWANGALLI-ZONE-CENTER\",\"detectedPeople\":800,\"measuredAt\":\"2026-07-29T12:00:00.000Z\",\"notify\":false}"
   ```
4. UI 확인
   - 관광객: http://localhost:3780/tourist.html (지도·예측 등)
   - 관리자: http://localhost:3780/admin.html → 「밀도 API · 구역 현황」 패널
5. (선택) 비전 안전지도 — Python 의존성 + `VISION_PYTHON` 설정 후 UI의 「비전 안전지도」 또는
   `POST /api/analyze/vision`

### HTTP 클라이언트

| 위치 | 용도 |
|------|------|
| `src/client/densityApiClient.ts` | Node/TS (`health`, `analyzeManual`, `getTouristZones`, `analyzeVision` …) |
| `public/density-api-client.js` | 브라우저 UI |

```ts
import { DensityApiClient } from "haeundae-crowd-density";

const client = new DensityApiClient({
  baseUrl: process.env.DENSITY_API_BASE_URL ?? "http://localhost:3780",
});
await client.health();
const result = await client.analyzeManual({
  zoneId: "GWANGALLI-ZONE-CENTER",
  detectedPeople: 800,
  measuredAt: new Date().toISOString(),
  notify: false,
});
// result.riskLevel, result.detectedPeople, result.adjustedDensity
```

환경변수: `DENSITY_API_BASE_URL` (기본 `http://localhost:3780`), 서버 포트 `PORT`.  
타임아웃: manual 10s · vision 180s. 연결 실패 시 UI에 **「밀도 분석 서비스 연결 실패」** 표시.

다른 프로젝트 복붙용 프롬프트: [`docs/INTEGRATION-PROMPT.md`](docs/INTEGRATION-PROMPT.md)

### 모듈 import (프로세스 내 연동)

```ts
import {
  CrowdDensityService,
  NotificationService,
  sharedNotificationService,
} from "haeundae-crowd-density";

// 분석 + 알림
const result = await crowdService.analyzeAndNotify({
  zoneId: "GWANGALLI-ZONE-CENTER",
  detectedPeople: 800,
  measuredAt: new Date().toISOString(),
});
```

## 디렉터리 구조

```
config/          임계값·구역 정의
src/density/     계산·스무징·히스테리시스·등급 판정
src/adapters/    수동/CCTV/비전 입력 어댑터
src/client/      DensityApiClient (HTTP 소비 전용)
src/views/       관광객·관리자 뷰 모델
src/api/         Express API
vision/          YOLOv8+SAHI 사람 탐지·밀도맵·호모그래피 (Python)
public/          UI (+ density-api-client.js)
tests/           단위·경계값 테스트
data/            테스트 측정 데이터
```

## 비전 파이프라인 연동 (YOLOv8 + SAHI)

스크린샷/프레임에서 사람을 탐지한 뒤, 기존 밀도 엔진(`CrowdDensityService`)에 주입합니다.

```bash
# Python 의존성 (최초 1회)
cd vision
pip install -r requirements.txt
cd ..

# CLI: 비전 분석 → 밀도 등급
npm run vision:analyze -- vision/input/screenshots/01_wide_full_beach.png

# API (서버 실행 중)
# POST /api/analyze/vision
# { "imagePath": "vision/input/screenshots/01_wide_full_beach.png", "zoneId": "GWANGALLI-ZONE-CENTER" }
```

열지도 등 산출물: `http://localhost:3780/vision-output/...`  
상세: `vision/README.md`
