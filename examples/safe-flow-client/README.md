# Safe Flow 클라이언트 예제 (다른 앱용)

이 폴더의 `safe-flow-realtime.js` 를 **다른 프로젝트에 복사**해 Safe Flow 실시간 AI를 붙이세요.

## 사전 조건

다른 PC에서도 동일:

```bash
git clone https://github.com/escescesc1234-cmyk/haeundae-crowd-density.git
cd haeundae-crowd-density
npm install
copy .env.example .env
npm run dev
# 별도 터미널 — AI 실시간
npm run vision:realtime
```

가중치(선택·권장): Google Drive의 `yolo26s_beach_ft.pt` → `vision/models/`

## 최소 코드

```html
<img id="map" alt="stream" />
<script src="./safe-flow-realtime.js"></script>
<script>
  const sf = SafeFlowRealtime.create({
    baseUrl: "http://localhost:3780",
  });
  sf.getMeta().then((m) => {
    document.getElementById("map").src = m.streamUrl;
  });
  sf.pollStatus((st) => {
    console.log(st.estimatedTotal, st.personCount, st.maxGridDensityPerM2);
  });
</script>
```

## 이 저장소 데모

서버 기동 후: http://localhost:3780/realtime.html

## Cursor 프롬프트

다른 앱 Cursor 채팅에 `docs/SAFE-FLOW-OTHER-CURSOR-PROMPT.md` 전체를 붙여넣으세요.
