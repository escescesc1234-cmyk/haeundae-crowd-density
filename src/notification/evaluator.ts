/**
 * 알림 이벤트 평가 — 발송 필요 여부 판단
 */

import type {
  AlertEvent,
  NotificationAnalysisInput,
  NotificationEvaluationResult,
  NotificationPolicy,
  NotificationTriggerReason,
} from "./types.js";
import { determineNotificationPriority } from "./priority.js";
import { preventDuplicateNotification } from "./duplicate.js";

function isStableRisk(level: string | null | undefined): boolean {
  return level === "안전" || level === "혼잡" || level === "위험";
}

function detectSystemWarning(input: NotificationAnalysisInput): boolean {
  return (
    input.currentRiskLevel === "오류" ||
    input.currentRiskLevel === "데이터없음" ||
    input.dataFreshness === "stale" ||
    input.dataFreshness === "missing" ||
    input.lowConfidence ||
    input.errors.length > 0
  );
}

function buildTriggerReasons(
  input: NotificationAnalysisInput,
  policy: NotificationPolicy,
  activeEvents: AlertEvent[],
): NotificationTriggerReason[] {
  const reasons: NotificationTriggerReason[] = [];
  const prev = input.previousRiskLevel;
  const cur = input.currentRiskLevel;

  const hasActiveHighEvent = activeEvents.some(
    (e) =>
      e.zoneId === input.zoneId &&
      (e.currentRiskLevel === "혼잡" || e.currentRiskLevel === "위험"),
  );

  if (prev === "안전" && cur === "혼잡") {
    reasons.push({
      code: "LEVEL_UP_SAFE_TO_BUSY",
      message: "안전에서 혼잡으로 등급 상승",
    });
  }
  if (prev === "혼잡" && cur === "위험") {
    reasons.push({
      code: "LEVEL_UP_BUSY_TO_DANGER",
      message: "혼잡에서 위험으로 등급 상승",
    });
  }
  if (prev === "안전" && cur === "위험") {
    reasons.push({
      code: "LEVEL_UP_SAFE_TO_DANGER",
      message: "안전에서 위험으로 급상승",
    });
  }
  if (input.criticalDensityReached) {
    reasons.push({
      code: "CRITICAL_DENSITY",
      message: "임계 밀도 도달",
    });
  }
  if (input.densityIncreaseRatePerSecond >= policy.rapidRisePerSecond) {
    reasons.push({
      code: "RAPID_RISE",
      message: `밀도 급상승 (${input.densityIncreaseRatePerSecond.toFixed(2)}명/㎡·초)`,
    });
  }
  if (input.manualCongestionBroadcast) {
    reasons.push({
      code: "MANUAL_BROADCAST",
      message: "관리자 수동 혼잡 안내",
    });
  }
  if (
    (hasActiveHighEvent ||
      prev === "혼잡" ||
      prev === "위험") &&
    cur === "안전" &&
    input.currentLevelDurationSeconds >= policy.minLevelDurationSeconds
  ) {
    reasons.push({
      code: "LEVEL_DOWN_TO_SAFE",
      message: "안전 단계로 안정적 전환",
    });
  }
  if (prev === "위험" && cur === "혼잡") {
    reasons.push({
      code: "LEVEL_DOWN_DANGER_TO_BUSY",
      message: "위험에서 혼잡으로 완화",
    });
  }
  if (
    input.densityBasedSafeButOtherRisks ||
    (input.currentRiskLevel === "안전" && input.additionalRiskFactors.length > 0)
  ) {
    reasons.push({
      code: "OTHER_RISKS_WHILE_SAFE",
      message: "밀도 기준 안전 + 추가 위험 요인",
    });
  }
  if (input.suddenScatterOrCollapse) {
    reasons.push({
      code: "SUDDEN_MOVEMENT",
      message: "갑작스러운 움직임 패턴 감지 — 현장 확인 필요",
    });
  }
  if (input.dataFreshness === "stale") {
    reasons.push({
      code: "STALE_DATA",
      message: "CCTV/측정 데이터 갱신 지연",
    });
  }
  if (input.lowConfidence) {
    reasons.push({
      code: "LOW_CONFIDENCE",
      message: "분석 신뢰도 저하",
    });
  }
  return reasons;
}

export function shouldSendTouristNotification(
  input: NotificationAnalysisInput,
  priority: ReturnType<typeof determineNotificationPriority>,
  reasons: NotificationTriggerReason[],
): boolean {
  if (priority === "system") return false;
  if (priority === 0) {
    return reasons.some(
      (r) =>
        r.code === "LEVEL_DOWN_TO_SAFE" ||
        r.code === "OTHER_RISKS_WHILE_SAFE",
    );
  }
  if (input.currentRiskLevel === "안전") {
    return reasons.some(
      (r) =>
        r.code === "LEVEL_DOWN_TO_SAFE" ||
        r.code === "OTHER_RISKS_WHILE_SAFE",
    );
  }
  return (
    input.currentRiskLevel === "혼잡" ||
    input.currentRiskLevel === "위험" ||
    (typeof priority === "number" && priority >= 2) ||
    reasons.some((r) => r.code === "MANUAL_BROADCAST")
  );
}

export function shouldSendManagerNotification(
  input: NotificationAnalysisInput,
  priority: ReturnType<typeof determineNotificationPriority>,
  reasons: NotificationTriggerReason[],
): boolean {
  if (priority === "system") return true;
  if (priority === 0) {
    return reasons.some(
      (r) =>
        r.code === "LEVEL_DOWN_TO_SAFE" ||
        r.code === "LEVEL_DOWN_DANGER_TO_BUSY",
    );
  }
  return (
    input.currentRiskLevel !== "안전" ||
    reasons.length > 0 ||
    input.densityBasedSafeButOtherRisks
  );
}

export function evaluateNotificationEvent(
  input: NotificationAnalysisInput,
  policy: NotificationPolicy,
  activeEvents: AlertEvent[],
): NotificationEvaluationResult {
  const isSystemWarning = detectSystemWarning(input);
  const priority = determineNotificationPriority(
    input,
    policy,
    isSystemWarning,
  );
  const triggerReasons = buildTriggerReasons(input, policy, activeEvents);

  if (isSystemWarning && triggerReasons.length === 0) {
    triggerReasons.push({
      code: "SYSTEM_DATA_ISSUE",
      message: "데이터 오류 또는 신뢰도 저하",
    });
  }

  const touristRequired = shouldSendTouristNotification(
    input,
    priority,
    triggerReasons,
  );
  const managerRequired = shouldSendManagerNotification(
    input,
    priority,
    triggerReasons,
  );

  if (!touristRequired && !managerRequired) {
    return {
      shouldNotify: false,
      isDuplicate: false,
      isSystemWarning,
      priority,
      touristRequired: false,
      managerRequired: false,
      triggerReasons,
      suppressReason: "발송 조건 미충족 (안전 단계 유지)",
    };
  }

  const dup = preventDuplicateNotification(
    input,
    priority,
    triggerReasons,
    activeEvents,
    policy,
  );

  if (dup.isDuplicate && !dup.allowResend) {
    return {
      shouldNotify: false,
      isDuplicate: true,
      isSystemWarning,
      priority,
      touristRequired,
      managerRequired,
      triggerReasons,
      updateExistingEventId: dup.existingEventId,
      suppressReason: dup.reason,
    };
  }

  return {
    shouldNotify: true,
    isDuplicate: dup.isDuplicate,
    isSystemWarning,
    priority,
    touristRequired,
    managerRequired,
    triggerReasons,
    updateExistingEventId: dup.existingEventId,
  };
}
