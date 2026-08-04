# Cursor 팀용 프롬프트 (복붙)

## 방법 A (권장) — 이 저장소를 Cursor로 연 경우

`.cursor/rules/waveguard.mdc` 가 자동 적용됩니다. 추가 복붙 없이 질문하면 됩니다.

실시간 데모: http://localhost:3780/realtime.html  
(`npm run dev` + `npm run vision:realtime`)

## 방법 B — **다른 앱** Cursor에 Safe Flow 실시간 붙이기

[`SAFE-FLOW-OTHER-CURSOR-PROMPT.md`](./SAFE-FLOW-OTHER-CURSOR-PROMPT.md) 의 코드 블록 전체를 붙여넣으세요.

## 방법 C — 이 저장소에서 규칙이 안 먹을 때

새 Agent 채팅 첫 메시지:

```text
당신은 Safe Flow(haeundae-crowd-density) 시니어 풀스택 엔지니어다.
할루시네이션 없이 실제 코드·API를 확인한 뒤 답하고 수정하라.

실행: npm install → copy .env.example .env → npm run dev (:3780)
실시간 AI: npm run vision:realtime (:8790)
데모: /realtime.html · /tourist.html · /admin.html
구역: GWANGALLI-ZONE-CENTER
클라이언트: src/client/densityApiClient.ts , public/density-api-client.js
예제: examples/safe-flow-client/safe-flow-realtime.js

금지: 밀도 공식 재구현, API 경로/필드명 변경, .env 실키 커밋,
관광객 UI에 밀도 API 패널 추가.

실시간: GET /api/vision/realtime , /status , /model + streamUrl MJPEG
문서: docs/SAFE-FLOW-REALTIME.md , docs/README.md

이제 내 요청을 이 기준에 맞게 처리해라:
(여기에 할 일)
```

## 상대에게 보낼 한 줄

> Safe Flow: https://github.com/escescesc1234-cmyk/haeundae-crowd-density  
> 다른 앱이면 `docs/SAFE-FLOW-OTHER-CURSOR-PROMPT.md` 전체를 Cursor에 붙여넣어.  
> 이 저장소면 그냥 열고 `npm run dev` + `npm run vision:realtime` 후 `/realtime.html` 보면 돼.
