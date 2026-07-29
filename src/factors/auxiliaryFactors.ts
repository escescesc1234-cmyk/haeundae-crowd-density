/**
 * 보조 위험 요인 — 밀도를 조작하지 않고 별도 경고로 표시
 */

import type { AuxiliaryAlert, AuxiliaryRiskFactors, RiskLevel } from "../types/index.js";

export function evaluateAuxiliaryFactors(
  factors: AuxiliaryRiskFactors | undefined,
  densityRiskLevel: RiskLevel,
): {
  alerts: AuxiliaryAlert[];
  densityBasedSafeButOtherRisks: boolean;
} {
  const alerts: AuxiliaryAlert[] = [];
  if (!factors) {
    return { alerts, densityBasedSafeButOtherRisks: false };
  }

  if (
    factors.densityIncreaseRatePerSecond !== undefined &&
    factors.densityIncreaseRatePerSecond >= 0.1
  ) {
    alerts.push({
      code: "DENSITY_RISE_RATE",
      message: `최근 밀도 증가 속도가 높음 (${factors.densityIncreaseRatePerSecond.toFixed(3)}명/㎡·초)`,
      severity: "warning",
    });
  }

  if (
    factors.directionalFlowRatio !== undefined &&
    factors.directionalFlowRatio >= 0.7
  ) {
    alerts.push({
      code: "DIRECTIONAL_FLOW",
      message: `특정 방향 이동 비율이 높음 (${(factors.directionalFlowRatio * 100).toFixed(0)}%)`,
      severity: "warning",
    });
  }

  if (factors.entranceCongestion) {
    alerts.push({
      code: "ENTRANCE_CONGESTION",
      message: "출입구 주변 정체가 감지됨",
      severity: "warning",
    });
  }

  if (factors.opposingCrowdFlow) {
    alerts.push({
      code: "OPPOSING_FLOW",
      message: "서로 반대 방향으로 이동하는 군중이 감지됨",
      severity: "critical",
    });
  }

  if (
    factors.stationaryObjectRatio !== undefined &&
    factors.stationaryObjectRatio >= 0.5
  ) {
    alerts.push({
      code: "STATIONARY_OBJECTS",
      message: `장시간 미이동 객체 비율이 높음 (${(factors.stationaryObjectRatio * 100).toFixed(0)}%)`,
      severity: "warning",
    });
  }

  if (factors.suddenScatterOrCollapse) {
    alerts.push({
      code: "SUDDEN_SCATTER",
      message: "갑작스러운 쓰러짐 또는 흩어짐 움직임이 감지됨",
      severity: "critical",
    });
  }

  if (factors.waveHeightMeters !== undefined && factors.waveHeightMeters >= 1.5) {
    alerts.push({
      code: "WAVE_HEIGHT",
      message: `파고가 높음 (${factors.waveHeightMeters}m)`,
      severity: "warning",
    });
  }

  if (factors.tideCondition) {
    alerts.push({
      code: "TIDE",
      message: `조류 상태: ${factors.tideCondition}`,
      severity: "info",
    });
  }

  if (factors.weatherCondition) {
    alerts.push({
      code: "WEATHER",
      message: `기상 상태: ${factors.weatherCondition}`,
      severity: "info",
    });
  }

  if (factors.rescueStaffDeployed === false) {
    alerts.push({
      code: "RESCUE_STAFF",
      message: "구조 인력 미배치 상태",
      severity: "warning",
    });
  }

  if (factors.controlledArea) {
    alerts.push({
      code: "CONTROLLED_AREA",
      message: "통제 구역으로 지정됨",
      severity: "warning",
    });
  }

  if (
    factors.cctvAnalysisConfidence !== undefined &&
    factors.cctvAnalysisConfidence < 0.7
  ) {
    alerts.push({
      code: "CCTV_CONFIDENCE",
      message: `CCTV 분석 신뢰도 낮음 (${factors.cctvAnalysisConfidence})`,
      severity: "warning",
    });
  }

  const hasNonInfo = alerts.some((a) => a.severity !== "info");
  const densityBasedSafeButOtherRisks =
    densityRiskLevel === "안전" && hasNonInfo;

  if (densityBasedSafeButOtherRisks) {
    alerts.unshift({
      code: "SAFE_DENSITY_WITH_OTHER_RISKS",
      message: "밀도 기준 안전 + 추가 위험 요인 감지",
      severity: "warning",
    });
  }

  return { alerts, densityBasedSafeButOtherRisks };
}
