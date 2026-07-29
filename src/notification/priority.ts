/**
 * 알림 우선순위 결정
 */

import type {
  NotificationAnalysisInput,
  NotificationPolicy,
  NotificationPriority,
} from "./types.js";

export function determineNotificationPriority(
  input: NotificationAnalysisInput,
  policy: NotificationPolicy,
  isSystemWarning: boolean,
): NotificationPriority {
  if (isSystemWarning) return "system";

  const density = input.adjustedDensity ?? 0;
  const emergencyCut =
    input.thresholds.highRiskDensity * policy.emergencyDensityMultiplier;

  if (
    input.suddenScatterOrCollapse ||
    (density >= emergencyCut && input.currentRiskLevel === "위험")
  ) {
    return 4;
  }

  if (input.currentRiskLevel === "위험") return 3;
  if (input.criticalDensityReached || input.approachingHighRisk) return 2;
  if (input.currentRiskLevel === "혼잡") return 1;
  if (
    input.densityBasedSafeButOtherRisks ||
    input.additionalRiskFactors.length > 0
  ) {
    return 1;
  }
  return 0;
}

export function priorityLabel(priority: NotificationPriority): string {
  switch (priority) {
    case 0:
      return "일반 상태 정보";
    case 1:
      return "혼잡 주의";
    case 2:
      return "임계 밀도 도달";
    case 3:
      return "위험 경보";
    case 4:
      return "즉각적 복합 위험";
    case "system":
      return "시스템 경고";
  }
}

export function isHighPriority(priority: NotificationPriority): boolean {
  return priority === 3 || priority === 4;
}
