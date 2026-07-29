/**
 * 밀도 분석 엔진 — 관광객/관리자 UI가 공통으로 호출하는 핵심 모듈
 */

import {
  loadThresholds,
  resolveZoneThresholds,
} from "../config/thresholds.js";
import { calculateRawDensity } from "./calculator.js";
import { deduplicateCameraDetections } from "./deduplication.js";
import { applyHysteresis, type HysteresisState } from "./hysteresis.js";
import {
  assessCriticalDensity,
  buildRiskReason,
  touristLabels,
} from "./riskClassifier.js";
import { computeAdjustedDensity, computeTrend, parseIsoMs } from "./smoothing.js";
import { evaluateAuxiliaryFactors } from "../factors/auxiliaryFactors.js";
import type {
  DensityAnalysisResult,
  DensityInput,
  DensitySample,
  DensityThresholds,
  RiskLevel,
  ZoneDefinition,
  ZoneRuntimeState,
} from "../types/index.js";

const DISCLAIMER =
  "본 등급은 사고 예측 결과가 아니라 현장 판단을 돕는 참고 정보입니다. 실제 운영 전 현장 실험과 안전 전문가 검토가 필요합니다.";

export function createInitialZoneState(zone: ZoneDefinition): ZoneRuntimeState {
  return {
    zoneId: zone.zoneId,
    zoneName: zone.zoneName,
    zoneType: zone.zoneType,
    totalAreaSquareMeters: zone.totalAreaSquareMeters,
    effectiveAreaSquareMeters: zone.effectiveAreaSquareMeters,
    currentPeople: null,
    rawDensity: null,
    adjustedDensity: null,
    riskLevel: "데이터없음",
    measuredAt: null,
    dataSource: null,
    confidence: null,
    managerConfirmStatus: "unconfirmed",
    densityHistory: [],
  };
}

function resolvePeopleCount(
  input: DensityInput,
  warnings: string[],
): { people: number | null | undefined; confidence: number } {
  if (input.cameraDetections && input.cameraDetections.length > 0) {
    const dedup = deduplicateCameraDetections(
      input.cameraDetections,
      input.boundaryDetections ?? [],
      input.zoneId,
    );
    warnings.push(...dedup.warnings);
    return {
      people: dedup.detectedPeople,
      confidence: input.confidence ?? dedup.confidence,
    };
  }
  return {
    people: input.detectedPeople,
    confidence: input.confidence ?? 1,
  };
}

function checkStale(
  measuredAt: string,
  nowIso: string,
  staleSeconds: number,
  warnings: string[],
): void {
  const ageSec = (parseIsoMs(nowIso) - parseIsoMs(measuredAt)) / 1000;
  if (ageSec > staleSeconds) {
    warnings.push(
      `CCTV/측정 데이터가 ${Math.floor(ageSec)}초 동안 갱신되지 않았습니다 (기준 ${staleSeconds}초).`,
    );
  }
}

export function analyzeZoneDensity(
  zone: ZoneDefinition,
  input: DensityInput,
  runtime: ZoneRuntimeState,
  options?: {
    globalThresholds?: DensityThresholds;
    nowIso?: string;
    skipHysteresis?: boolean;
  },
): { result: DensityAnalysisResult; runtime: ZoneRuntimeState } {
  const errors: string[] = [];
  const warnings: string[] = [];
  const globalThresholds = options?.globalThresholds ?? loadThresholds();
  const { thresholds, source } = resolveZoneThresholds(
    globalThresholds,
    zone.thresholdOverrides,
  );

  const effectiveArea =
    input.effectiveAreaSquareMeters ?? zone.effectiveAreaSquareMeters;

  const { people, confidence } = resolvePeopleCount(input, warnings);
  const nowIso = options?.nowIso ?? new Date().toISOString();
  checkStale(input.measuredAt, nowIso, thresholds.staleDataSeconds, warnings);

  const lowConfidence = confidence < thresholds.minimumConfidence;
  if (lowConfidence) {
    warnings.push(
      `객체 감지 신뢰도(${confidence})가 최소 기준(${thresholds.minimumConfidence})보다 낮습니다.`,
    );
  }

  const calc = calculateRawDensity(people, effectiveArea);
  const dataSource = input.dataSource ?? "manual";
  const isTestData = input.isTestData ?? dataSource === "test";

  const thresholdsUsed = {
    congestionStartDensity: thresholds.congestionStartDensity,
    criticalDensity: thresholds.criticalDensity,
    highRiskDensity: thresholds.highRiskDensity,
    hysteresisMargin: thresholds.hysteresisMargin,
    source,
  };

  if (!calc.ok) {
    const riskLevel: RiskLevel =
      calc.code === "MISSING_PEOPLE" ? "데이터없음" : "오류";
    if (calc.code !== "MISSING_PEOPLE") {
      errors.push(calc.message);
    } else {
      warnings.push(calc.message);
    }

    const labels = touristLabels(riskLevel);
    const result: DensityAnalysisResult = {
      zoneId: zone.zoneId,
      zoneName: zone.zoneName,
      zoneType: zone.zoneType,
      effectiveAreaSquareMeters: effectiveArea || 0,
      detectedPeople: people ?? null,
      rawDensity: null,
      adjustedDensity: null,
      riskLevel,
      criticalDensityReached: false,
      approachingHighRisk: false,
      trend: "알수없음",
      thresholds: thresholdsUsed,
      reason: calc.message,
      confidence,
      lowConfidence,
      requiresManagerReview: true,
      measuredAt: input.measuredAt,
      dataSource,
      isTestData,
      recommendedActions: [labels.recommendedAction],
      touristSummary: {
        zoneId: zone.zoneId,
        zoneName: zone.zoneName,
        riskLevel,
        ...labels,
        updatedAt: input.measuredAt,
        disclaimer: DISCLAIMER,
      },
      adminSummary: {
        zoneId: zone.zoneId,
        zoneName: zone.zoneName,
        effectiveAreaSquareMeters: effectiveArea || 0,
        detectedPeople: people ?? null,
        rawDensity: null,
        adjustedDensity: null,
        riskLevel,
        thresholds: thresholdsUsed,
        criticalDensityReached: false,
        approachingHighRisk: false,
        trend: "알수없음",
        reason: calc.message,
        confidence,
        requiresManagerReview: true,
        densityHistory: runtime.densityHistory,
        auxiliaryAlerts: [],
        actionsTriggered: [],
        errors,
        warnings,
        canManualOverride: true,
        canSendAlert: true,
        canRecordFalsePositive: true,
      },
      auxiliaryAlerts: [],
      densityBasedSafeButOtherRisks: false,
      errors,
      warnings,
      actionsTriggered: [],
    };

    return {
      result,
      runtime: {
        ...runtime,
        currentPeople: people ?? null,
        rawDensity: null,
        adjustedDensity: null,
        riskLevel,
        measuredAt: input.measuredAt,
        dataSource,
        confidence,
        managerConfirmStatus: "requires_review",
      },
    };
  }

  const rawDensity = calc.rawDensity;
  const historyForSmoothing = [
    ...runtime.densityHistory,
    ...(input.previousDensities ?? []).map((d, i) => ({
      measuredAt: new Date(
        parseIsoMs(input.measuredAt) - (i + 1) * 1000,
      ).toISOString(),
      rawDensity: d,
      adjustedDensity: d,
      detectedPeople: calc.detectedPeople,
      confidence,
    })),
  ];

  const adjustedDensity = computeAdjustedDensity(
    rawDensity,
    historyForSmoothing,
    input.measuredAt,
    thresholds.measurementWindowSeconds,
  );

  const { trend, ratePerSecond } = computeTrend(
    runtime.densityHistory,
    adjustedDensity,
    input.measuredAt,
    thresholds.measurementWindowSeconds,
  );

  if (
    runtime.adjustedDensity !== null &&
    adjustedDensity - runtime.adjustedDensity >=
      thresholds.highRiskDensity * 0.5
  ) {
    warnings.push(
      `밀도가 비정상적으로 급상승했습니다 (${runtime.adjustedDensity} → ${adjustedDensity}).`,
    );
  }

  let riskLevel: RiskLevel;
  let hysteresisNotes: string[] = [];
  let nextHystState: HysteresisState;
  let immediateAlert = false;

  const previousStable =
    runtime.riskLevel === "안전" ||
    runtime.riskLevel === "혼잡" ||
    runtime.riskLevel === "위험"
      ? runtime.riskLevel
      : "안전";

  if (options?.skipHysteresis) {
    const simple =
      adjustedDensity >= thresholds.highRiskDensity
        ? "위험"
        : adjustedDensity >= thresholds.congestionStartDensity
          ? "혼잡"
          : "안전";
    riskLevel = simple;
    nextHystState = {
      currentRiskLevel: simple,
      currentRiskSince: input.measuredAt,
    };
    hysteresisNotes = ["히스테리시스 미적용(즉시 판정 모드)"];
  } else {
    const hyst = applyHysteresis({
      adjustedDensity,
      rawDensity,
      measuredAt: input.measuredAt,
      thresholds,
      densityRisePerSecond: Math.max(
        ratePerSecond,
        input.auxiliaryFactors?.densityIncreaseRatePerSecond ?? 0,
      ),
      state: {
        currentRiskLevel: previousStable,
        currentRiskSince: runtime.currentRiskSince ?? input.measuredAt,
        pendingRiskLevel:
          runtime.pendingRiskLevel === "안전" ||
          runtime.pendingRiskLevel === "혼잡" ||
          runtime.pendingRiskLevel === "위험"
            ? runtime.pendingRiskLevel
            : undefined,
        pendingRiskSince: runtime.pendingRiskSince,
      },
    });
    riskLevel = hyst.riskLevel;
    hysteresisNotes = hyst.reasonParts;
    nextHystState = hyst.state;
    immediateAlert = hyst.immediateAlert;
  }

  const critical = assessCriticalDensity(
    adjustedDensity,
    riskLevel as "안전" | "혼잡" | "위험",
    thresholds,
  );

  const actionsTriggered = [...critical.actionsTriggered];
  if (immediateAlert) {
    actionsTriggered.push("즉시 위험 경고 발생");
  }

  const reason = buildRiskReason(
    adjustedDensity,
    riskLevel as "안전" | "혼잡" | "위험",
    thresholds,
    critical.reasonExtra,
    hysteresisNotes,
  );

  const aux = evaluateAuxiliaryFactors(
    {
      ...input.auxiliaryFactors,
      densityIncreaseRatePerSecond:
        input.auxiliaryFactors?.densityIncreaseRatePerSecond ?? ratePerSecond,
      cctvAnalysisConfidence:
        input.auxiliaryFactors?.cctvAnalysisConfidence ?? confidence,
    },
    riskLevel,
  );

  const requiresManagerReview =
    lowConfidence ||
    critical.criticalDensityReached ||
    riskLevel === "위험" ||
    aux.densityBasedSafeButOtherRisks ||
    errors.length > 0 ||
    Boolean(input.managerConfirmed) === false && riskLevel !== "안전";

  const sample: DensitySample = {
    measuredAt: input.measuredAt,
    rawDensity,
    adjustedDensity,
    detectedPeople: calc.detectedPeople,
    confidence,
  };

  const densityHistory = [...runtime.densityHistory, sample].slice(-200);
  const labels = touristLabels(riskLevel);

  const result: DensityAnalysisResult = {
    zoneId: zone.zoneId,
    zoneName: zone.zoneName,
    zoneType: zone.zoneType,
    effectiveAreaSquareMeters: calc.effectiveAreaSquareMeters,
    detectedPeople: calc.detectedPeople,
    rawDensity,
    adjustedDensity,
    riskLevel,
    criticalDensityReached: critical.criticalDensityReached,
    approachingHighRisk: critical.approachingHighRisk,
    trend,
    thresholds: thresholdsUsed,
    reason,
    confidence,
    lowConfidence,
    requiresManagerReview,
    measuredAt: input.measuredAt,
    dataSource,
    isTestData,
    recommendedActions: [labels.recommendedAction],
    touristSummary: {
      zoneId: zone.zoneId,
      zoneName: zone.zoneName,
      riskLevel,
      ...labels,
      updatedAt: input.measuredAt,
      disclaimer: DISCLAIMER,
    },
    adminSummary: {
      zoneId: zone.zoneId,
      zoneName: zone.zoneName,
      effectiveAreaSquareMeters: calc.effectiveAreaSquareMeters,
      detectedPeople: calc.detectedPeople,
      rawDensity,
      adjustedDensity,
      riskLevel,
      thresholds: thresholdsUsed,
      criticalDensityReached: critical.criticalDensityReached,
      approachingHighRisk: critical.approachingHighRisk,
      trend,
      reason,
      confidence,
      requiresManagerReview,
      densityHistory,
      auxiliaryAlerts: aux.alerts,
      actionsTriggered,
      errors,
      warnings,
      canManualOverride: true,
      canSendAlert: true,
      canRecordFalsePositive: true,
    },
    auxiliaryAlerts: aux.alerts,
    densityBasedSafeButOtherRisks: aux.densityBasedSafeButOtherRisks,
    errors,
    warnings,
    actionsTriggered,
  };

  const nextRuntime: ZoneRuntimeState = {
    ...runtime,
    currentPeople: calc.detectedPeople,
    rawDensity,
    adjustedDensity,
    riskLevel,
    measuredAt: input.measuredAt,
    dataSource,
    confidence,
    managerConfirmStatus: requiresManagerReview
      ? "requires_review"
      : "unconfirmed",
    densityHistory,
    currentRiskSince: nextHystState.currentRiskSince,
    pendingRiskLevel: nextHystState.pendingRiskLevel,
    pendingRiskSince: nextHystState.pendingRiskSince,
  };

  return { result, runtime: nextRuntime };
}
