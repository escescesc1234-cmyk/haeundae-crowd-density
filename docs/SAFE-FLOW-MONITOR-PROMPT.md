# Safe Flow — 다른 Cursor용 초상세 프롬프트 (실시간 모니터)

아래 **코드 블록 전체**를 다른 프로젝트 Cursor에 붙여넣으세요.
(부분 탐지 OK · SAHI 실시간 · 9:16 잘림 없음)

---

```text
당신은 시니어 풀스택 연동 엔지니어다. 할루시네이션 없이 아래를 한 단계도 건너뛰지 말고 직접 수행하라.
목표: 이미 구축된 현재 앱에서 Safe Flow 실시간 AI 분석을 "모니터링"한다.
- 화면 일부만 인식되어도 정상(partialOk). 전수 인원을 요구하지 마라.
- 밀도 엔진 재구현 금지. API 경로/필드명 변경 금지. .env 실키 커밋 금지.
- 관광객 UI에 「밀도 API · 구역 현황」 패널 추가 금지.

════════════════════════════════════════════════════════
0. 최우선 — Safe Flow 저장소 git pull (절대 건너뛰지 말 것)
════════════════════════════════════════════════════════
연동·기동·문서 읽기 전에, 이 PC의 haeundae-crowd-density 를 최신으로 맞춰라.
오래된 clone이면 /api/vision/realtime/monitor 가 없어 실시간 모니터가 실패한다.

직접 터미널에서 실행:

# 이미 clone 되어 있으면 (경로를 찾아라: 홈, Projects, Desktop 등)
cd <haeundae-crowd-density 경로>
git fetch origin
git checkout main
git pull origin main
git log -1 --oneline
# → 최신에 feat: 실시간 monitor / SAFE-FLOW-MONITOR 관련 커밋이 보여야 함

# clone 자체가 없으면
git clone https://github.com/escescesc1234-cmyk/haeundae-crowd-density.git
cd haeundae-crowd-density
git pull origin main

pull 실패(권한/네트워크)면 사용자에게 알리고 중단하지 말고 재시도·안내.
pull 완료 전에는 앱 연동 코드를 작성하지 마라.

════════════════════════════════════════════════════════
A. 반드시 이해할 것 (실패 원인 대부분이 여기)
════════════════════════════════════════════════════════
1) localhost는 "그 Cursor가 돌아가는 PC" 안에서만 통한다.
   다른 사람 노트북의 localhost에 원격으로 붙을 수 없다.
2) 실시간 AI는 프로세스 2개가 필요하다.
   - 밀도 API :3780  → npm run dev
   - 비전 SAHI :8790 → npm run vision:realtime
   3780만 켜고 8790을 안 켜면 monitor가 502/연결실패가 난다.
3) "분석"은 POST /api/analyze/vision(일회성)과 다르다.
   앱 모니터링은 GET /api/vision/realtime/monitor 를 2초마다 폴링한다.
4) 스트림 영상과 숫자(인원)는 별개다.
   - 영상: monitor.streamUrl → <img src> (MJPEG)
   - 숫자: monitor.monitoring.* 를 텍스트로 표시
5) SAHI는 첫 프레임까지 수십 초 걸릴 수 있다(phase=warming).
   그동안 FAST 숫자/추정 인원을 보여주고, "워밍업 중"을 표시하라. 실패로 처리하지 마라.
6) 부분 탐지 OK: sahiPersonCount가 작거나 0이어도 UI를 끄지 마라.
   estimatedTotal / fastPersonCount / sahiPersonCount 를 모두 보여라.

════════════════════════════════════════════════════════
B. 이 PC에서 Safe Flow 서버 기동 (직접 실행·검증)
════════════════════════════════════════════════════════
# 0단계에서 이미 cd + git pull 완료된 상태여야 함
cd <haeundae-crowd-density 경로>
git pull origin main
npm install
copy .env.example .env
# DENSITY_API_BASE_URL=http://localhost:3780

# 2) 터미널 A — 밀도 API
npm run dev
# http://localhost:3780

# 3) 터미널 B — SAHI 실시간 (필수)
# Python 의존성 최초 1회: cd vision && pip install -r requirements.txt && cd ..
npm run vision:realtime
# VISION_SAHI256=1 기본, --detector both
# SAHI MJPEG: http://127.0.0.1:8790/stream/sahi256

# 4) 가중치(권장): vision/models/yolo26s_beach_ft.pt
#    없으면 기본 가중치로라도 동작. 정확도만 낮을 수 있음.

# 5) 검증 — 아래가 성공할 때까지 재시도. 실패 시 사용자에게 기동 안내.
curl http://localhost:3780/api/health
# → ok:true, service:haeundae-crowd-density

curl http://localhost:3780/api/vision/realtime/monitor
# → ok:true, streamUrl 에 /stream/sahi256 포함
# → monitoring.sahiPersonCount / fastPersonCount / estimatedTotal 존재
# → partialOk:true

# 브라우저 참고 데모(정답 UI):
# http://localhost:3780/realtime.html

monitor가 실패하면:
- 3780 down → npm run dev
- 8790 down → npm run vision:realtime
- Windows 방화벽/백신 확인
- 포트 점유: netstat 로 3780/8790

════════════════════════════════════════════════════════
C. 현재 앱에 붙일 API (이것만 쓰면 됨)
════════════════════════════════════════════════════════
BASE = process.env.DENSITY_API_BASE_URL ?? "http://localhost:3780"

## 핵심(필수) — 실시간 모니터
GET `${BASE}/api/vision/realtime/monitor`
성공 예:
{
  "ok": true,
  "live": true,
  "phase": "live" | "warming" | "error" | "sahi_disabled",
  "partialOk": true,
  "disclaimer": "부분 탐지...참고용...",
  "streamUrl": "http://127.0.0.1:8790/stream/sahi256",
  "pollIntervalMs": 2000,
  "display": { "aspectRatio": "9:16", "objectFit": "contain" },
  "monitoring": {
    "estimatedTotal": 120,
    "fastPersonCount": 18,
    "sahiPersonCount": 22,
    "tubeCount": 3,
    "maxGridDensityPerM2": 2.1,
    "sahiState": "ok",
    "sahiEnabled": true,
    "source": "density"
  },
  "alerts": {
    "hasDanger": false,
    "dangerCellCount": 0,
    "touristMessage": null,
    "managerMessage": null
  }
}

실패(비전 미기동) 예:
{ "ok": false, "live": false, "error": "...", "howToStart": ["npm run dev","npm run vision:realtime"] }

## 보조
GET `${BASE}/api/health`
GET `${BASE}/api/vision/realtime`          // 계약/URL
POST `${BASE}/api/analyze/manual`         // 구역 등급(선택)
{ "zoneId":"GWANGALLI-ZONE-CENTER", "detectedPeople": <monitoring.estimatedTotal 또는 sahiPersonCount>, "measuredAt":"<ISO>", "notify":false }

고정 위험 문구(변경 금지):
- 관광객: "주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요."
- 관리자: "경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요."
alerts.hasDanger 이면 touristMessage/managerMessage 또는 위 고정 문구 표시.

════════════════════════════════════════════════════════
D. UI 구현 규칙 (필수·세밀)
════════════════════════════════════════════════════════
1) 폴링
   - 마운트 시 즉시 GET monitor 1회
   - 이후 2000ms마다 폴링 (pollIntervalMs 존중)
   - 언마운트 시 clearInterval/stop
   - 요청이 겹치면 이전 응답이 늦게 와도 최신만 반영(시퀀스 번호 권장)

2) 영상 (SAHI)
   - src = data.streamUrl  (반드시 .../stream/sahi256)
   - 안전지도(/stream)나 yolo 스트림으로 대체하지 말 것
   - 9:16 컨테이너 + object-fit: contain 만 사용 (cover 금지 = 잘림 금지)
   - 레터박스(#111) 허용
   CSS 예:
   .sf-video-9x16 { width:min(100%,420px); margin:0 auto; aspect-ratio:9/16; overflow:hidden; border-radius:12px; background:#111; position:relative; }
   .sf-video-9x16__media { position:absolute; inset:0; width:100%; height:100%; object-fit:contain; background:#111; }

3) 숫자 카드 (항상 표시, 0이어도 표시)
   - 추정 인원 = monitoring.estimatedTotal
   - SAHI 인원 = monitoring.sahiPersonCount   ← "실시간 AI 분석" 핵심
   - FAST 인원 = monitoring.fastPersonCount   ← 보조
   - (선택) 밀도 = monitoring.maxGridDensityPerM2
   - 부분 탐지여도 숨기지 말 것. 라벨에 "일부 인식·참고" 가능

4) phase UX
   - warming: 노란 배너 "SAHI 준비 중… 잠시만 기다려 주세요" + 숫자는 계속 갱신
   - live: 초록 "실시간 분석 중"
   - error: 빨강 + FAST/추정 숫자는 유지
   - ok:false: "밀도 분석 서비스 연결 실패" + howToStart 명령 안내

5) 스트림이 안 보이면
   - img onerror 시 streamUrl에 ?t=Date.now() 로 1~2회 재시도
   - 그래도 안 되면 8790 기동 안내 (숫자 폴링은 계속)

6) (선택) 구역 등급
   - estimatedTotal 또는 sahiPersonCount 로 analyzeManual 호출해 riskLevel 표시
   - 실패해도 모니터 UI는 유지

7) 스택별
   - Web: <img> MJPEG
   - React Native: 가능하면 WebView/img 로 MJPEG, 불가하면 숫자 모니터만이라도 완성
   - Flutter: 동일 — 스트림 불가 시 숫자 모니터 우선 완성

════════════════════════════════════════════════════════
E. 최소 동작 코드 (웹, 그대로 응용)
════════════════════════════════════════════════════════
const BASE = process.env.DENSITY_API_BASE_URL ?? "http://localhost:3780";

async function tick() {
  const r = await fetch(`${BASE}/api/vision/realtime/monitor`);
  const data = await r.json();
  if (!data.ok) {
    showError(data.error || "밀도 분석 서비스 연결 실패");
    return;
  }
  img.src = data.streamUrl; // /stream/sahi256
  setText("est", data.monitoring.estimatedTotal);
  setText("sahi", data.monitoring.sahiPersonCount);
  setText("fast", data.monitoring.fastPersonCount);
  setPhase(data.phase); // warming|live|error
  if (data.alerts?.hasDanger) showAlert(data.alerts.managerMessage || data.alerts.touristMessage);
}
tick();
setInterval(tick, 2000);

════════════════════════════════════════════════════════
F. 완료 기준 (직접 검증 후 한국어로 보고)
════════════════════════════════════════════════════════
[ ] curl health 성공
[ ] curl /api/vision/realtime/monitor 성공, streamUrl에 sahi256
[ ] 앱에서 2초마다 sahiPersonCount/estimatedTotal 갱신
[ ] 9:16 contain(잘림 없음)으로 SAHI 영상 표시(또는 플랫폼 제약 시 숫자만이라도)
[ ] phase=warming 을 실패로 처리하지 않음
[ ] 부분 탐지(작은 숫자)에도 UI 유지
[ ] 서비스 다운 시 "밀도 분석 서비스 연결 실패" + 기동 안내
[ ] 수정 파일 목록과 사용법 보고

지금 바로 0단계(git pull origin main)부터 수행하고, 이어서 B→D→E 순으로 구현하라.
막히면 추측하지 말고 curl 응답 JSON을 읽고 원인을 고친 뒤 계속하라.
pull을 안 한 채 옛 API만 보고 "엔드포인트 없음"이라고 단정하지 마라.
```
