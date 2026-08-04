# 다른 Cursor용 — 기존 앱에 SAHI 실시간 · 9:16(잘림 없음)

아래 코드 블록 전체를 붙여넣으세요.

---

```text
당신은 시니어 프론트엔드 엔지니어다. 이미 구축된 현재 앱을 읽고,
Safe Flow SAHI 실시간 영상을 9:16·잘림 없이 표시하도록 수정하라.
밀도 엔진·API 경로·필드명 변경 금지. UI 비율·스트림 소스만 수정.

# Safe Flow
baseUrl = process.env.DENSITY_API_BASE_URL ?? "http://localhost:3780"
서버(이 PC): haeundae-crowd-density 에서
  npm run dev
  npm run vision:realtime   # VISION_SAHI256=1 기본, SAHI-256 실시간

GET /api/vision/realtime
→ streamUrl              = SAHI-256 MJPEG (기본, 이걸 사용)
→ streamSahi256Url       = 동일
→ streamSafetyMapUrl     = 격자 안전지도(보조)
→ display: { aspectRatio:"9:16", objectFit:"contain" }

GET /api/vision/realtime/status (2초 폴링)
→ personCount, estimatedTotal, sahi256.personCount, alerts

# 표시 규칙 (필수)
1. 스트림 src = meta.streamUrl (또는 streamSahi256Url). 안전지도(/stream)로 바꾸지 말 것.
2. 컨테이너 비율 9:16. 영상 잘림 금지.
3. object-fit: contain 만 사용. cover 금지.
4. 레터박스(검은 여백)는 허용.
5. 권장 CSS:
   .sf-video-9x16 {
     position: relative;
     width: min(100%, 420px);
     margin: 0 auto;
     aspect-ratio: 9 / 16;
     overflow: hidden;
     border-radius: 12px;
     background: #111;
   }
   .sf-video-9x16__media {
     position: absolute; inset: 0;
     width: 100%; height: 100%;
     object-fit: contain; /* 잘림 금지 */
     display: block; background: #111;
   }
6. React Native: aspectRatio 9/16 + resizeMode 'contain'
7. Flutter: AspectRatio(9/16) + BoxFit.contain
8. 관광객에 밀도 API 패널 추가 금지

# 검증
- 영상이 잘리지 않고 9:16 박스 안에 contain으로 들어가는지
- stream URL이 .../stream/sahi256 인지
- status.sahi256 이 갱신되는지
수정 파일만 짧게 보고.
```
