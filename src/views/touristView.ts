import type { DensityAnalysisResult, TouristViewModel } from "../types/index.js";
import {
  applyBeachWideToTouristViews,
  judgeBeachWide,
  type BeachWideJudgment,
} from "../density/beachWide.js";

export function toTouristPayload(
  result: DensityAnalysisResult,
): TouristViewModel {
  return result.touristSummary;
}

/**
 * 관광객 API: 구역별 개별 안내 대신
 * 「한쪽이라도 혼잡하면 광안리 전체 혼잡」 정책으로 통일한다.
 * 관리자 API(/api/admin/zones)는 구역별 원본을 유지한다.
 */
export function toTouristList(
  results: DensityAnalysisResult[],
): TouristViewModel[] {
  const judgment = judgeBeachWide(results);
  const views = results.map(toTouristPayload);
  return applyBeachWideToTouristViews(views, judgment);
}

export function getBeachWideJudgment(
  results: DensityAnalysisResult[],
): BeachWideJudgment {
  return judgeBeachWide(results);
}
