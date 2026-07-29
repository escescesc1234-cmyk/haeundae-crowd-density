# 실시간 안전 알림 시스템 설계

## 1. 설계 개요

밀도 분석 모듈(`DensityAnalysisResult`)의 출력을 입력으로 받아 **이벤트 기반**으로 관광객·관리자 알림을 생성·발송한다.

```
밀도 분석 → buildNotificationInput → evaluateNotificationEvent
  → (중복 검사) → 메시지 생성 → 수신자 선택 → 채널 발송 → 기록 저장
  → (미응답 시) escalateUnacknowledgedAlert
```

| 모듈 | 책임 |
|------|------|
| `adapter.ts` | 분석 결과 → 알림 입력 변환 |
| `evaluator.ts` | `evaluateNotificationEvent`, 발송 조건 |
| `priority.ts` | `determineNotificationPriority` |
| `duplicate.ts` | `preventDuplicateNotification`, `shouldSendNotification` |
| `touristMessage.ts` | `generateTouristMessage` |
| `managerMessage.ts` | `generateManagerMessage` |
| `recipients.ts` | `selectTouristRecipients`, `selectManagerRecipients` |
| `send.ts` | `sendNotification`, `retryFailedNotification` |
| `escalation.ts` | `acknowledgeManagerAlert`, `escalateUnacknowledgedAlert`, `resolveAlertEvent` |
| `service.ts` | `NotificationService` 오케스트레이션 |
| `channels/mockChannel.ts` | 테스트용 가상 발송 (FCM/APNs 미연동) |

## 2. 관광객 vs 관리자 알림 차이

| | 관광객 | 관리자 |
|---|--------|--------|
| 초점 | 행동 지침, 이동 방향 | 수치, 판단 근거, 대응 |
| 밀도 표시 | 선택적(소수점 1자리) | 원본·보정·임계값 포함 |
| 채널 | 앱 배너, 푸시, 지도 | 대시보드, 소리/진동 |
| 안전 단계 | 반복 푸시 없음 | 대시보드만 표시 |
| 표현 | "위험 가능성 감지" | CCTV 링크, 확인 버튼 |

## 3. 위험 등급별 발송 조건

- **안전**: 일반 푸시 없음. 혼잡/위험에서 안전으로 **최소 지속 시간** 후 해제 알림.
- **혼잡**: 등급 상승, 임계 밀도 도달, 급상승, 구역 진입, 수동 안내 시 발송.
- **위험**: 구역 내·인접·접근 중 관광객 + 담당/확대 관리자. 소리·진동 가능.
- **시스템 경고**: stale 데이터, 낮은 신뢰도, 오류 — 일반 위험과 분리.

## 4. 우선순위 · 중복 방지

| 우선순위 | 의미 |
|----------|------|
| 0 | 일반 정보 |
| 1 | 혼잡 주의 |
| 2 | 임계 밀도 도달 |
| 3 | 위험 경보 |
| 4 | 즉각 복합 위험 |
| system | 데이터/CCTV 오류 |

설정(`config/notification.default.json`):
- `sameLevelCooldownSeconds`: 동일 등급 재발송 제한
- `minLevelDurationSeconds`: 해제 알림 전 안전 유지 시간
- `dangerRepeatIntervalSeconds`: 위험 반복 주기
- `managerAckTimeoutSeconds`: 관리자 미응답 확대 대기
- `minDensityChangeForResend`: 재발송 최소 밀도 변화

## 5. 상태 전이

`안전→혼잡` · `혼잡→임계` · `혼잡→위험` · `위험→혼잡` · `혼잡→안전`

이벤트 상태: 생성됨 → 발송됨 → 관리자 확인 → 현장 확인 중 → 대응 중 → 해결됨 / 오경보 / 자동 종료

## 6. API

- `POST /api/analyze/manual` + `notify: true` — 분석+알림 일괄
- `POST /api/notifications/process` — 분석 결과만 전달
- `GET /api/notifications/events` — 이벤트 목록
- `POST /api/notifications/acknowledge` — 관리자 확인
- `POST /api/notifications/respond` — 대응 시작
- `POST /api/notifications/resolve` — 해결
- `POST /api/notifications/escalate-check` — 미응답 확대

## 7. 실제 푸시 연결 방법

1. `NotificationChannel` 인터페이스 구현 (`src/notification/types.ts`)
2. FCM/APNs 어댑터를 `channels/`에 추가
3. `NotificationService` 생성자에서 mock 대신 실제 채널 주입
4. 발송 실패 시 `retryFailedNotification` + 대체 채널(SMS 등) 연동

## 8. 개인정보 · 안전

- 위치 동의 없으면 개인 위치 기반 푸시 미발송 (전체 공지·지도만)
- 얼굴 인식·신원 매핑 없음
- 사고 확정 표현 금지 ("압사 발생" 등)
- AI 판단은 참고 정보, 관리자가 CCTV·현장 확인 후 최종 대응

## 9. 제한 사항

- 푸시/SMS/방송은 **모의 채널**만 구현
- 사용자·관리자 프로필은 인메모리 샘플
- 이벤트·발송 기록은 프로세스 메모리 (재시작 시 초기화)
