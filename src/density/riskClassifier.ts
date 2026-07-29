/**
 * 위험 등급 판정 및 임계 밀도 도달 처리
 */

import type { DensityThresholds, DensityTrend } from "../types/index.js";
import {
  classifyByDensityOnly,
  type StableRiskLevel,
} from "./hysteresis.js";

export type { StableRiskLevel } from "./hysteresis.js";

export interface RiskAssessment {
  riskLevel: StableRiskLevel;
  criticalDensityReached: boolean;
  approachingHighRisk: boolean;
  reason: string;
  actionsTriggered: string[];
}

export function assessCriticalDensity(
  adjustedDensity: number,
  riskLevel: StableRiskLevel,
  thresholds: DensityThresholds,
): {
  criticalDensityReached: boolean;
  approachingHighRisk: boolean;
  actionsTriggered: string[];
  reasonExtra: string[];
} {
  const actionsTriggered: string[] = [];
  const reasonExtra: string[] = [];

  const criticalDensityReached =
    adjustedDensity >= thresholds.criticalDensity &&
    adjustedDensity < thresholds.highRiskDensity;

  const approachingHighRisk =
    riskLevel === "혼잡" &&
    adjustedDensity >= thresholds.criticalDensity &&
    adjustedDensity < thresholds.highRiskDensity;

  if (criticalDensityReached) {
    reasonExtra.push(
      "보정 밀도가 임계 밀도에 도달했지만 고위험 임계값 미만임",
    );
    actionsTriggered.push("임계 밀도 도달 상태 표시");
    actionsTriggered.push("관리자 사전 경고 발송");
    actionsTriggered.push("데이터 갱신 주기 단축");
    actionsTriggered.push("CCTV 또는 현장 확인 요청");
    actionsTriggered.push("위험 단계 진입 가능성 표시");
    actionsTriggered.push("밀도 증가 추세 분석 실행");
  }

  return {
    criticalDensityReached,
    approachingHighRisk,
    actionsTriggered,
    reasonExtra,
  };
}

export function buildRiskReason(
  adjustedDensity: number,
  riskLevel: StableRiskLevel,
  thresholds: DensityThresholds,
  extras: string[],
  hysteresisNotes: string[],
): string {
  const base =
    riskLevel === "안전"
      ? `보정 밀도(${adjustedDensity}명/㎡)가 혼잡 시작값(${thresholds.congestionStartDensity})보다 낮음`
      : riskLevel === "혼잡"
        ? `보정 밀도(${adjustedDensity}명/㎡)가 혼잡 시작값(${thresholds.congestionStartDensity}) 이상이며 고위험 임계값(${thresholds.highRiskDensity}) 미만임`
        : `보정 밀도(${adjustedDensity}명/㎡)가 고위험 임계값(${thresholds.highRiskDensity}) 이상임`;

  return [base, ...extras, ...hysteresisNotes].filter(Boolean).join(". ");
}

export function buildImmediateRiskAssessment(
  adjustedDensity: number,
  thresholds: DensityThresholds,
): RiskAssessment {
  const riskLevel = classifyByDensityOnly(adjustedDensity, thresholds);
  const critical = assessCriticalDensity(
    adjustedDensity,
    riskLevel,
    thresholds,
  );
  return {
    riskLevel,
    criticalDensityReached: critical.criticalDensityReached,
    approachingHighRisk: critical.approachingHighRisk,
    actionsTriggered: critical.actionsTriggered,
    reason: buildRiskReason(
      adjustedDensity,
      riskLevel,
      thresholds,
      critical.reasonExtra,
      [],
    ),
  };
}

export function touristLabels(riskLevel: StableRiskLevel | "오류" | "데이터없음"): {
  riskLabel: string;
  congestionHint: string;
  recommendedAction: string;
  safeDirectionHint: string;
  colorCode: string;
  icon: string;
} {
  switch (riskLevel) {
    case "안전":
      return {
        riskLabel: "안전",
        congestionHint: "정상 이용 가능",
        recommendedAction: "평소와 같이 이용하되, 안내 방송에 주의하세요.",
        safeDirectionHint: "현재 구역 내 이동 가능",
        colorCode: "#2e7d32",
        icon: "✓",
      };
    case "혼잡":
      return {
        riskLabel: "혼잡",
        congestionHint: "혼잡 구역 접근 자제 또는 이동 주의",
        recommendedAction: "혼잡 구역 접근을 자제하고 여유 공간으로 이동하세요.",
        safeDirectionHint: "출입구·백사장 외곽 방향 권장",
        colorCode: "#f9a825",
        icon: "!",
      };
    case "위험":
      return {
        riskLabel: "위험",
        congestionHint: "고위험 — 즉시 이탈",
        recommendedAction:
          "즉시 해당 구역에서 벗어나고 관리자의 안내에 따르세요.",
        safeDirectionHint: "가장 가까운 안전 유도 경로로 이동",
        colorCode: "#c62828",
        icon: "⚠",
      };
    case "데이터없음":
      return {
        riskLabel: "정보 없음",
        congestionHint: "현재 밀도 정보를 확인할 수 없음",
        recommendedAction: "안내 요원 또는 안내판을 확인하세요.",
        safeDirectionHint: "관리자 안내에 따르세요",
        colorCode: "#757575",
        icon: "?",
      };
    default:
      return {
        riskLabel: "점검 중",
        congestionHint: "데이터 오류로 표시 불가",
        recommendedAction: "현장 안내를 우선하세요.",
        safeDirectionHint: "관리자 안내에 따르세요",
        colorCode: "#616161",
        icon: "…",
      };
  }
}

export function trendMessage(trend: DensityTrend): string {
  switch (trend) {
    case "증가":
      return "밀도 증가 추세";
    case "감소":
      return "밀도 감소 추세";
    case "유지":
      return "밀도 유지";
    default:
      return "추세 판단 불가";
  }
}
