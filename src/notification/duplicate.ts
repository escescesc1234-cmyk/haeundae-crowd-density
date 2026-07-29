/**
 * 중복 알림 방지
 */

import { parseIsoMs } from "../density/smoothing.js";
import type {
  AlertEvent,
  NotificationAnalysisInput,
  NotificationPolicy,
  NotificationPriority,
  NotificationTriggerReason,
} from "./types.js";

export interface DuplicateCheckResult {
  isDuplicate: boolean;
  allowResend: boolean;
  existingEventId?: string;
  reason?: string;
}

function samePriorityFamily(
  a: NotificationPriority,
  b: NotificationPriority,
): boolean {
  if (a === b) return true;
  if (a === "system" && b === "system") return true;
  if (typeof a === "number" && typeof b === "number") {
    return Math.abs(a - b) <= 1;
  }
  return false;
}

export function preventDuplicateNotification(
  input: NotificationAnalysisInput,
  priority: NotificationPriority,
  reasons: NotificationTriggerReason[],
  activeEvents: AlertEvent[],
  policy: NotificationPolicy,
): DuplicateCheckResult {
  const now = Date.now();
  const zoneEvents = activeEvents
    .filter(
      (e) =>
        e.zoneId === input.zoneId &&
        e.status !== "해결됨" &&
        e.status !== "오경보" &&
        e.status !== "자동 종료",
    )
    .sort((a, b) => parseIsoMs(b.createdAt) - parseIsoMs(a.createdAt));

  const latest = zoneEvents[0];
  if (!latest) {
    return { isDuplicate: false, allowResend: true };
  }

  const elapsedSec = (now - parseIsoMs(latest.createdAt)) / 1000;
  const densityDelta = Math.abs(
    (input.adjustedDensity ?? 0) - (latest.adjustedDensity ?? 0),
  );

  const resendCodes = new Set([
    "CRITICAL_DENSITY",
    "RAPID_RISE",
    "SUDDEN_MOVEMENT",
    "MANUAL_BROADCAST",
    "LEVEL_UP_SAFE_TO_BUSY",
    "LEVEL_UP_BUSY_TO_DANGER",
    "LEVEL_UP_SAFE_TO_DANGER",
    "OTHER_RISKS_WHILE_SAFE",
  ]);

  const hasResendTrigger = reasons.some((r) => resendCodes.has(r.code));

  if (priority === 3 || priority === 4) {
    if (elapsedSec >= policy.dangerRepeatIntervalSeconds) {
      return {
        isDuplicate: true,
        allowResend: true,
        existingEventId: latest.eventId,
        reason: "위험 지속 — 반복 주기 도달",
      };
    }
    if (densityDelta >= policy.minDensityChangeForResend) {
      return {
        isDuplicate: true,
        allowResend: true,
        existingEventId: latest.eventId,
        reason: "밀도 추가 상승",
      };
    }
    if (hasResendTrigger && latest.currentRiskLevel !== input.currentRiskLevel) {
      return { isDuplicate: false, allowResend: true };
    }
  }

  if (priority === "system") {
    if (elapsedSec < policy.sameLevelCooldownSeconds / 2) {
      return {
        isDuplicate: true,
        allowResend: false,
        existingEventId: latest.eventId,
        reason: "시스템 경고 쿨다운",
      };
    }
    return { isDuplicate: false, allowResend: true };
  }

  if (
    samePriorityFamily(latest.priority, priority) &&
    latest.currentRiskLevel === input.currentRiskLevel &&
    elapsedSec < policy.sameLevelCooldownSeconds &&
    !hasResendTrigger &&
    densityDelta < policy.minDensityChangeForResend
  ) {
    return {
      isDuplicate: true,
      allowResend: false,
      existingEventId: latest.eventId,
      reason: `동일 등급 재발송 제한 (${Math.floor(elapsedSec)}초 / ${policy.sameLevelCooldownSeconds}초)`,
    };
  }

  if (
    input.currentRiskLevel === "안전" &&
    (input.previousRiskLevel === "혼잡" ||
      input.previousRiskLevel === "위험") &&
    input.currentLevelDurationSeconds < policy.minLevelDurationSeconds
  ) {
    return {
      isDuplicate: true,
      allowResend: false,
      existingEventId: latest.eventId,
      reason: "안전 전환 최소 지속 시간 미달",
    };
  }

  return {
    isDuplicate: Boolean(latest),
    allowResend: true,
    existingEventId: latest.eventId,
  };
}

export function shouldSendNotification(
  evaluation: { shouldNotify: boolean; suppressReason?: string },
): boolean {
  return evaluation.shouldNotify;
}
