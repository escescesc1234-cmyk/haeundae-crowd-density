/**
 * 광안리 전체 판정: 구역을 나눠 안내하지 않고,
 * 한 지점(한쪽)이라도 혼잡·위험이면 해수욕장 전체를 그 등급으로 본다.
 */

import type { DensityAnalysisResult, RiskLevel, TouristViewModel } from "../types/index.js";
import { touristLabels } from "./riskClassifier.js";

const RISK_RANK: Record<string, number> = {
  위험: 0,
  혼잡: 1,
  안전: 2,
  데이터없음: 3,
  오류: 4,
};

export function riskRank(level: string): number {
  return RISK_RANK[level] ?? 99;
}

export function pickWorstRisk(levels: string[]): RiskLevel {
  const ranked = [...levels].sort((a, b) => riskRank(a) - riskRank(b));
  const worst = ranked[0];
  if (worst === "위험" || worst === "혼잡" || worst === "안전") return worst;
  if (worst === "데이터없음") return "데이터없음";
  return "데이터없음";
}

export interface BeachWideJudgment {
  overallRiskLevel: RiskLevel;
  hotspotZoneId?: string;
  hotspotZoneName?: string;
  measuredZoneCount: number;
  policy: "beach_wide_worst_case";
  explanation: string;
}

/** 실측이 있는 구역 중 최악 등급을 광안리 전체 판정으로 사용 */
export function judgeBeachWide(results: DensityAnalysisResult[]): BeachWideJudgment {
  const measured = results.filter(
    (r) =>
      r.riskLevel !== "데이터없음" &&
      r.riskLevel !== "오류" &&
      r.detectedPeople != null,
  );
  if (!measured.length) {
    return {
      overallRiskLevel: "데이터없음",
      measuredZoneCount: 0,
      policy: "beach_wide_worst_case",
      explanation:
        "실측 데이터가 없어 광안리 전체 밀집도를 판정할 수 없습니다.",
    };
  }
  const worst = [...measured].sort(
    (a, b) => riskRank(a.riskLevel) - riskRank(b.riskLevel),
  )[0];
  const overall = pickWorstRisk(measured.map((r) => r.riskLevel));
  return {
    overallRiskLevel: overall,
    hotspotZoneId: worst.zoneId,
    hotspotZoneName: worst.zoneName,
    measuredZoneCount: measured.length,
    policy: "beach_wide_worst_case",
    explanation:
      overall === "안전"
        ? "관측된 지점 모두 안전 수준입니다. 광안리 해수욕장 전체를 안전으로 안내합니다."
        : `${worst.zoneName} 등에서 ${overall}이(가) 감지되어, 구역을 나누지 않고 광안리 해수욕장 전체를 ${overall}(으)로 안내합니다.`,
  };
}

/** 관광객 화면용: 모든 구역 카드를 전체 판정 등급으로 통일 */
export function applyBeachWideToTouristViews(
  views: TouristViewModel[],
  judgment: BeachWideJudgment,
): TouristViewModel[] {
  const labels = touristLabels(judgment.overallRiskLevel);
  const hotspotNote = judgment.hotspotZoneName
    ? ` (감지 지점: ${judgment.hotspotZoneName})`
    : "";
  return views.map((v) => ({
    ...v,
    riskLevel: judgment.overallRiskLevel,
    riskLabel: labels.riskLabel,
    congestionHint:
      judgment.overallRiskLevel === "안전" || judgment.overallRiskLevel === "데이터없음"
        ? labels.congestionHint
        : `광안리 전체 ${labels.congestionHint}${hotspotNote}`,
    recommendedAction: labels.recommendedAction,
    safeDirectionHint:
      judgment.overallRiskLevel === "위험"
        ? "해수욕장 외곽·지정 대피 경로로 이동"
        : judgment.overallRiskLevel === "혼잡"
          ? "중심이 붐비면 광안리 전체를 혼잡으로 안내합니다. 방문 시간을 조정하거나 외곽으로 이동하세요."
          : "광안리 해수욕장 내 이동 가능",
    colorCode: labels.colorCode,
    icon: labels.icon,
  }));
}
