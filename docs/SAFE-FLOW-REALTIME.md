# Safe Flow 실시간 AI 계약

제품명 **Safe Flow**. 엔진 재구현 금지 — HTTP만 소비.

## 기동

| 프로세스 | 명령 | 포트 |
|----------|------|------|
| 밀도 API | `npm run dev` | 3780 |
| 실시간 AI | `npm run vision:realtime` | 8790 |

가중치: `vision/models/yolo26s_beach_ft.pt` (없으면 기본 가중치로 동작할 수 있음)

## 엔드포인트 (baseUrl = `http://localhost:3780`)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/vision/realtime` | streamUrl·계약 JSON |
| GET | `/api/vision/realtime/status` | 탐지 수치 (2초 폴링) |
| GET | `/api/vision/realtime/model` | 로드된 YOLO 메타 |
| — | `meta.streamUrl` (보통 `:8790/stream`) | MJPEG `<img src>` |

## status 핵심 필드

```json
{
  "personCount": 12,
  "tubeCount": 3,
  "maxGridDensityPerM2": 2.1,
  "estimatedTotal": { "count": 120, "detection": 15, "density": 120, "source": "density" },
  "pipeline": "fast",
  "updatedAt": "...",
  "alerts": { "hasDanger": false, "touristMessage": null, "managerMessage": null }
}
```

## 클라이언트

| 위치 | 용도 |
|------|------|
| `src/client/densityApiClient.ts` | Node/TS (`startRealtimePolling`) |
| `public/density-api-client.js` | 이 서버 UI |
| `examples/safe-flow-client/safe-flow-realtime.js` | **다른 앱 복사용** |
| `public/realtime.html` | 동작 데모 |

## 다른 Cursor

프롬프트: [`SAFE-FLOW-OTHER-CURSOR-PROMPT.md`](./SAFE-FLOW-OTHER-CURSOR-PROMPT.md)

## 고정 위험 문구

- 관광객: `주의하세요! 혼잡 지역이 있습니다. 안전 거리를 유지해 주세요.`
- 관리자: `경고: 위험 구역이 발생했습니다. 즉시 현장 점검 및 안전 조치를 시행하세요.`
