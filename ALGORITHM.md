# 부산 해수욕장 AI 안전 관리 — 전체 알고리즘 설계서

> **문서 성격**: 구현 가능한 알고리즘 명세. 존재하지 않는 API·모델 성능·테스트 수치를 가정하지 않는다.  
> **현재 코드베이스 대비**: `haeundae-crowd-density`에는 밀도·등급·알림·예측·WaveGuard UI·기상/SK 보조가 구현되어 있다. 본 문서는 **CCTV 탐지·추적·좌표변환·멀티카메라 융합**까지 포함한 **전체 시스템** 설계이며, 이미 구현된 모듈과의 대응을 §15에 명시한다.  
> **역할 경계**: AI는 사고 확정·단독 구조 투입 결정을 하지 않는다. 위험 *가능성* 분석과 관광객·관리자 판단을 지원한다.  
> **초기 흐름도(중학생 수준)**: [docs/INITIAL-ALGORITHM-FLOW.md](docs/INITIAL-ALGORITHM-FLOW.md)

---

## 1. 시스템 전체 작동 원리 요약

시스템은 **센서/영상 → 익명 객체 스트림 → 구역 인원·밀집도 → 다요인 위험 점수 → 지속성 기반 경보 → 사용자별 메시지 → 지도 시각화** 파이프라인으로 동작한다.

1. **수집 계층**이 CCTV 프레임·기상·해양·행사·관리자 확인을 기준 시각(KST wall-clock 또는 simulation clock)에 정렬한다.
2. **비전 계층**이 사람만 탐지·추적하여 익명 `trackId`와 영상 좌표를 생성한다. 얼굴·신원은 저장하지 않는다.
3. **공간 계층**이 호모그래피(또는 구역 마스크)로 지도 좌표·구역 ID를 부여하고, 멀티카메라 중복을 제거한다.
4. **밀도 계층**이 `인원 ÷ 유효면적(㎡)`을 계산하고, 이동평균·중앙값·히스테리시스로 순간 오탐을 완화한다.
5. **위험 계층**이 밀집도 + 흐름 충돌·정체·급증·안전선 접근·기상/해양 악화를 **위험 점수**로 합성하되, 익사·압사를 *확정하지 않는다*.
6. **예측 계층**이 과거·날씨·이벤트·실시간 추세로 시간대별 방문객·혼잡도를 예측하고, 데이터 부족 시 신뢰도를 낮춘다.
7. **경보 계층**이 지속 시간·다센서 일치·신뢰도 조건을 만족할 때만 관찰→주의→위험→긴급검토로 승격한다.
8. **알림·UI 계층**이 관광객(행동 안내)과 관리자(근거·확인·승인) 메시지를 분리한다. 관리자 확인 전 “사고 발생” 문구를 쓰지 않는다.
9. **시뮬레이션·백테스트**는 동일 판정·경보 로직을 쓰되 `dataSource=simulation|backtest`로 실측과 구분한다.

---

## 2. 전체 데이터 흐름

```
[CCTV/테스트영상] ──► Detect ──► Track ──► GeoMap+Zone ──┐
[기상·해양 API]  ──► NormalizeTime ─────────────────────┤
[면적·구역 GIS]  ──► ZoneCatalog ───────────────────────┤
[행사·공휴일]    ──► EventCalendar ─────────────────────┤
[관리자 확인]    ──► Ack/Override ──────────────────────┤
[SK 혼잡도 등]   ──► AuxCongestion (가중치만) ──────────┤
                                                      ▼
                                              CrowdSnapshot(t)
                                                      │
                    ┌─────────────────────────────────┼─────────────────────────┐
                    ▼                                 ▼                         ▼
             DensityEngine                     ForecastEngine              AlertEngine
                    │                                 │                         │
                    ▼                                 ▼                         ▼
           ZoneRiskState(t)                   ForecastHorizon             AlertEvent*
                    │                                 │                         │
                    └────────────┬────────────────────┴──────────┬──────────────┘
                                 ▼                               ▼
                        TouristViewModel                  AdminViewModel
                                 │                               │
                                 ▼                               ▼
                        안전지도·사전알림                   모니터·경보·확인
```

\* AlertEvent는 관리자 확인(승인/보류/오판단) 전 `hypothesis` 상태이며, `confirmed_incident`로 자동 전이하지 않는다.

**기준 시각**: 모든 레코드에 `observedAt`(원천), `ingestedAt`(수신), `alignedAt`(파이프라인 기준)을 둔다. 지연이 임계(예: 영상>5s, 기상>30min)를 넘으면 `stale=true`.

---

## 3. 단계별 알고리즘

### 3.1 1단계 — 데이터 수집

**주기(권장 초기값, 현장 조정)**  
| 소스 | 주기 | 비고 |
|------|------|------|
| CCTV 프레임 | 5–10 FPS 분석(원본 25/30fps에서 다운샘플) | 야간·악천후 시 FPS↓ 가능 |
| 구역 인원 스냅샷 | 1–5 s | 추적 결과 집계 |
| 기상(KMA 등) | 10–60 min | 캐시 |
| 해양(파고·수온 등) | 제공 API 주기 | 없으면 `missing` |
| SK 장소 혼잡도 | ≥10 min | 한도·캐시, 밀도 대체 금지 |
| 관리자 확인 | 이벤트 | 경보 상태머신 입력 |

**처리**
1. 소스별 `SourceHealth{ lastOk, latencyMs, errorCode, consecutiveFails }` 갱신.
2. 시간대 통일: 모두 `Asia/Seoul` ISO-8601으로 변환.
3. 중복 키 `(sourceId, observedAt, payloadHash)` 드롭.
4. 누락: 직전 정상값 *전파 금지*가 기본. 예외적으로 기상만 TTL 내 캐시 허용하고 `apiStatus=cached` 표기.

### 3.2 2단계 — 객체 탐지

**역할**: 프레임에서 사람(person) 클래스만.  
**권장 기술**: 실시간 1-stage detector (예: YOLO 계열) — *역할: 사람 bbox*.  
**대체**: 공개 person detector + ROI 마스크; 또는 수동/테스트 인원 입력(비전 미연결 시).

```
FOR each frame F at time t:
  raw = Detector.infer(F)  // class=person only
  FOR d in raw:
    IF d.confidence < CONF_MIN: mark HOLD or DROP
    IF IoU(d, static_mask_structure) > thr: DROP  // 난간·의자
    IF aspect/area outlier vs zone-calibrated prior: DROP or HOLD
    emit Detection{ bbox, cx, cy, conf, cameraId, t }
```

`CONF_MIN` 초기 제안: 0.4–0.5 (현장 검증 후 조정). HOLD는 다음 프레임 추적 게이트에만 사용하고 인원 집계에는 넣지 않는다.

### 3.3 3단계 — 객체 추적

**권장**: ByteTrack / DeepSORT 계열 — *역할: ID 유지*.  
**대체**: IoU+칼만 필터 단순 트래커(저밀도).

```
tracks = Tracker.update(detections)
FOR tr in tracks:
  tr.velocity = Δposition / Δt
  tr.heading = atan2(vy, vx)
  tr.dwell = time_since_first_seen
  // re-ID: 짧은 occlusion 창(예: ≤2s)에서 IoU/외형 유사도로 재연결
  // 얼굴·생체 특징 저장 금지. 익명 trackId만 사용(세션 만료 시 파기)
```

중복 인원 방지: 동일 카메라 내 `trackId` 유일; 종료된 ID는 쿨다운 후 재사용.

### 3.4 4단계 — 좌표 변환 및 구역 분류

1. 카메라별 **호모그래피** \(H\): 영상 픽셀 → 지도 평면(또는 구역 폴리곤 마스크).
2. \( (x_w, y_w) = H · (cx, cy, 1) \).
3. point-in-polygon으로 `zoneId` 부여. 경계 버퍼에서 이중 소속 시 **우선순위**(입수>안전선>백사장>산책로) 또는 면적 가중.
4. **원근 보정**: 동일 사람을 바닥 접점(bbox bottom-center)으로 투영. 머리 중심 단독 사용 금지.
5. **멀티카메라**: 동일 `alignedAt` 창에서 지도 거리 < \(d_{merge}\)이고 속도·방향 유사하면 하나의 `globalTrackId`로 병합. 인원 집계는 global 기준.

면적 미설정 구역: 밀도 계산 스킵, 상태=`데이터없음`.

### 3.5 5단계 — 인원수 및 밀집도

\[
\rho_{raw}(z,t) = \frac{N(z,t)}{A_{eff}(z)}
\]

- \(N\): 중복 제거 후 구역 내 global 인원.
- \(A_{eff}\): 전체면적 − 제외면적(시설·울타리 등).

윈도우 \(W\)(예: 30–60s)에서:

\[
\rho_{adj} = \mathrm{median}\big( \{\rho_{raw}\} \cup \{\mathrm{MA}(\rho_{raw})\} \big)
\]

추가 지표(구역별):
- \(\Delta N / \Delta t\) (증가율)
- 평균 속도 \(\bar{v}\)
- 주방향 비율, 대향 흐름 충돌 지수
- 정체 비율(\(v < v_{slow}\) 및 dwell > \(T_{dwell}\))
- 출입구·안전선 버퍼 내 집중도

### 3.6 6단계 — 현재 혼잡도·위험 점수

**밀도 밴드(초기 참고, 관리자 조정 가능)**  
| 밴드 | 조건(명/㎡) | UI 3단계 매핑 |
|------|-------------|---------------|
| 안전 | \(\rho < 4\) | 안전 |
| 혼잡 | \(4 \le \rho < 5\) | 혼잡(주의) |
| 임계 주의 | \(5 \le \rho < 6\) | 혼잡 + `criticalDensityReached` |
| 위험 | \(\rho \ge 6\) | 위험 |

히스테리시스·최소 지속 시간은 기존 엔진과 동일 철학(상승 빠름, 하강 느림).

**다요인 위험 점수** \(R \in [0,100]\) (예시 가중치 — 현장 보정 전제):

| 요인 | 신호 | 가중(초기) |
|------|------|------------|
| D | 밀도 밴드 정규화 | 0.35 |
| G | 급증 \(\Delta N\) | 0.15 |
| S | 속도 급감 | 0.10 |
| C | 대향 흐름 충돌 | 0.10 |
| F | 출입구/협로 집중 | 0.10 |
| L | 안전선·통제구역 접근 | 0.10 |
| W | 기상·해양 악화 | 0.05 |
| A | 장시간 정지·이탈 | 0.05 |

\(R = 100 \sum w_i \cdot s_i\), \(s_i \in [0,1]\).  
**금지**: \(R\) 또는 \(\rho\)만으로 “익사/압사 발생” 출력.

### 3.7 7단계 — 미래 혼잡도 예측

설명 가능 가중 모델(현 구현과 동일 계열):

\[
\hat{V}(h) = V_0 \cdot f_{tod} \cdot f_{dow} \cdot f_{season} \cdot f_{wx} \cdot f_{event} \cdot f_{hist} \cdot f_{trend}
\]

출력: 시간대별 \(\hat{V}\), \(\hat{\rho}\), 예상 단계, 신뢰도, 상위 요인, 피크 슬롯.  
데이터 부족 시 신뢰도↓, 문구는 “혼잡 가능성이 높음” 수준.

### 3.8 8단계 — 경보 판단

즉시 최고 단계 금지. 승격 조건 예:

| 단계 | 조건(요약) |
|------|------------|
| 관찰 | \(R \ge R_{obs}\) 또는 보조 이상, 지속 \(T_{obs}\) |
| 주의 | 혼잡 밴드 지속 또는 \(R \ge R_{warn}\) + 증가 추세 |
| 위험 | 위험 밴드 지속 \(T_{danger}\) 또는 \(R \ge R_{danger}\) + 다센서 일치 |
| 긴급 검토 | 위험+안전선/기상 악화 동시 + 신뢰도 충분 + 관리자 미확인 |

불일치·저신뢰도: 단계 상한 캡 또는 `requires_review`.

### 3.9–3.10 알림 분리 · 지도

- **관광객**: 구역·행동 권고·대안 구역/시간. 확정 사고 표현 금지.
- **관리자**: 시각·위치·\(N,\rho\)·단계·지속·증가율·흐름·기상·근거·신뢰도·CCTV 링크·승인/보류/오판단.
- **지도**: 관광객=등급·요약; 관리자=+경로·수치·근거·stale 표시.

---

## 4. 입력·출력 데이터 표

### 입력

| 데이터 | 필수 | 형식(개념) | 누락 시 |
|--------|------|------------|---------|
| 프레임/타임스탬프/cameraId | 비전 경로 | video+meta | 시뮬레이션 또는 수동 인원 |
| Detection/Track | 비전 | bbox, trackId, conf | 인원 직접 입력 |
| Zone GIS·면적 | 예 | polygon, \(A_{eff}\) | 데이터없음 |
| 기상 | 권장 | T, sky, rain, wind | 예측 신뢰도↓ |
| 해양 | 권장 | wave, current, SST | 요인 제외+표시 |
| 행사·공휴일 | 권장 | calendar | 요인=1.0 |
| 과거 방문객 | 권장 | slot counts | 신뢰도↓ |
| 관리자 확인 | 경보 | ack/override | 에스컬레이션 |
| SK 혼잡도 | 선택 | level 1–4 | idle/캐시 |

### 출력

| 산출물 | 소비자 |
|--------|--------|
| `CrowdSnapshot`, `ZoneMetrics` | 내부 |
| `ZoneRiskState` (등급, \(R\), 근거) | API·알림 |
| `ForecastHorizon` | 지도·사전알림 |
| `AlertEvent` | 관리자 |
| `TouristMessage` / `AdminMessage` | 앱 |
| `MapLayerState` | UI |
| `HealthStatus` | 운영 |

---

## 5. 구역별 인원수·밀집도 계산식

```
N_cam(z)     = |{ globalTrackId | zone(track)=z, conf≥CONF_COUNT }|
N(z)         = merge_cameras(N_cam)          // 거리·시간 게이트
A_eff(z)     = max(0, A_total(z) - A_excl(z))
IF A_eff ≤ 0 OR N is null: status = 데이터없음|오류; STOP
ρ_raw(z,t)   = N(z,t) / A_eff(z)
ρ_adj(z,t)   = combine( movingAverage(ρ_raw, W), median(ρ_raw, W) )
ΔN(z,t)      = N(z,t) - N(z,t-Δ)
v̄(z,t)       = mean(||velocity|| of tracks in z)
```

---

## 6. 위험 점수 및 상태 판정 로직

```
band ← densityBand(ρ_adj, thresholds[z])     // 안전|혼잡|임계|위험
stableBand ← hysteresis(band, history, policy)
s_D ← normalizeBand(stableBand)
s_G ← saturate(ΔN / ΔN_ref)
s_S ← saturate((v_ref - v̄) / v_ref)
s_C ← conflictIndex(headings)
s_F ← fractionIn(entrance∪narrow)
s_L ← fractionIn(safetyLineBuffer∪restricted)
s_W ← weatherMarineHazard(wx, sea)
s_A ← fraction(stationary∨flowOutlier)
R ← 100 * Σ w_i * s_i
UI_level ← map3(stableBand)                  // 안전|혼잡|위험
flags.criticalDensityReached ← (ρ_adj ≥ critical)
ASSERT never output "익사 확정" | "압사 확정" | auto_dispatch
```

---

## 7. 미래 혼잡도 예측 알고리즘

```
INPUT: targetDate, slots[], liveTrend, hist, wx, events, beachProfile
FOR each slot h:
  V0 ← baseline(beach, dow, season) or hist.sameSlot
  factors ← {tod, dow, season, wx, event, histYoY, trend}
  Vhat ← V0
  FOR f in factors: Vhat ← Vhat * f.multiplier
  ρhat ← Vhat / A_eff_beach_or_zone
  level ← densityBand(ρhat)
  conf ← degrade(missingData, age, variance)
  record Forecast{slot, Vhat, ρhat, level, conf, topFactors}
peak ← argmax(Vhat)
IF conf < CONF_LOW: wording ← "혼잡 가능성이 높음" (확정어 금지)
```

---

## 8. 관광객용 알림 알고리즘

```
IF NOT user.consent.location: skip proximity alerts; show beach-wide only
IF alert.level is 관찰 only: no tourist push (map tint optional)
IF 혼잡|주의 and prefs.congestion:
  msg ← "현재 {zone} 혼잡도가 높아지고 있습니다. 안전을 위해 {altZone}을 이용해 주세요."
IF 위험 and prefs.danger:
  msg ← "위험 수준에 가까운 혼잡입니다. 해당 구역 접근을 자제하고 안내를 따라 주세요."
IF forecast peak within H hours and prefs.congestion:
  msg ← "{time}경 혼잡 가능성이 높습니다. 방문 시간 조정을 권장합니다."
dedupe by (user, zone, level, cooldown)
NEVER: "사고가 발생했습니다", "익사/압사"
```

---

## 9. 관리자용 경보 및 확인 알고리즘

```
ON AlertEvent e:
  status ← PENDING_REVIEW
  show: time, zone, N, ρ, R, duration, ΔN, flow, wx, reasons[], confidence, cctvUrl
  WAIT manager action:
    ACK → status=ACKNOWLEDGED; start response timer
    RESPOND → status=IN_PROGRESS
    APPROVE_ALERT → status=ACTIVE_OPS (현장 조치 기록 가능)
    HOLD → status=HELD
    FALSE_POSITIVE → status=FP; log for training; suppress similar cooldown
    RESOLVE → status=RESOLVED
  IF timeout and still PENDING: escalate (상위 관리자) — 자동 구조 명령 없음
  AI must not set status=CONFIRMED_INCIDENT without manager
```

---

## 10. 시뮬레이션 알고리즘

```
INPUT: date, startTime, speed (1 sim-sec = speed real-min), scenario
clock ← startTime; label all outputs dataSource=simulation
WHILE running:
  advance clock by speed
  spawn/despawn agents by tod + wx + event + hist curve
  update agent paths (waypoints), dwell
  feed synthetic tracks into SAME Density→Risk→Alert pipeline
  IF user seek(t'): set clock ← t'; rebuild state
UI banner: "시뮬레이션 — 실제 분석 결과가 아닙니다"
```

---

## 11. 예외 처리 로직

| 상황 | 처리 |
|------|------|
| CCTV 끊김 | Health=down; 해당 카메라 제외; 지도 `수신 실패`; 경보 상한↓ |
| 프레임 지연 | stale; 집계 보류 또는 마지막 *표시*만(판정 승격 금지) |
| 외부 API 실패 | 캐시 TTL 내 cached; 아니면 missing + 신뢰도↓ |
| 기상/해양 누락 | 요인 제외; “환경 데이터 확인 불가” |
| 면적 미설정 | 밀도 계산 안 함; 데이터없음 |
| 중복 탐지 | NMS + track + multi-cam merge |
| 카메라 흔들림/오염 | conf↓, HOLD↑; 관리자 점검 알림 |
| 야간·비·안개·역광 | 야간 모델/파라미터 또는 conf 임계↑; 미탐지율 모니터링 |
| 순간 밀집 급등 | 윈도우·히스테리시스로 완화; 단독 최고경보 금지 |
| 모델 신뢰도 저하 | requires_review; 관광객 문구 완화 |
| 관리자 미응답 | 에스컬레이션 타이머 |
| 위치 권한 거부 | 비근접 알림만 |
| 과거 데이터 부족 | 예측 신뢰도↓ |
| 잘못된 시뮬 설정 | 입력 검증 오류 반환 |
| **공통** | 임의 값 생성 금지. `데이터 확인 불가` / `예측 신뢰도 낮음` 표시 |

---

## 12. 개인정보 보호 및 AI 윤리 대책

1. 얼굴 인식·개인 신원 확인 **미사용**.
2. `trackId`는 익명·임시, 세션/일 단위 파기.
3. 원본 영상: 최소 보관, 역할 기반 접근, 감사 로그.
4. 관광객 GPS: **명시 동의** 후에만 근접 알림.
5. AI 결과 ≠ 사고 사실. UI 카피 분리.
6. 구조·통제·대피 **자동 실행 금지**. 관리자 확인·현장 SOP 필수.
7. 오경보·미탐지 기록 → 임계값·가중치 개선 데이터.
8. 시뮬레이션·모의 API는 실측과 라벨 분리.

---

## 13. 전체 알고리즘 의사코드

```
ALGORITHM BeachSafetyPipeline
INPUT:
  cameras[], zoneCatalog, thresholds, policies
  streams: video, weather, marine, events, adminActions
  mode ∈ {live, simulation, backtest}
OUTPUT:
  zoneStates[], forecasts[], alerts[], touristViews, adminViews, health

INIT health, tracks, historyBuffers, alertFSM
IF mode ≠ live: tag dataSource accordingly

LOOP forever OR until sim end:
  t ← now_aligned(mode)

  // --- ingest ---
  packets ← CollectAll(streams, t)
  FOR p in packets:
    Validate(p); AlignTime(p); Dedup(p)
    UpdateHealth(p)
  IF criticalSourcesDown(): PublishDegraded(); CONTINUE with safe caps

  // --- vision (skip if manual-only snapshot provided) ---
  IF hasVideo:
    dets ← DetectPersons(frames)
    dets ← FilterFalsePositives(dets)
    tracks ← Track(dets)                 // anonymous IDs only
    world ← ProjectAndZone(tracks, H, zoneCatalog)
    world ← MergeMultiCamera(world)
  ELSE IF hasManualCounts:
    world ← ManualToZonePeople(...)
  ELSE:
    MarkZones(데이터없음); GOTO forecast_and_ui

  // --- density ---
  FOR z in zones:
    N ← Count(world, z)
    ρ_raw ← N / A_eff(z) IF A_eff>0 ELSE NULL
    ρ_adj ← Smooth(ρ_raw, history[z])
    metrics[z] ← {N, ρ_adj, ΔN, v̄, conflict, dwellFrac, ...}

  // --- risk ---
  FOR z in zones:
    band ← ClassifyDensity(ρ_adj, thresholds[z])
    band ← Hysteresis(band, history[z])
    R ← RiskScore(metrics[z], weather, marine)
    zoneStates[z] ← {band, R, reasons, confidence, criticalFlag}
    // NO accident confirmation

  // --- forecast ---
  forecasts ← ForecastHorizon(t, hist, weather, events, liveTrend, beach)
  DowngradeConfidenceIfMissing(forecasts)

  // --- alerts ---
  FOR z in zones:
    stage ← AlertFSM(zoneStates[z], duration, multiCamAgree, confidence)
    IF stage promotes:
      e ← CreateAlert(z, stage, evidence)
      e.status ← PENDING_REVIEW
      NotifyAdmin(e)
      IF stage ≥ 주의 AND touristPolicy: NotifyTourist(SoftCopy(e))

  ApplyAdminActions(adminActions)   // ack/hold/fp/resolve — no auto rescue

  // --- views ---
  touristViews ← BuildTouristMap(zoneStates, forecasts, health)
  adminViews ← BuildAdminMonitor(zoneStates, tracks, forecasts, alerts, health)
  Publish(touristViews, adminViews)

  Sleep(cyclePeriod)
END LOOP
```

---

## 14. 개발자 구현 순서도 (권장 마일스톤)

```
M0 구역·면적·임계값 설정/감사
    ↓
M1 수동 인원 → 밀도·3단계 등급·히스테리시스     ← (현재 코어에 해당)
    ↓
M2 알림 FSM·관광객/관리자 메시지 분리             ← (현재 알림 모듈)
    ↓
M3 기상·예측·시뮬·백테스트                        ← (현재 forecast)
    ↓
M4 WaveGuard UI·운영도구·설정                     ← (현재 public)
    ↓
M5 CCTV 어댑터: Detection JSON 수신 → 동일 엔진
    ↓
M6 탐지·추적 서비스(별도 GPU 워커) + 익명 ID
    ↓
M7 호모그래피·구역 폴리곤·멀티캠 병합
    ↓
M8 위험점수 다요인·경보 지속성·에스컬레이션 고도화
    ↓
M9 해양 API·안전선 버퍼·동의 기반 근접 알림
    ↓
M10 성능평가 대시보드·오탐/미탐 피드백 루프
```

---

## 15. 테스트 시나리오와 성능 평가 기준

### 시나리오 (합/불 판정은 현장 라벨 필요 — 수치 목표치는 검증 후 확정)

| ID | 시나리오 | 기대 |
|----|----------|------|
| T1 | 정상 저밀도 | 안전, 경보 없음 |
| T2 | 밀도 4→5 지속 | 혼잡, 임계 플래그, 즉시 위험 아님 |
| T3 | 순간 스파이크 1프레임 | 스무딩으로 미승격 |
| T4 | 위험 밀도 지속 + 관리자 미확인 | 위험→에스컬레이션, 사고확정 문구 없음 |
| T5 | CCTV 끊김 | 데이터 확인 불가, 임의 채움 없음 |
| T6 | 기상 API 실패 | 캐시/missing, 예측 신뢰도↓ |
| T7 | 시뮬 배속 | 동일 로직, simulation 배지 |
| T8 | 오판단 기록 | FP 쿨다운, 로그 |
| T9 | 위치 권한 거부 | 비근접만 |
| T10 | 멀티캠 동일인 | N 이중 계산 없음 |

### 평가 지표 (측정 방법만 정의, 허구 점수 없음)

| 지표 | 측정 |
|------|------|
| 탐지 정확도 | 라벨 프레임 대비 Precision/Recall |
| ID 유지율 | MOTA/IDF1 또는 ID switch rate |
| 인원 오차 | \|N_pred−N_gt\| / N_gt |
| 밀도 오차 | MAE(ρ) |
| 단계 분류 | confusion matrix vs 관리자 라벨 |
| 방문객 예측 | MAE/MAPE (백테스트) |
| 오경보율 | FP / 발송 |
| 미탐지율 | FN / 위험 구간 |
| 경보 지연 | 기준 충족→알림까지 시간 |
| 결측 안정성 | 예외 시나리오 T5–T6 |
| 관리자 일치율 | AI 단계 vs 승인 결과 |

백테스트: 과거 시계열을 시간순 재생. 시뮬: 가상 유입·악천후·행사 충돌.

---

## 16. 구현에 필요한 데이터·기술과 남은 제한사항

### 필요 데이터
- 구역 폴리곤·유효면적 실측, 카메라↔지도 캘리브레이션
- 라벨드 영상(주간/야간/악천후), 관리자 확인·사고·조치 로그
- 기상·해양·행사 피드를 쓸 **실제 계약/키** (없으면 mock + 상태 표시)
- (선택) SK 장소 혼잡도 — 보조만

### 기술 제안과 대체

| 역할 | 제안 예 | 선택 이유 | 대체 |
|------|---------|-----------|------|
| 사람 탐지 | YOLO 계열 RT | 실시간·person | 기타 1-stage / 클라우드 Vision(개인정보 검토) |
| 추적 | ByteTrack | 단순·빠름 | DeepSORT, OC-SORT |
| 좌표 | 호모그래피+폴리곤 | 해변 평면 근사 | 구역 마스크만, LiDAR(고비용) |
| 예측 | 규칙·가중치 | 설명 가능·데이터 적을 때 | 이후 GBM/시계열(설명력 유지 시) |
| 보조 혼잡 | SK Puzzle | 광역 보정 | 없이 운영 가능 |
| 앱 | 현재 WaveGuard 웹 | 기존 코드 | 네이티브 래핑 |

### 남은 제한사항 (현재 저장소 기준)
- 실시간 GPU 탐지·추적 파이프라인 미구축(어댑터·수동/테스트 중심).
- 호모그래피·멀티캠 병합 미구현.
- 해양 전용 피드·안전선 기하 미완.
- 밀도·알림·예측·UI는 동작하나, **현장 검증 전 임계값·가중치는 참고값**.
- Free tier SK 등 외부 API 한도·미구독 시 폴백 필요.
- AI가 사고를 확정하거나 구조를 자동 지시하지 않음 — **제품 카피에 고정**.

---

## 부록 A. 현재 저장소 모듈 매핑

| 설계 단계 | 코드 위치(존재 시) |
|-----------|-------------------|
| 밀도·등급·히스테리시스 | `src/density/*` |
| 구역·면적 | `config/zones.*.json`, `src/zone/*` |
| 알림 | `src/notification/*` |
| 예측·기상·시뮬·백테스트 | `src/forecast/*` |
| SK 보조 | `src/forecast/telecomProvider.ts` |
| API·WaveGuard UI | `src/api/*`, `public/*` |
| CCTV 탐지·추적·지오매핑 | **미구현 — M5–M7** |

---

*문서 버전: 2026-07-27 · WaveGuard / haeundae-crowd-density 설계 확장*
