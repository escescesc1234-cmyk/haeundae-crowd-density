# 예측·기상·시뮬레이션 기능 설계

## 기존 구조 분석

- 기술 스택: TypeScript, Express, 정적 HTML/CSS/브라우저 JS, Vitest
- DB: 없음. 현재 프로젝트는 JSON 설정과 인메모리 런타임 사용
- UI: `public/tourist.html`, `public/admin.html`, `public/styles.css`
- 밀도/등급: `src/density/*`, `src/service/crowdDensityService.ts`
- 알림: `src/notification/*`
- 차트: 외부 라이브러리 없음. 기존 `.chart` CSS 기반 미니 그래프만 존재
- 외부 기상 API: 기존 연결 없음

## 전체 설계

새 기능은 `src/forecast/*`에 모듈화했다.

- `weatherProvider.ts`: 기상청 단기예보 조회서비스(`getVilageFcst`) 어댑터 + 모의 공급자
- `forecastModel.ts`: 설명 가능한 규칙/가중치 기반 방문객 예측
- `service.ts`: 예측, 과거 비교, 사전 알림, 시뮬레이션, 백테스트 오케스트레이션
- `dataStore.ts`: JSON 모의 데이터 로더
- `types.ts`: `CrowdObservation`, `CrowdForecast`, `WeatherObservation`, `WeatherForecast`, `HistoricalWeather`, `BeachEvent`, `BeachZone`, `RiskAssessment`, `SimulationScenario`, `BacktestResult`

## 실시간 밀집도 데이터 흐름

`/api/admin/zones`와 `/api/tourist/zones`는 기존 실측 밀도 결과를 유지한다.  
`/api/forecast/overview`는 이 실측값을 받아 `oneHourForecast`로 1시간 후 예상 혼잡도를 반환한다.

## 기상청 및 과거 날씨 구조

기상청 API는 서버에서만 호출한다. 클라이언트에는 키를 노출하지 않는다.

- 환경 변수: `KMA_SERVICE_KEY`
- 선택 환경 변수: `KMA_API_BASE_URL`, `KMA_BASE_TIME`, `KMA_NX`, `KMA_NY`
- 기본 엔드포인트: `https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst`
- 주요 파라미터: `serviceKey`, `pageNo`, `numOfRows`, `dataType`, `base_date`, `base_time`, `nx`, `ny`

키가 없거나 호출이 실패하면 `MockWeatherProvider` 또는 마지막 정상 데이터 구조를 사용하고 UI에 상태를 표시한다.

## 통신사 장소 혼잡도 (보조 소스)

SK open API 지오비전 퍼즐 「장소 혼잡도」를 **예측 가중치 보조**로만 사용한다. CCTV/수동 밀도 판정을 대체하지 않는다.

- 환경 변수: `SK_OPEN_API_APP_KEY`
- 엔드포인트: `GET https://apis.openapi.sk.com/puzzle/place/congestion/rltm/pois/{poiId}`
- 헤더: `appKey`, `Accept: application/json`
- 구역 매핑: `config/telecom.gwangalli.json`
- **기본 비활성**: 페이지 새로고침·예측 기본 경로에서는 SK를 호출하지 않음 (`apiStatus: idle`)
- 테스트 연결: `POST /api/population/telecom/test` 또는 `GET /api/population/telecom?live=true`
- 예측에만 포함: `GET /api/forecast/overview?useTelecom=true`
- 403이면 상품 미구독/권한 문제로 보고 모의 데이터로 대체 (`apiStatus: not_subscribed`)

## 방문객 예측 알고리즘

초기 모델은 머신러닝이 아니라 설명 가능한 가중치 기반이다.

`기본 방문객 수 × 시간대 × 요일 × 성수기 × 휴가철 × 날씨 × 이벤트 × 과거 방문객 × 유사 날씨 × 현재 추세`

각 요인은 `ForecastFactor`로 반환되어 관리자 화면에서 예측 근거로 표시할 수 있다. 데이터가 부족하면 신뢰도를 낮추고 경고를 표시한다.

## 관광객 사전 알림

혼잡 가능성 또는 예상 등급이 기준을 넘으면 `ProactiveNotificationPreview`를 만든다.

- live: `sendTestNotification=true`일 때 모의 채널에 기록
- simulation: 실제 발송 금지, 메시지에 `[시뮬레이션 알림 — 실제 발송 아님]` 포함

## 날짜 선택·시뮬레이션

관리자 UI에서 모드, 날짜, 시간, 비교연도, 날씨/이벤트 사용 여부를 선택한다.  
`SimulationScenario`에는 `mode`, 기준 날짜, 데이터 범위, 실행자, 실제 발송 여부를 저장한다.

## 전날 백테스트

`runBacktest()`는 `dataAvailableUntil = targetDate 09:00`을 기준으로 그 이전 데이터만 입력에 사용한다.  
전날 실제 관측값과 비교해 절대오차, 오차율, 예측/실제 등급, 알림 적절성을 반환한다.

## API

- `GET /api/forecast/overview`
- `GET /api/weather/current`
- `GET /api/weather/compare`
- `POST /api/forecast/simulation`
- `POST /api/forecast/backtest`
- `POST /api/forecast/pre-notifications`

## 제한 사항

- 방문객/날씨/이벤트 데이터는 테스트용 모의 데이터
- 실제 기상청 특보/해양 데이터는 인터페이스만 일부 포함
- DB, 실시간 WebSocket, 실제 FCM/APNs/SMS 없음
- 예측 정확도 수치는 실제 성능이 아니며 테스트 통과만 검증
- 실제 운영 전 현장 데이터 축적과 안전 전문가 검토 필요
