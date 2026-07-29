/**
 * 관광객용 메시지 생성기
 */

import type {
  NotificationAnalysisInput,
  NotificationPolicy,
  NotificationPriority,
  TouristNotificationMessage,
  TouristUserProfile,
} from "./types.js";

function formatDensity(d: number | null): string | null {
  if (d === null || !Number.isFinite(d)) return null;
  return d.toFixed(1);
}

function safeAlternativeZone(input: NotificationAnalysisInput): string {
  switch (input.zoneType) {
    case "entrance":
      return "백사장 외곽 또는 샤워장·편의시설이 적은 구역";
    case "swimming":
      return "백사장 육상 구역 또는 해안선 인접 여유 공간";
    case "shoreline":
      return "백사장 중앙 또는 출입구 반대편";
    default:
      return "혼잡도가 낮은 인근 구역 또는 안내 표지판이 있는 우회 경로";
  }
}

export function generateTouristMessage(
  input: NotificationAnalysisInput,
  user: TouristUserProfile,
  priority: NotificationPriority,
  policy: NotificationPolicy,
  options?: { forceRelief?: boolean },
): TouristNotificationMessage | null {
  const densityStr = formatDensity(input.adjustedDensity);
  const insideZone =
    user.insideRiskZone ||
    user.currentZoneId === input.zoneId;

  if (priority === "system") return null;

  if (
    options?.forceRelief ||
    ((input.previousRiskLevel === "혼잡" ||
      input.previousRiskLevel === "위험") &&
      input.currentLevelDurationSeconds >= policy.minLevelDurationSeconds)
  ) {
    if (input.currentRiskLevel === "안전") {
      return {
        audience: "tourist",
        zoneId: input.zoneId,
        zoneName: input.zoneName,
        riskLevel: "안전",
        title: "혼잡 완화 안내",
        body: `${input.zoneName}의 혼잡도가 낮아져 현재 안전 단계로 전환되었습니다. 주변 상황과 안전요원의 안내를 계속 확인해 주세요.`,
        actionHint: "평소와 같이 이용 가능하나 안내 방송에 주의하세요.",
        safeAlternative: safeAlternativeZone(input),
        channels: insideZone ? ["in_app_banner", "push"] : ["in_app_banner"],
        soundAndVibration: false,
        priority: 0,
        updatedAt: input.measuredAt,
        showDensity: densityStr ? Number(densityStr) : null,
      };
    }
  }

  if (input.currentRiskLevel === "안전" && priority === 0) {
    return null;
  }

  if (input.currentRiskLevel === "혼잡") {
    const critical = input.criticalDensityReached;
    return {
      audience: "tourist",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: "혼잡",
      title: critical ? "혼잡도 급증 안내" : "혼잡 안내",
      body: critical
        ? `${input.zoneName}의 혼잡도가 빠르게 증가하고 있습니다. 해당 구역으로의 이동을 자제하고 안내된 우회 경로를 이용해 주세요.`
        : `현재 ${input.zoneName}이 혼잡합니다. 이동 시 주변 사람과 충분한 거리를 유지하고, 가능하면 혼잡도가 낮은 구역을 이용해 주세요.`,
      actionHint: "뛰거나 밀지 말고 천천히 이동하세요.",
      safeAlternative: safeAlternativeZone(input),
      channels: ["in_app_banner", "push", "map_overlay"],
      soundAndVibration: false,
      priority: critical ? 2 : 1,
      updatedAt: input.measuredAt,
      showDensity: densityStr ? Number(densityStr) : null,
    };
  }

  if (input.currentRiskLevel === "위험") {
    if (insideZone) {
      return {
        audience: "tourist",
        zoneId: input.zoneId,
        zoneName: input.zoneName,
        riskLevel: "위험",
        title: "위험 알림 — 현재 위치",
        body: `현재 위치가 위험 구역 안에 있습니다. 뛰거나 밀지 말고 천천히 안내된 안전 방향으로 이동해 주세요. 현장 안전요원의 안내에 따라 주시기 바랍니다.`,
        actionHint: "즉시 안내된 방향으로 천천히 이동",
        safeAlternative: safeAlternativeZone(input),
        channels: ["in_app_banner", "push", "map_overlay"],
        soundAndVibration: true,
        priority: priority === 4 ? 4 : 3,
        updatedAt: input.measuredAt,
        showDensity: densityStr ? Number(densityStr) : null,
      };
    }
    return {
      audience: "tourist",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: "위험",
      title: "위험 알림",
      body: `위험 알림: 현재 ${input.zoneName}의 인구 밀집도가 매우 높습니다. 해당 구역으로 이동하지 말고, 안내된 방향을 따라 혼잡 지역에서 벗어나 주세요. 현장 안전요원의 안내에 따라 주시기 바랍니다.`,
      actionHint: "해당 구역 진입 금지 · 우회 이동",
      safeAlternative: safeAlternativeZone(input),
      channels: ["in_app_banner", "push", "map_overlay"],
      soundAndVibration: true,
      priority: priority === 4 ? 4 : 3,
      updatedAt: input.measuredAt,
      showDensity: densityStr ? Number(densityStr) : null,
    };
  }

  if (input.densityBasedSafeButOtherRisks) {
    return {
      audience: "tourist",
      zoneId: input.zoneId,
      zoneName: input.zoneName,
      riskLevel: "안전",
      title: "주변 상황 안내",
      body: `${input.zoneName}은 비교적 원활하나 주변에서 주의가 필요한 상황이 감지되었습니다. 안내 표지와 안전요원의 안내를 확인해 주세요.`,
      actionHint: "불필요한 체류를 줄이고 이동 경로를 확인하세요.",
      safeAlternative: safeAlternativeZone(input),
      channels: ["in_app_banner"],
      soundAndVibration: false,
      priority: 1,
      updatedAt: input.measuredAt,
      showDensity: densityStr ? Number(densityStr) : null,
    };
  }

  return null;
}
