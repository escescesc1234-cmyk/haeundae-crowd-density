/**
 * 측정 윈도우 내 이동평균·중앙값으로 보정 밀도 산출
 */

import { roundDensity } from "./calculator.js";
import type { DensitySample } from "../types/index.js";

export function parseIsoMs(iso: string): number {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) {
    throw new Error(`잘못된 시각 형식: ${iso}`);
  }
  return ms;
}

export function filterSamplesInWindow(
  samples: DensitySample[],
  measuredAt: string,
  windowSeconds: number,
): DensitySample[] {
  const end = parseIsoMs(measuredAt);
  const start = end - windowSeconds * 1000;
  return samples
    .filter((s) => {
      const t = parseIsoMs(s.measuredAt);
      return t >= start && t <= end;
    })
    .sort((a, b) => parseIsoMs(a.measuredAt) - parseIsoMs(b.measuredAt));
}

export function movingAverage(values: number[]): number {
  if (values.length === 0) return 0;
  const sum = values.reduce((a, b) => a + b, 0);
  return sum / values.length;
}

export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[mid - 1] + sorted[mid]) / 2;
  }
  return sorted[mid];
}

/**
 * 원본 밀도와 최근 윈도우 샘플을 결합해 보정 밀도를 만든다.
 * 샘플이 충분하면 (이동평균 + 중앙값) / 2, 부족하면 원본 밀도를 사용한다.
 */
export function computeAdjustedDensity(
  rawDensity: number,
  history: DensitySample[],
  measuredAt: string,
  windowSeconds: number,
): number {
  const windowSamples = filterSamplesInWindow(
    history,
    measuredAt,
    windowSeconds,
  );
  const densities = [
    ...windowSamples.map((s) => s.rawDensity),
    rawDensity,
  ];

  if (densities.length < 2) {
    return roundDensity(rawDensity);
  }

  const avg = movingAverage(densities);
  const med = median(densities);
  return roundDensity((avg + med) / 2);
}

export function computeTrend(
  history: DensitySample[],
  currentAdjusted: number,
  measuredAt: string,
  windowSeconds: number,
): { trend: "증가" | "감소" | "유지" | "알수없음"; ratePerSecond: number } {
  const windowSamples = filterSamplesInWindow(
    history,
    measuredAt,
    windowSeconds,
  );
  if (windowSamples.length < 1) {
    return { trend: "알수없음", ratePerSecond: 0 };
  }

  const oldest = windowSamples[0];
  const elapsedSec = Math.max(
    1,
    (parseIsoMs(measuredAt) - parseIsoMs(oldest.measuredAt)) / 1000,
  );
  const delta = currentAdjusted - oldest.adjustedDensity;
  const ratePerSecond = delta / elapsedSec;

  if (Math.abs(delta) < 0.05) {
    return { trend: "유지", ratePerSecond };
  }
  return {
    trend: delta > 0 ? "증가" : "감소",
    ratePerSecond,
  };
}
