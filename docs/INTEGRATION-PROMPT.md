# 다른 프로젝트 연동용 프롬프트 (복붙용)

아래 블록 전체를 **다른 프로젝트 Cursor 채팅**에 붙여넣으세요.  
`[ ]` 안만 본인 프로젝트 정보로 바꾼 뒤 실행하면 됩니다.

---

## 📋 복사해서 쓸 프롬프트

```text
당신은 시니어 풀스택 연동 엔지니어다. 할루시네이션 없이, 아래 "해운대/광안리 군중밀도 분석 서비스"와
현재 워크스페이스 프로젝트를 실제로 연결하는 코드를 구현하라.

# 목표
현재 프로젝트(관광객/관리자 앱·대시보드·모바일·다른 백엔드 등)에서
해운대 군중밀도 분석 API를 호출해 다음을 화면에 보여준다.
1) 구역별 위험 등급(안전/혼잡/위험)
2) 인원수·밀도(명/m²)
3) (선택) 비전 안전지도 이미지 + 위험 격자 경고(관광객/관리자 메시지)

절대 새 밀도 엔진을 다시 만들지 마라. 외부 서비스 HTTP API를 소비하는 어댑터만 작성한다.

# 외부 서비스 정보
- GitHub: https://github.com/escescesc1234-cmyk/haeundae-crowd-density
- 로컬 기본 URL: http://localhost:3780
- 헬스체크: GET /api/health  → { ok: true, service: "haeundae-crowd-density" }
- 기본 구역 ID: GWANGALLI-ZONE-CENTER
- 임계값(참고): 주의(혼잡) 4명/m², 위험 6명/m²
- 데이터 출처 중 비전: dataSource = "vision_yolo_sahi"

# 반드시 사용할 API

## A. 수동/서버 밀도 분석 (가장 단순, 권장 1차 연동)
POST /api/analyze/manual
Content-Type: application/json
{
  "zoneId": "GWANGALLI-ZONE-CENTER",
  "detectedPeople": 800,
  "measuredAt": "<ISO8601>",
  "notify": false
}
응답: DensityAnalysisResult
- zoneId, zoneName, riskLevel ("안전"|"혼잡"|"위험"|...)
- detectedPeople, rawDensity, adjustedDensity
- touristSummary / adminSummary
- recommendedActions, warnings, errors

## B. 최신 결과 조회
GET /api/results
GET /api/tourist/zones
GET /api/tourist/beach
GET /api/admin/zones

## C. 비전(YOLO+SAHI) 안전지도 연동 (2차)
POST /api/analyze/vision
{
  "imagePath": "vision/input/screenshots/01_wide_full_beach.png",
  "zoneId": "GWANGALLI-ZONE-CENTER",
  "skipHysteresis": true,
  "notify": false
}
응답 핵심:
{
  "ok": true,
  "analysis": { ... DensityAnalysisResult ... },
  "alerts": {
    "hasDanger": boolean,
    "dangerCellCount": number,
    "touristMessage": string|null,
    "managerMessage": string|null
  },
  "vision": {
    "safetyMapRelativePath": "vision/output/safety_map/....jpg",
    "heatmapRelativePath": "vision/output/app_bridge/....jpg",
    "roiPersonCount": number,
    "maxGridDensityPerM2": number
  }
}

안전지도/열지도 이미지 URL:
- http://localhost:3780/vision-output/safety_map/<파일명>
- http://localhost:3780/vision-output/app_bridge/<파일명>
(서버가 /vision-output 으로 vision/output 을 static 제공)

위험(빨강 격자 ≥1)일 때 메시지(고정 문구, 변경 금지):
- 관광객: "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요."
- 관리자: "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요."

## D. 대시보드(선택)
GET /api/waveguard/dashboard

## E. 실시간 AI 모델 (YOLO 파인튜닝 가중치, 3차)
밀도 서버(3780) + 실시간 비전(8790)이 떠 있어야 한다.
기동: 밀도 `npm run dev` / 비전 `npm run vision:realtime`
가중치: vision/models/yolo26s_beach_ft.pt (person / tube)

메타·계약 (3780만으로 충분):
GET /api/vision/realtime
→ uiUrl, streamUrl, statusUrl, modelInfoUrl, proxiedStatusPath, proxiedModelInfoPath, alerts

상태·모델 (3780 프록시 — 타 앱 baseUrl 하나만 쓰면 됨):
GET /api/vision/realtime/status   → personCount, tubeCount, estimatedTotal, maxGridDensityPerM2 …
GET /api/vision/realtime/model    → 로드된 가중치 경로·클래스

MJPEG 스트림은 브라우저 <img> / video 소스로 직접:
- http://127.0.0.1:8790/stream          (안전지도 격자)
- http://127.0.0.1:8790/stream/yolo     (FAST 박스)
- http://127.0.0.1:8790/stream/sahi256  (리콜 모니터)

DensityApiClient 메서드: getRealtimeVision(), getRealtimeVisionStatus(), getRealtimeVisionModel()
CORS: 밀도·비전 서버 모두 기본 CORS_ORIGINS=* (다른 포트 앱에서 fetch/img 가능)

# 현재 프로젝트 정보 (채워넣기)
- 프로젝트 종류: [예: React Native / Next.js / Flutter / Express]
- UI 대상: [관광객 | 관리자 | 둘 다]
- 이 프로젝트가 이미 쓰는 API base URL 패턴: [예: process.env.API_URL]
- 연동 우선순위: [1차 manual / 2차 vision / 3차 realtime AI]
- 밀도 서비스 주소: [기본 http://localhost:3780 또는 배포 URL]

# 구현 요구사항
1. `DensityApiClient` (또는 동등한 모듈) 생성
   - baseUrl 설정 가능
   - health(), analyzeManual(), getTouristZones(), analyzeVision()
   - (3차) getRealtimeVision(), getRealtimeVisionStatus()
2. 환경변수 추가
   - DENSITY_API_BASE_URL=http://localhost:3780
3. UI 연동
   - 관광객 화면: riskLevel + touristMessage(alerts.hasDanger 시) + 안전지도 이미지(있으면)
   - 관리자 화면: riskLevel + density + managerMessage + 안전지도/열지도
   - (3차) 실시간: streamUrl을 img src로, status의 estimatedTotal/위험 격자 표시
4. 실패 처리
   - 서비스 다운 시 사용자에게 "밀도 분석 서비스 연결 실패" 표시
   - 타임아웃 권장: manual 10s, vision 180s, realtime status 8s
5. 타입을 현재 스택에 맞게 정의하되, 필드명은 위 계약과 동일하게 유지
6. README에 "해운대 밀도 서비스 연동" 섹션 추가 (실행 순서 포함)

# 실행/검증 체크리스트 (직접 수행)
1. 밀도 서비스가 떠 있는지 확인: curl http://localhost:3780/api/health
   - 안 떠 있으면 사용자에게
     cd haeundae-crowd-density && npm run dev
     (비전 사용 시 vision/requirements.txt pip 설치 + VISION_PYTHON 설정)
     을 안내하고, 가능하면 health 성공할 때까지 대기/재시도
2. analyzeManual 샘플 호출 성공 확인
3. 현재 프로젝트 UI에서 결과가 보이는지 확인
4. (선택) analyzeVision 호출 후 alerts + safety map URL 표시 확인
5. (3차 AI) npm run vision:realtime 후
   curl http://localhost:3780/api/vision/realtime/model
   curl http://localhost:3780/api/vision/realtime/status

# 금지
- 밀도/위험등급 공식을 추측해서 재구현하지 말 것
- API 경로/필드명을 임의로 바꾸지 말 것
- .env 실키를 커밋하지 말 것

# 완료 기준
- 현재 프로젝트에서 localhost:3780(또는 지정 URL)과 통신 성공
- 관광객/관리자 중 요청된 UI에 등급·메시지·(가능하면)안전지도가 표시됨
- 연동 코드 위치와 사용법을 짧게 보고
```

---

## 상대 프로젝트에서 밀도 서비스를 띄우는 방법

다른 프로젝트 연동 전에 이 저장소에서:

```bash
cd haeundae-crowd-density
npm install
npm run dev
# http://localhost:3780
```

비전 분석·실시간 AI까지 쓰려면:

```bash
cd vision
pip install -r requirements.txt
# 프로젝트 루트 .env 에 VISION_PYTHON=... 설정
# 가중치: vision/models/yolo26s_beach_ft.pt 존재 확인
npm run vision:realtime   # http://127.0.0.1:8790
```

---

## 최소 연동 예시 (fetch)

```js
const BASE = process.env.DENSITY_API_BASE_URL ?? "http://localhost:3780";

// 1) 수동 분석
const manual = await fetch(`${BASE}/api/analyze/manual`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    zoneId: "GWANGALLI-ZONE-CENTER",
    detectedPeople: 800,
    measuredAt: new Date().toISOString(),
  }),
}).then((r) => r.json());

// 2) 비전 분석
const vision = await fetch(`${BASE}/api/analyze/vision`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    imagePath: "vision/input/screenshots/01_wide_full_beach.png",
    zoneId: "GWANGALLI-ZONE-CENTER",
    skipHysteresis: true,
  }),
}).then((r) => r.json());

const safetyMapUrl = vision.vision?.safetyMapRelativePath
  ? `${BASE}/vision-output/${vision.vision.safetyMapRelativePath.replace(/^vision\/output\//, "")}`
  : null;

// 3) 실시간 AI 모델 (3780 프록시 + 8790 스트림)
const rt = await fetch(`${BASE}/api/vision/realtime`).then((r) => r.json());
const status = await fetch(`${BASE}/api/vision/realtime/status`).then((r) => r.json());
// <img src={rt.streamUrl} />  // MJPEG 안전지도
// status.estimatedTotal, status.personCount, status.maxGridDensityPerM2
```

안전지도 URL 규칙:  
`safetyMapRelativePath` 가 `vision/output/safety_map/foo.jpg` 이면  
→ `http://localhost:3780/vision-output/safety_map/foo.jpg`

---

## 한 줄 초간단 프롬프트 (급할 때)

```text
현재 프로젝트를 http://localhost:3780 의 haeundae-crowd-density 서비스와 HTTP 연동해줘.
POST /api/analyze/manual 로 구역 GWANGALLI-ZONE-CENTER 분석 결과를 관광객/관리자 UI에 표시하고,
가능하면 POST /api/analyze/vision 의 alerts(touristMessage/managerMessage)와
/vision-output/... 안전지도 이미지도 연결해.
실시간 AI가 필요하면 GET /api/vision/realtime 의 streamUrl·
GET /api/vision/realtime/status 를 쓰고, 밀도 엔진은 재구현하지 말고 API만 소비해.
```
