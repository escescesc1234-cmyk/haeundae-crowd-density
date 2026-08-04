import { loadEnvFile } from "../config/loadEnv.js";
import { createApp } from "./server.js";

loadEnvFile();

const port = Number(process.env.PORT ?? 3780);
/** Docker/VPS에서 외부 접속 가능하도록 기본 0.0.0.0 */
const host = process.env.HOST ?? "0.0.0.0";
const app = createApp();

app.listen(port, host, () => {
  console.log(`Safe Flow 밀도 서버: http://${host}:${port}`);
  console.log(`관광객 화면: http://localhost:${port}/tourist.html`);
  console.log(`관리자 화면: http://localhost:${port}/admin.html`);
  console.log(
    `기상청 API: ${process.env.KMA_SERVICE_KEY ? "키 로드됨" : "키 없음(모의 데이터)"}`,
  );
  console.log(
    `SK 장소 혼잡도: ${process.env.SK_OPEN_API_APP_KEY ? "WaveGuard 대시보드에서 10분마다 갱신" : "앱키 없음(모의 데이터)"}`,
  );
});
