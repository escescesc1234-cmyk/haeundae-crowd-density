/**
 * 밀도 분석 결과 → 알림 입력 변환
 */

import type { DensityAnalysisResult, RiskLevel } from "../types/index.js";
import type {
  DataFreshnessStatus,
  DensityResultWithContext,
  NotificationAnalysisInput,
} from "./types.js";
import { parseIsoMs } from "../density/smoothing.js";
import { loadNotificationPolicy } from "./config.js";

function inferDataFreshness(
  measuredAt: string,
  warnings: string[],
  nowIso: string,
): DataFreshnessStatus {
  const policy = loadNotificationPolicy();
  const ageSec = (parseIsoMs(nowIso) - parseIsoMs(measuredAt)) / 1000;
  if (warnings.some((w) => w.includes("갱신되지 않"))) return "stale";
  if (ageSec > policy.staleDataSeconds) return "stale";
  return "fresh";
}

function extractAdditionalFactors(result: DensityAnalysisResult): string[] {
  return result.auxiliaryAlerts.map((a) => a.message);
}

export function buildNotificationInput(
  result: DensityResultWithContext,
  options?: {
    previousRiskLevel?: RiskLevel | null;
    currentLevelDurationSeconds?: number;
    densityIncreaseRatePerSecond?: number;
    nowIso?: string;
    nearbyTouristCount?: number;
    managerConfirmStatus?: string;
    manualCongestionBroadcast?: boolean;
  },
): NotificationAnalysisInput {
  const nowIso = options?.nowIso ?? new Date().toISOString();
  const dataFreshness =
    result.dataFreshness ??
    inferDataFreshness(result.measuredAt, result.warnings, nowIso);

  const suddenScatterOrCollapse = Boolean(
    result.suddenScatterOrCollapse ??
      result.auxiliaryAlerts.some((a) => a.code === "SUDDEN_SCATTER"),
  );

  return {
    zoneId: result.zoneId,
    zoneName: result.zoneName,
    zoneType: result.zoneType,
    detectedPeople: result.detectedPeople,
    effectiveAreaSquareMeters: result.effectiveAreaSquareMeters,
    rawDensity: result.rawDensity,
    adjustedDensity: result.adjustedDensity,
    currentRiskLevel: result.riskLevel,
    previousRiskLevel:
      options?.previousRiskLevel ?? result.previousRiskLevel ?? null,
    thresholds: {
      congestionStartDensity: result.thresholds.congestionStartDensity,
      criticalDensity: result.thresholds.criticalDensity,
      highRiskDensity: result.thresholds.highRiskDensity,
    },
    criticalDensityReached: result.criticalDensityReached,
    approachingHighRisk: result.approachingHighRisk,
    densityTrend: result.trend,
    densityIncreaseRatePerSecond:
      options?.densityIncreaseRatePerSecond ??
      result.densityIncreaseRatePerSecond ??
      0,
    currentLevelDurationSeconds:
      options?.currentLevelDurationSeconds ??
      result.currentLevelDurationSeconds ??
      0,
    confidence: result.confidence,
    measuredAt: result.measuredAt,
    dataFreshness,
    lowConfidence: result.lowConfidence,
    additionalRiskFactors: extractAdditionalFactors(result),
    managerConfirmStatus:
      options?.managerConfirmStatus ?? result.managerConfirmStatus ?? "unconfirmed",
    nearbyTouristCount:
      options?.nearbyTouristCount ?? result.nearbyTouristCount ?? 0,
    reason: result.reason,
    errors: result.errors,
    warnings: result.warnings,
    densityBasedSafeButOtherRisks: result.densityBasedSafeButOtherRisks,
    suddenScatterOrCollapse,
    isTestData: result.isTestData,
    manualCongestionBroadcast: options?.manualCongestionBroadcast,
  };
}
