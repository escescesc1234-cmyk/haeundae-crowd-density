import { describe, expect, it, beforeEach } from "vitest";
import { CrowdDensityService } from "../src/service/crowdDensityService.js";
import { analyzeZoneDensity, createInitialZoneState } from "../src/density/engine.js";
import { loadZoneCatalog, getZoneById } from "../src/zone/zoneService.js";
import { DEFAULT_THRESHOLDS } from "../src/config/thresholds.js";

describe("엔진 통합 — 필수 밀도 시나리오", () => {
  let service: CrowdDensityService;

  beforeEach(() => {
    service = new CrowdDensityService();
  });

  function analyzeDensity(density: number, area = 100) {
    const people = Math.round(density * area);
    return service.analyzeManual(
      {
        zoneId: "GWANGALLI-ZONE-CENTER",
        detectedPeople: people,
        effectiveAreaSquareMeters: area,
        measuredAt: new Date().toISOString(),
        confidence: 0.95,
        isTestData: true,
      },
      { skipHysteresis: true },
    );
  }

  it("0명/㎡ → 안전", () => {
    const r = analyzeDensity(0);
    expect(r.rawDensity).toBe(0);
    expect(r.riskLevel).toBe("안전");
  });

  it("3.9 → 안전", () => {
    expect(analyzeDensity(3.9).riskLevel).toBe("안전");
  });

  it("4.0 → 혼잡", () => {
    const r = analyzeDensity(4.0);
    expect(r.riskLevel).toBe("혼잡");
    expect(r.criticalDensityReached).toBe(false);
  });

  it("4.9 → 혼잡", () => {
    expect(analyzeDensity(4.9).riskLevel).toBe("혼잡");
  });

  it("5.0 → 혼잡 + 임계 밀도 도달", () => {
    const r = analyzeDensity(5.0);
    expect(r.riskLevel).toBe("혼잡");
    expect(r.criticalDensityReached).toBe(true);
    expect(r.actionsTriggered).toContain("관리자 사전 경고 발송");
  });

  it("5.9 → 혼잡 + 위험 진입 가능성", () => {
    const r = analyzeDensity(5.9);
    expect(r.riskLevel).toBe("혼잡");
    expect(r.approachingHighRisk).toBe(true);
    expect(r.criticalDensityReached).toBe(true);
  });

  it("6.0 → 위험", () => {
    expect(analyzeDensity(6.0).riskLevel).toBe("위험");
  });

  it("고위험 초과", () => {
    expect(analyzeDensity(7.5).riskLevel).toBe("위험");
  });

  it("면적 0 → 오류", () => {
    const r = service.analyzeManual(
      {
        zoneId: "GWANGALLI-ZONE-CENTER",
        detectedPeople: 10,
        effectiveAreaSquareMeters: 0,
        isTestData: true,
      },
      { skipHysteresis: true },
    );
    expect(r.riskLevel).toBe("오류");
    expect(r.errors.length).toBeGreaterThan(0);
  });

  it("인원 누락 → 데이터없음", () => {
    const r = service.analyze(
      {
        zoneId: "GWANGALLI-ZONE-CENTER",
        detectedPeople: null,
        measuredAt: new Date().toISOString(),
        dataSource: "test",
        isTestData: true,
      },
      { skipHysteresis: true },
    );
    expect(r.riskLevel).toBe("데이터없음");
  });

  it("낮은 신뢰도 경고", () => {
    const r = service.analyzeManual(
      {
        zoneId: "GWANGALLI-ZONE-CENTER",
        detectedPeople: 100,
        effectiveAreaSquareMeters: 160,
        confidence: 0.4,
        isTestData: true,
      },
      { skipHysteresis: true },
    );
    expect(r.lowConfidence).toBe(true);
    expect(r.warnings.some((w) => w.includes("신뢰도"))).toBe(true);
  });

  it("출력 JSON에 관광객/관리자 요약 포함", () => {
    const r = analyzeDensity(5.2);
    expect(r.zoneId).toBe("GWANGALLI-ZONE-CENTER");
    expect(r.touristSummary.riskLevel).toBe("혼잡");
    expect(r.adminSummary.rawDensity).toBeCloseTo(5.2, 5);
    expect(r.thresholds.congestionStartDensity).toBe(4);
    expect(r.requiresManagerReview).toBe(true);
  });

  it("짧은 시간 급격 증가 → 즉시 위험(히스테리시스 적용)", () => {
    const zone = getZoneById("GWANGALLI-ZONE-CENTER");
    let runtime = createInitialZoneState(zone);
    const base = "2026-07-17T12:00:00.000Z";

    const first = analyzeZoneDensity(
      zone,
      {
        zoneId: zone.zoneId,
        detectedPeople: 100,
        effectiveAreaSquareMeters: 100,
        measuredAt: base,
        confidence: 0.95,
        dataSource: "test",
        isTestData: true,
      },
      runtime,
      { globalThresholds: DEFAULT_THRESHOLDS, skipHysteresis: false },
    );
    runtime = first.runtime;

    const second = analyzeZoneDensity(
      zone,
      {
        zoneId: zone.zoneId,
        detectedPeople: 450,
        effectiveAreaSquareMeters: 100,
        measuredAt: "2026-07-17T12:00:02.000Z",
        confidence: 0.95,
        dataSource: "test",
        isTestData: true,
        auxiliaryFactors: { densityIncreaseRatePerSecond: 0.2 },
      },
      runtime,
      { globalThresholds: DEFAULT_THRESHOLDS, skipHysteresis: false },
    );
    expect(second.result.riskLevel).toBe("위험");
    expect(second.result.actionsTriggered).toContain("즉시 위험 경고 발생");
  });
});

describe("구역 카탈로그", () => {
  it("중앙 구역 단일 카탈로그", () => {
    const catalog = loadZoneCatalog();
    expect(catalog.zones.length).toBe(1);
    expect(catalog.zones[0].zoneId).toBe("GWANGALLI-ZONE-CENTER");
    expect(catalog.zones[0].zoneName).toBe("중앙 구역");
    expect(catalog.zones.every((z) => z.effectiveAreaSquareMeters > 0)).toBe(
      true,
    );
  });
});
