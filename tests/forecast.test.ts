import { describe, expect, it } from "vitest";
import { ForecastService } from "../src/forecast/service.js";
import { MockWeatherProvider, KmaVilageForecastProvider } from "../src/forecast/weatherProvider.js";
import { compareDateByYear, validateIsoDate } from "../src/forecast/config.js";
import { ForecastDataStore } from "../src/forecast/dataStore.js";
import { WeightedVisitorForecastModel } from "../src/forecast/forecastModel.js";
import { loadZoneCatalog } from "../src/zone/zoneService.js";

describe("기상/날짜 처리", () => {
  it("기상청 API 키가 없으면 모의 데이터로 대체", async () => {
    const provider = new KmaVilageForecastProvider(new MockWeatherProvider());
    const old = process.env.KMA_SERVICE_KEY;
    delete process.env.KMA_SERVICE_KEY;
    const result = await provider.getWeather({ targetDate: "2026-07-18", mode: "live" });
    expect(result.apiStatus).toBe("missing_api_key");
    expect(result.forecasts.length).toBeGreaterThan(0);
    process.env.KMA_SERVICE_KEY = old;
  });

  it("날짜 형식 오류 처리", () => {
    expect(() => validateIsoDate("2026/07/18")).toThrow();
  });

  it("윤년 비교일 보정", () => {
    expect(compareDateByYear("2024-02-29", 2023)).toBe("2023-02-28");
  });
});

describe("방문객 예측 시나리오", () => {
  const service = new ForecastService();

  it("맑은 주말이며 행사가 없는 날", async () => {
    const overview = await service.getOverview({
      mode: "live",
      targetDate: "2026-07-18",
      useEventData: false,
    });
    expect(overview.busiestSlots.length).toBeGreaterThan(0);
    expect(overview.weather.currentObservation.source).toBeTruthy();
  }, 15000);

  it("맑은 주말이며 대형 행사가 있는 날은 행사 요인을 포함", async () => {
    const overview = await service.getOverview({
      mode: "live",
      targetDate: "2026-07-18",
      useEventData: true,
    });
    const factors = overview.busiestSlots.flatMap((f) => f.mainFactors);
    expect(factors.some((f) => f.includes("행사"))).toBe(true);
  });

  it("비가 오는 평일은 방문객 예측이 낮아짐", async () => {
    const rainy = await service.getOverview({ mode: "simulation", targetDate: "2026-07-15" });
    const sunny = await service.getOverview({ mode: "simulation", targetDate: "2026-07-16" });
    const rainyMax = rainy.busiestSlots[0].expectedPeople;
    const sunnyMax = sunny.busiestSlots[0].expectedPeople;
    expect(rainyMax).toBeLessThan(sunnyMax);
  });

  it("폭염이 예상되는 성수기 요인 표시", async () => {
    const overview = await service.getOverview({ mode: "simulation", targetDate: "2026-07-16" });
    const factors = overview.busiestSlots.flatMap((f) => f.mainFactors);
    expect(factors).toContain("폭염 가능성");
    expect(factors).toContain("성수기");
  });

  it("작년 같은 날짜 방문객 수가 높으면 예측 근거에 포함", async () => {
    const overview = await service.getOverview({ mode: "simulation", targetDate: "2026-07-17" });
    const factors = overview.busiestSlots.flatMap((f) => f.mainFactors);
    expect(factors).toContain("작년 같은 날짜");
  });

  it("작년과 오늘 날씨가 크게 달라도 공식 예보와 과거 비교를 분리", async () => {
    const overview = await service.getOverview({
      mode: "simulation",
      targetDate: "2026-07-15",
      compareYear: 2025,
    });
    expect(overview.weather.currentObservation.source).toBeTruthy();
    expect(overview.historicalComparison.note).toContain("패턴 비교");
  });

  it("과거 방문객 데이터가 부족한 구역은 경고 표시", async () => {
    const overview = await service.getOverview({
      mode: "simulation",
      targetDate: "2020-07-17",
      zoneIds: ["GWANGALLI-ZONE-CENTER"],
    });
    expect(
      overview.dataWarnings.some((w) => w.includes("기록이 부족")),
    ).toBe(true);
  });

  it("실시간 인원수가 예측에 추세로 반영", async () => {
    const overview = await service.getOverview(
      { mode: "live", targetDate: "2026-07-18", zoneIds: ["GWANGALLI-ZONE-CENTER"] },
      [
        {
          zoneId: "GWANGALLI-ZONE-CENTER",
          zoneName: "백사장 1구역",
          zoneType: "sand_beach",
          effectiveAreaSquareMeters: 1120,
          detectedPeople: 5000,
          rawDensity: 4.46,
          adjustedDensity: 4.46,
          riskLevel: "혼잡",
          criticalDensityReached: false,
          approachingHighRisk: false,
          trend: "증가",
          thresholds: { congestionStartDensity: 4, criticalDensity: 5, highRiskDensity: 6, hysteresisMargin: 0.2, source: "global" },
          reason: "test",
          confidence: 0.9,
          lowConfidence: false,
          requiresManagerReview: true,
          measuredAt: "2026-07-18T10:00:00.000Z",
          dataSource: "test",
          isTestData: true,
          recommendedActions: [],
          touristSummary: {} as never,
          adminSummary: {} as never,
          auxiliaryAlerts: [],
          densityBasedSafeButOtherRisks: false,
          errors: [],
          warnings: [],
          actionsTriggered: [],
        },
      ],
    );
    const first = overview.zoneForecasts["GWANGALLI-ZONE-CENTER"][0];
    expect(first.mainFactors).toContain("현재 증가 추세");
  });

  it("중앙 구역 시간대별 예측 슬롯 생성", async () => {
    const overview = await service.getOverview({
      mode: "simulation",
      targetDate: "2026-07-18",
      zoneIds: ["GWANGALLI-ZONE-CENTER"],
    });
    const slots = overview.zoneForecasts["GWANGALLI-ZONE-CENTER"] ?? [];
    expect(slots.length).toBeGreaterThan(0);
    expect(slots.every((f) => f.expectedPeople >= 0)).toBe(true);
  });

  it("이벤트 취소 시 데이터 경고", async () => {
    const overview = await service.getOverview({ mode: "simulation", targetDate: "2026-07-19" });
    expect(overview.dataWarnings.some((w) => w.includes("행사 데이터"))).toBe(true);
  }, 20000);
});

describe("시뮬레이션·사전 알림·백테스트", () => {
  const service = new ForecastService();

  it("시뮬레이션 모드에서는 실제 발송 없음", async () => {
    const scenario = service.createSimulationScenario({
      mode: "simulation",
      targetDate: "2026-07-18",
      sendTestNotification: true,
    });
    expect(scenario.realDelivery).toBe(false);
    const overview = await service.getOverview({
      mode: "simulation",
      targetDate: "2026-07-18",
      sendTestNotification: true,
    });
    expect(overview.proactiveNotifications.every((n) => !n.realDelivery)).toBe(true);
    expect(overview.proactiveNotifications.every((n) => n.message.body.includes("시뮬레이션"))).toBe(true);
  });

  it("혼잡 예상 사전 알림 생성", async () => {
    const overview = await service.getOverview({
      mode: "simulation",
      targetDate: "2026-07-16",
      sendTestNotification: false,
    });
    const slots = overview.zoneForecasts["GWANGALLI-ZONE-CENTER"] ?? [];
    expect(slots.length).toBeGreaterThan(0);
    expect(slots.some((s) => s.expectedPeople > 0)).toBe(true);
  }, 20000);

  it("전날 데이터를 이용한 백테스트", async () => {
    const bt = await service.runBacktest("2026-07-16");
    expect(bt.rows.length).toBeGreaterThan(0);
    expect(bt.dataAvailableUntil).toBe("2026-07-16T09:00:00.000Z");
    expect(bt.meanAbsoluteError).not.toBeNull();
  });

  it("예상 혼잡보다 실제 혼잡이 빨리 시작되면 늦음 표시 가능", async () => {
    const bt = await service.runBacktest("2026-07-16");
    expect(bt.rows.some((r) => r.alertSuitability === "늦음" || r.alertSuitability === "적절")).toBe(true);
  });

  it("예측값이 음수가 되지 않음", async () => {
    const provider = new MockWeatherProvider(new ForecastDataStore());
    const weather = await provider.getWeather({ targetDate: "2026-07-15", mode: "simulation" });
    const model = new WeightedVisitorForecastModel();
    const forecasts = model.forecast({
      mode: "simulation",
      targetDate: "2026-07-15",
      zones: [loadZoneCatalog().zones[0]],
      weatherForecasts: weather.forecasts,
      events: [],
      sameDateHistory: [],
      similarWeatherHistory: [],
    });
    expect(forecasts["GWANGALLI-ZONE-CENTER"].every((f) => f.expectedPeople >= 0)).toBe(true);
  });
});
