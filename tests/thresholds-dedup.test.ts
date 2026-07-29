import { describe, expect, it } from "vitest";
import {
  validateThresholdOrder,
  DEFAULT_THRESHOLDS,
  resolveZoneThresholds,
} from "../src/config/thresholds.js";
import { deduplicateCameraDetections } from "../src/density/deduplication.js";
import { evaluateAuxiliaryFactors } from "../src/factors/auxiliaryFactors.js";
import { computeEffectiveArea } from "../src/zone/zoneService.js";

describe("임계값 검증", () => {
  it("올바른 순서 허용", () => {
    expect(validateThresholdOrder(DEFAULT_THRESHOLDS).valid).toBe(true);
  });

  it("잘못된 순서는 거부", () => {
    const r = validateThresholdOrder({
      congestionStartDensity: 6,
      criticalDensity: 5,
      highRiskDensity: 4,
    });
    expect(r.valid).toBe(false);
    expect(r.message).toContain("congestionStartDensity < criticalDensity < highRiskDensity");
  });

  it("구역별 오버라이드 가능", () => {
    const { thresholds, source } = resolveZoneThresholds(DEFAULT_THRESHOLDS, {
      congestionStartDensity: 3.5,
      criticalDensity: 4.5,
      highRiskDensity: 5.5,
    });
    expect(source).toBe("zone_override");
    expect(thresholds.congestionStartDensity).toBe(3.5);
  });
});

describe("중복 감지 완화", () => {
  it("trackId 유니온으로 중복 제거", () => {
    const r = deduplicateCameraDetections(
      [
        {
          cameraId: "cam1",
          detectedPeople: 3,
          confidence: 0.9,
          trackedObjectIds: ["a", "b", "c"],
          measuredAt: "2026-07-17T10:00:00.000Z",
        },
        {
          cameraId: "cam2",
          detectedPeople: 3,
          confidence: 0.85,
          trackedObjectIds: ["b", "c", "d"],
          measuredAt: "2026-07-17T10:00:00.000Z",
        },
      ],
      [],
      "GWANGALLI-ZONE-CENTER",
    );
    expect(r.detectedPeople).toBe(4);
    expect(r.method).toBe("track_id_union");
  });

  it("경계 주 소속 구역만 카운트", () => {
    const r = deduplicateCameraDetections(
      [
        {
          cameraId: "cam1",
          detectedPeople: 2,
          confidence: 0.9,
          trackedObjectIds: ["x", "y"],
          measuredAt: "2026-07-17T10:00:00.000Z",
        },
      ],
      [
        {
          trackId: "x",
          zoneIds: ["ZONE-A", "GWANGALLI-ZONE-CENTER"],
          assignedZoneId: "ZONE-A",
        },
        {
          trackId: "y",
          zoneIds: ["ZONE-A", "GWANGALLI-ZONE-CENTER"],
          assignedZoneId: "GWANGALLI-ZONE-CENTER",
        },
      ],
      "GWANGALLI-ZONE-CENTER",
    );
    expect(r.detectedPeople).toBe(1);
  });

  it("trackId 없으면 max + 경고", () => {
    const r = deduplicateCameraDetections([
      {
        cameraId: "cam1",
        detectedPeople: 100,
        confidence: 0.8,
        measuredAt: "2026-07-17T10:00:00.000Z",
      },
      {
        cameraId: "cam2",
        detectedPeople: 120,
        confidence: 0.8,
        measuredAt: "2026-07-17T10:00:00.000Z",
      },
    ]);
    expect(r.detectedPeople).toBe(120);
    expect(r.warnings.length).toBeGreaterThan(0);
  });
});

describe("보조 위험 요인", () => {
  it("밀도 안전이어도 추가 위험 요인 동시 표시", () => {
    const r = evaluateAuxiliaryFactors(
      { opposingCrowdFlow: true, entranceCongestion: true },
      "안전",
    );
    expect(r.densityBasedSafeButOtherRisks).toBe(true);
    expect(r.alerts.some((a) => a.code === "SAFE_DENSITY_WITH_OTHER_RISKS")).toBe(
      true,
    );
  });
});

describe("유효 면적", () => {
  it("제외 면적 반영", () => {
    const r = computeEffectiveArea(1000, 200);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.effectiveAreaSquareMeters).toBe(800);
  });

  it("제외 후 0 이하면 오류", () => {
    const r = computeEffectiveArea(100, 100);
    expect(r.ok).toBe(false);
  });
});
