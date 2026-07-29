# 프로그램 설계 문서 — 광안리 군중 밀집도 분석

## 1. 프로그램 설계 개요

본 시스템은 광안리 해수욕장을 **구역 단위**로 나누어 유효 면적 대비 인원수로 인구 밀도를 계산하고, 설정 가능한 임계값에 따라 **안전·혼잡·위험** 3단계 등급을 판정한다.

- **코어 엔진** (`src/density/*`): UI에 종속되지 않는 순수 분석 로직
- **서비스 계층** (`src/service/crowdDensityService.ts`): AI LAB 앱·API가 공통 호출
- **어댑터** (`src/adapters/*`): 수동 입력 / CCTV 감지 입력 변환
- **뷰 모델** (`src/views/*`): 관광객·관리자 화면용 요약 분리
- **API + UI** (`src/api`, `public/`): 데모·운영 모니터링

기존 AI LAB 코드베이스는 워크스페이스에 없어 **신규 모듈형 프로젝트**로 구성했다. 이후 지도·CCTV 모듈은 `CrowdDensityService` 또는 `analyzeZoneDensity`를 import하여 연결하면 된다.

## 2. 위험 등급 및 임계 밀도 적용 방식

| 조건 | 등급 | 부가 처리 |
|------|------|-----------|
| 보정 밀도 &lt; congestionStartDensity | 안전 | — |
| congestionStartDensity ≤ 보정 밀도 &lt; highRiskDensity | 혼잡 | — |
| 보정 밀도 ≥ highRiskDensity | 위험 | — |
| criticalDensity ≤ 보정 밀도 &lt; highRiskDensity | 혼잡 유지 | 임계 밀도 도달 플래그 + 사전 경고·갱신 주기 단축·현장 확인 요청 등 |

초기 참고값: congestionStartDensity=4.0, criticalDensity=5.0, highRiskDensity=6.0  
이 값은 **절대 기준이 아니며** `config/thresholds.default.json` 및 관리자 API로 변경한다. 변경 시 `config/threshold-changes.json`에 감사 로그를 남긴다.  
구역별 `thresholdOverrides`로 출입구·백사장 등 다른 임계값을 적용할 수 있다.

등급 안정화: 이동평균+중앙값 보정, 상승/하락 비대칭 히스테리시스, 최소 지속 시간, 급상승·고위험 과다 초과 시 즉시 위험.

## 3. 데이터 구조

핵심 타입은 `src/types/index.ts`에 정의한다.

- `ZoneDefinition`: 구역 ID/이름/유형/전체·제외·유효 면적
- `DensityInput`: 분석 입력(인원, 면적, 시각, 신뢰도, 카메라 감지, 보조 요인)
- `DensityAnalysisResult`: 공통 출력 JSON (관광객·관리자 요약 포함)
- `DensityThresholds` / `ThresholdChangeRecord`: 임계값 및 변경 이력
- `AuxiliaryRiskFactors` / `AuxiliaryAlert`: 밀도 외 위험 요인(밀도 값 자체를 조작하지 않음)

## 4. 밀도 계산과 등급 판정 로직

1. 유효 면적·인원 검증 → 실패 시 `오류` 또는 `데이터없음`
2. 다중 카메라/경계 trackId 중복 제거 (`deduplication.ts`)
3. `rawDensity = people / effectiveArea`
4. 측정 윈도우 내 이동평균·중앙값으로 `adjustedDensity` 산출
5. 히스테리시스로 안정 등급 결정 (또는 `skipHysteresis`로 즉시 판정)
6. criticalDensity 도달 시 혼잡 유지 + 조치 트리거
7. 보조 요인 평가 → 별도 경고 (밀도 안전 + 추가 위험 동시 표시 가능)
8. 관광객/관리자 뷰 모델 생성

## 5. 생성·수정 파일 목록

| 경로 | 역할 |
|------|------|
| `package.json`, `tsconfig.json`, `vitest.config.ts` | 프로젝트 설정 |
| `config/thresholds.default.json` | 초기 임계값 |
| `config/zones.gwangalli.json` | 광안리 구역 정의 |
| `src/types/index.ts` | 공통 타입 |
| `src/config/thresholds.ts` | 임계값 로드/검증/저장/감사 |
| `src/density/calculator.ts` | 원본 밀도 계산 |
| `src/density/smoothing.ts` | 이동평균·중앙값·추세 |
| `src/density/hysteresis.ts` | 히스테리시스·즉시 경고 |
| `src/density/riskClassifier.ts` | 등급·임계 밀도·관광객 라벨 |
| `src/density/deduplication.ts` | 중복 감지 완화 |
| `src/density/engine.ts` | 통합 분석 엔진 |
| `src/factors/auxiliaryFactors.ts` | 보조 위험 요인 |
| `src/zone/zoneService.ts` | 구역 카탈로그·유효 면적 |
| `src/adapters/*` | 수동/CCTV 어댑터 |
| `src/views/*` | 화면용 뷰 |
| `src/service/crowdDensityService.ts` | 세션 서비스 |
| `src/api/server.ts`, `src/api/start.ts` | HTTP API |
| `src/cli.ts`, `src/index.ts` | CLI·모듈 export |
| `public/*` | 관광객·관리자 UI |
| `tests/*`, `data/sample-measurements.json` | 테스트 |
| `README.md`, `DESIGN.md` | 문서 |

## 6. 실행 방법

```bash
cd C:\Users\user\Projects\haeundae-crowd-density
npm install
npm test
npm run analyze
npm run build
npm run dev
```

- http://localhost:3780/tourist.html
- http://localhost:3780/admin.html

## 7. 오류·예외 처리

- 면적 ≤ 0 / 누락 → `오류`
- 인원 누락 → `데이터없음`
- 인원 음수·비정수 → `오류`
- 신뢰도 &lt; minimumConfidence → 낮은 신뢰도 경고 + 관리자 검토
- 오래된 측정(staleDataSeconds) → 경고
- 다중 카메라 중복 → trackId 유니온 또는 max 휴리스틱 + 경고
- 임계값 순서 위반 → 저장 거부
- 밀도 급상승 → 경고 및/또는 즉시 위험

## 8. CCTV 연결 시 추가 작업

1. `CctvIngestClient.fetchLatestDetections` 실제 구현
2. 익명 `trackedObjectIds` 제공 (신원 매핑 금지)
3. 구역 경계 겹침 시 `BoundaryDetection.assignedZoneId` 규칙 합의
4. 네트워크 단절 시 stale 경고·관리자 알림 채널 연동
5. GIS로 구역 폴리곤·유효 면적 재측량

## 9. 현재 구현의 제한 사항

- 구역 면적은 **예시 값**이며 실측 전 운영 불가
- CCTV는 어댑터·인터페이스만 제공 (실장비 미연동)
- 경보 SMS/푸시는 기록만 수행
- 지도 오버레이·실시간 WebSocket은 미포함
- 밀도 이력은 프로세스 메모리(재시작 시 초기화)
- 한국어 등급 문자열을 API에 사용 (국제화 시 enum 코드 분리 권장)
