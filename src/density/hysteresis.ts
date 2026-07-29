/**
 * 히스테리시스 + 최소 지속 시간으로 등급 플리커링 방지
 * 급격한 고위험/급상승 시에는 즉시 위험 승격
 */

import type { DensityThresholds, RiskLevel } from "../types/index.js";
import { parseIsoMs } from "./smoothing.js";

export type StableRiskLevel = Extract<RiskLevel, "안전" | "혼잡" | "위험">;

export interface HysteresisState {
  currentRiskLevel: StableRiskLevel;
  currentRiskSince: string;
  pendingRiskLevel?: StableRiskLevel;
  pendingRiskSince?: string;
}

export interface HysteresisInput {
  adjustedDensity: number;
  /** 즉시 경고 판정용(급상승·과다 초과). 없으면 adjustedDensity 사용 */
  rawDensity?: number;
  measuredAt: string;
  thresholds: DensityThresholds;
  state: HysteresisState;
  densityRisePerSecond?: number;
}

export interface HysteresisResult {
  riskLevel: StableRiskLevel;
  state: HysteresisState;
  immediateAlert: boolean;
  reasonParts: string[];
}

function candidateFromDensity(
  density: number,
  thresholds: DensityThresholds,
  forUpgrade: boolean,
): StableRiskLevel {
  const { congestionStartDensity, highRiskDensity, hysteresisMargin } =
    thresholds;

  if (forUpgrade) {
    if (density >= highRiskDensity) return "위험";
    if (density >= congestionStartDensity) return "혼잡";
    return "안전";
  }

  // 하락 시: 임계값에서 hysteresisMargin 만큼 더 내려가야 하락
  if (density > highRiskDensity - hysteresisMargin) return "위험";
  if (density > congestionStartDensity - hysteresisMargin) return "혼잡";
  return "안전";
}

function rank(level: StableRiskLevel): number {
  switch (level) {
    case "안전":
      return 0;
    case "혼잡":
      return 1;
    case "위험":
      return 2;
  }
}

export function applyHysteresis(input: HysteresisInput): HysteresisResult {
  const {
    adjustedDensity,
    rawDensity,
    measuredAt,
    thresholds,
    state,
    densityRisePerSecond = 0,
  } = input;
  const reasonParts: string[] = [];
  const spikeDensity = Math.max(adjustedDensity, rawDensity ?? adjustedDensity);

  const immediateHighRiskCut =
    thresholds.highRiskDensity * thresholds.immediateHighRiskMultiplier;
  const rapidRise =
    densityRisePerSecond >= thresholds.rapidRisePerSecond &&
    spikeDensity >= thresholds.congestionStartDensity;

  if (spikeDensity >= immediateHighRiskCut || rapidRise) {
    reasonParts.push(
      spikeDensity >= immediateHighRiskCut
        ? `밀도가 고위험 임계값의 ${thresholds.immediateHighRiskMultiplier}배(${immediateHighRiskCut}명/㎡) 이상이라 즉시 위험 경고`
        : `밀도 급상승(${densityRisePerSecond.toFixed(3)}명/㎡·초)으로 즉시 위험 경고`,
    );
    return {
      riskLevel: "위험",
      immediateAlert: true,
      reasonParts,
      state: {
        currentRiskLevel: "위험",
        currentRiskSince: measuredAt,
        pendingRiskLevel: undefined,
        pendingRiskSince: undefined,
      },
    };
  }

  const upgradeCandidate = candidateFromDensity(
    adjustedDensity,
    thresholds,
    true,
  );
  const downgradeCandidate = candidateFromDensity(
    adjustedDensity,
    thresholds,
    false,
  );

  const current = state.currentRiskLevel;
  let target: StableRiskLevel = current;

  if (rank(upgradeCandidate) > rank(current)) {
    target = upgradeCandidate;
  } else if (rank(downgradeCandidate) < rank(current)) {
    target = downgradeCandidate;
  } else {
    target = current;
  }

  if (target === current) {
    return {
      riskLevel: current,
      immediateAlert: false,
      reasonParts: [`현재 등급(${current}) 유지`],
      state: {
        currentRiskLevel: current,
        currentRiskSince: state.currentRiskSince,
        pendingRiskLevel: undefined,
        pendingRiskSince: undefined,
      },
    };
  }

  const isUpgrade = rank(target) > rank(current);
  const requiredSeconds = isUpgrade
    ? thresholds.minDurationSecondsForUpgrade
    : thresholds.minDurationSecondsForDowngrade;

  const pendingLevel = state.pendingRiskLevel;
  const pendingSince = state.pendingRiskSince;

  if (pendingLevel !== target || !pendingSince) {
    reasonParts.push(
      `${current} → ${target} 전환 대기 시작 (최소 ${requiredSeconds}초 지속 필요)`,
    );
    return {
      riskLevel: current,
      immediateAlert: false,
      reasonParts,
      state: {
        currentRiskLevel: current,
        currentRiskSince: state.currentRiskSince,
        pendingRiskLevel: target,
        pendingRiskSince: measuredAt,
      },
    };
  }

  const elapsed =
    (parseIsoMs(measuredAt) - parseIsoMs(pendingSince)) / 1000;

  if (elapsed < requiredSeconds) {
    reasonParts.push(
      `${current} → ${target} 전환 대기 중 (${elapsed.toFixed(1)}/${requiredSeconds}초)`,
    );
    return {
      riskLevel: current,
      immediateAlert: false,
      reasonParts,
      state: {
        currentRiskLevel: current,
        currentRiskSince: state.currentRiskSince,
        pendingRiskLevel: target,
        pendingRiskSince: pendingSince,
      },
    };
  }

  reasonParts.push(
    `${requiredSeconds}초 이상 지속되어 ${current} → ${target}로 등급 변경`,
  );
  return {
    riskLevel: target,
    immediateAlert: false,
    reasonParts,
    state: {
      currentRiskLevel: target,
      currentRiskSince: measuredAt,
      pendingRiskLevel: undefined,
      pendingRiskSince: undefined,
    },
  };
}

/**
 * 히스테리시스 없이 밀도만으로 기본 3단계 판정 (테스트·설명용)
 */
export function classifyByDensityOnly(
  adjustedDensity: number,
  thresholds: Pick<
    DensityThresholds,
    "congestionStartDensity" | "highRiskDensity"
  >,
): StableRiskLevel {
  if (adjustedDensity >= thresholds.highRiskDensity) return "위험";
  if (adjustedDensity >= thresholds.congestionStartDensity) return "혼잡";
  return "안전";
}
