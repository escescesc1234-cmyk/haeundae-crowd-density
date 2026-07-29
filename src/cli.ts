/**
 * CLI: 테스트 측정 데이터로 구역별 밀도·등급 일괄 분석
 * 사용: npm run analyze -- data/sample-measurements.json
 */

import { readFileSync } from "node:fs";
import { CrowdDensityService } from "./service/crowdDensityService.js";

interface SampleFile {
  skipHysteresis?: boolean;
  measurements: Array<{
    zoneId: string;
    detectedPeople: number | null;
    effectiveAreaSquareMeters?: number;
    measuredAt?: string;
    confidence?: number;
    dataSource?: "manual" | "test";
  }>;
}

function main() {
  const path = process.argv[2] ?? "data/sample-measurements.json";
  const file = JSON.parse(readFileSync(path, "utf-8")) as SampleFile;
  const service = new CrowdDensityService();

  const results = file.measurements.map((m) => {
    if (m.detectedPeople === null) {
      return service.analyze(
        {
          zoneId: m.zoneId,
          detectedPeople: null,
          measuredAt: m.measuredAt ?? new Date().toISOString(),
          confidence: m.confidence,
          dataSource: m.dataSource ?? "test",
          isTestData: true,
          effectiveAreaSquareMeters: m.effectiveAreaSquareMeters,
        },
        { skipHysteresis: file.skipHysteresis ?? true },
      );
    }
    return service.analyzeManual(
      {
        zoneId: m.zoneId,
        detectedPeople: m.detectedPeople,
        effectiveAreaSquareMeters: m.effectiveAreaSquareMeters,
        measuredAt: m.measuredAt,
        confidence: m.confidence,
        isTestData: true,
      },
      { skipHysteresis: file.skipHysteresis ?? true },
    );
  });

  const compact = results.map((r) => ({
    zoneId: r.zoneId,
    zoneName: r.zoneName,
    effectiveAreaSquareMeters: r.effectiveAreaSquareMeters,
    detectedPeople: r.detectedPeople,
    rawDensity: r.rawDensity,
    adjustedDensity: r.adjustedDensity,
    riskLevel: r.riskLevel,
    criticalDensityReached: r.criticalDensityReached,
    trend: r.trend,
    thresholds: r.thresholds,
    reason: r.reason,
    confidence: r.confidence,
    requiresManagerReview: r.requiresManagerReview,
    measuredAt: r.measuredAt,
    errors: r.errors,
    warnings: r.warnings,
  }));

  console.log(JSON.stringify(compact, null, 2));
}

main();
