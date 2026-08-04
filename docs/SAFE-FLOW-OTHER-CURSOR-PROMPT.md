# Safe Flow — 다른 Cursor용 프롬프트 (복붙)

아래 **코드 블록 전체**를 다른 프로젝트의 Cursor 채팅에 붙여넣으세요.

---

```text
당신은 시니어 풀스택 연동 엔지니어다. 할루시네이션 없이 Safe Flow 실시간 AI·밀도 API를
현재 워크스페이스 앱에 연결하라. 밀도/위험등급 공식을 재구현하지 말고 HTTP만 소비한다.
API 경로·필드명 변경 금지. .env 실키 커밋 금지.

# Safe Flow
- GitHub: https://github.com/escescesc1234-cmyk/haeundae-crowd-density
- 제품명: Safe Flow
- baseUrl: http://localhost:3780
- 헬스: GET /api/health → { ok:true, service:"haeundae-crowd-density" }
- 구역: GWANGALLI-ZONE-CENTER
- 데모: http://localhost:3780/realtime.html
- 복사 클라이언트: examples/safe-flow-client/safe-flow-realtime.js
- 문서: docs/SAFE-FLOW-REALTIME.md

# 0) 최우선 — git pull (절대 건너뛰지 말 것)
# 이미 clone 있으면:
cd <haeundae-crowd-density 경로>
git fetch origin && git checkout main && git pull origin main
# 없으면:
git clone https://github.com/escescesc1234-cmyk/haeundae-crowd-density.git
cd haeundae-crowd-density && git pull origin main
# pull 전에는 연동 코드 작성 금지 (/api/vision/realtime/monitor 등 최신 API 필요)

# 1) 이 PC에서 서버 기동 (안 떠 있으면 실행·재시도)
npm install
copy .env.example .env
npm run dev
# 실시간 AI (필수 for 스트림)
npm run vision:realtime
# 권장: vision/models/yolo26s_beach_ft.pt (Drive로 별도 전달)

검증: curl http://localhost:3780/api/health
     curl http://localhost:3780/api/vision/realtime/monitor

# 2) 실시간 구현 (필수)
GET /api/vision/realtime
→ streamUrl, streamYoloUrl, alerts, proxiedStatusPath
GET /api/vision/realtime/status  (2초 폴링 권장)
→ personCount, tubeCount, maxGridDensityPerM2, estimatedTotal{count,detection,density,source}, alerts
GET /api/vision/realtime/model
→ path, classes {0:person,1:tube}

UI:
- <img src={streamUrl}> 로 MJPEG 안전지도 표시
- estimatedTotal.count / personCount / maxGridDensityPerM2 숫자 표시
- alerts.hasDanger 시 managerMessage 또는 touristMessage 표시
고정 문구:
- 관광객: "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요."
- 관리자: "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요."

권장: examples/safe-flow-client/safe-flow-realtime.js 를 현재 프로젝트에 복사해
SafeFlowRealtime.create({ baseUrl }).getMeta() / pollStatus() 사용.
또는 저장소 public/density-api-client.js 의 startRealtimePolling 사용.

# 3) 밀도 API (함께 연동)
POST /api/analyze/manual
{ zoneId:"GWANGALLI-ZONE-CENTER", detectedPeople:800, measuredAt:ISO, notify:false }
GET /api/tourist/zones · GET /api/admin/zones
환경변수: DENSITY_API_BASE_URL=http://localhost:3780
실패 UI: "밀도 분석 서비스 연결 실패"
타임아웃: manual 10s, realtime status 8s

# 4) UI 규칙
- 관광객+관리자 둘 다 실시간 스트림·인원 표시 가능
- 관광객에 「밀도 API · 구역 현황」 패널 넣지 말 것
- localhost는 그 PC만. 각자 npm run dev + vision:realtime

# 완료
health·realtime status·스트림·폴링 UI까지 검증 후 수정 파일과 사용법을 짧게 보고.
```
