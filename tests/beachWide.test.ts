import { describe, expect, it } from "vitest";
import {
  applyBeachWideToTouristViews,
  judgeBeachWide,
  pickWorstRisk,
} from "../src/density/beachWide.js";
import type { DensityAnalysisResult, TouristViewModel } from "../src/types/index.js";

function stubResult(
  zoneId: string,
  zoneName: string,
  riskLevel: DensityAnalysisResult["riskLevel"],
  people: number | null,
): DensityAnalysisResult {
  return {
    zoneId,
    zoneName,
    zoneType: "sand_beach",
    effectiveAreaSquareMeters: 100,
    detectedPeople: people,
    rawDensity: people,
    adjustedDensity: people,
    riskLevel,
    criticalDensityReached: false,
    approachingHighRisk: false,
    trend: "알수없음",
    thresholds: {
      congestionStartDensity: 4,
      criticalDensity: 5,
      highRiskDensity: 6,
      hysteresisMargin: 0.2,
      source: "global",
    },
    reason: "test",
    confidence: 1,
    lowConfidence: false,
    requiresManagerReview: false,
    measuredAt: new Date().toISOString(),
    dataSource: "manual",
    isTestData: true,
    recommendedActions: [],
    touristSummary: {
      zoneId,
      zoneName,
      riskLevel,
      riskLabel: riskLevel,
      congestionHint: "hint",
      recommendedAction: "action",
      safeDirectionHint: "dir",
      colorCode: "#000",
      icon: "-",
      updatedAt: new Date().toISOString(),
      disclaimer: "d",
    },
    adminSummary: {} as DensityAnalysisResult["adminSummary"],
    auxiliaryAlerts: [],
    densityBasedSafeButOtherRisks: false,
    errors: [],
    warnings: [],
    actionsTriggered: [],
  };
}

describe("beach-wide worst-case judgment", () => {
  it("한쪽 혼잡이면 전체를 혼잡으로 판정", () => {
    const judgment = judgeBeachWide([
      stubResult("Z1", "서쪽", "안전", 10),
      stubResult("Z2", "동쪽", "혼잡", 80),
      stubResult("Z3", "중앙", "데이터없음", null),
    ]);
    expect(judgment.overallRiskLevel).toBe("혼잡");
    expect(judgment.hotspotZoneName).toBe("동쪽");
    expect(judgment.policy).toBe("beach_wide_worst_case");
  });

  it("관광객 뷰의 모든 구역 등급을 전체 판정으로 통일", () => {
    const results = [
      stubResult("Z1", "서쪽", "안전", 10),
      stubResult("Z2", "동쪽", "위험", 120),
    ];
    const judgment = judgeBeachWide(results);
    const views = applyBeachWideToTouristViews(
      results.map((r) => r.touristSummary),
      judgment,
    );
    expect(views.every((v: TouristViewModel) => v.riskLevel === "위험")).toBe(true);
    expect(views[0].congestionHint).toContain("광안리 전체");
  });

  it("pickWorstRisk는 위험을 최악으로 선택", () => {
    expect(pickWorstRisk(["안전", "혼잡", "위험"])).toBe("위험");
  });
});
