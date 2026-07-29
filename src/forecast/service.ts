import type { DensityAnalysisResult, RiskLevel } from "../types/index.js";
import type { TouristNotificationMessage } from "../notification/types.js";
import { sendNotification } from "../notification/send.js";
import { createDefaultChannels } from "../notification/channels/mockChannel.js";
import { loadThresholds } from "../config/thresholds.js";
import {
  compareDateByYear,
  FORECAST_TIME_SLOTS,
  loadForecastPolicy,
  previousDate,
  validateIsoDate,
} from "./config.js";
import { ForecastDataStore, sharedForecastDataStore } from "./dataStore.js";
import { WeightedVisitorForecastModel } from "./forecastModel.js";
import {
  KmaVilageForecastProvider,
  MockWeatherProvider,
  describeWeatherKey,
  type WeatherProvider,
} from "./weatherProvider.js";
import {
  sharedTelecomProvider,
  type SkPuzzlePlaceCongestionProvider,
  type TelecomProviderResult,
} from "./telecomProvider.js";
import type {
  BacktestResult,
  BeachEvent,
  CrowdForecast,
  AlertSuitability,
  ForecastMode,
  ForecastRequest,
  ForecastSummary,
  HistoricalComparison,
  HourlyVisitorHistory,
  ProactiveNotificationPreview,
  SimulationScenario,
  WeatherForecast,
} from "./types.js";
import { loadZoneCatalog } from "../zone/zoneService.js";
import { roundDensity } from "../density/calculator.js";
import { classifyByDensityOnly } from "../density/hysteresis.js";

function nowIso(): string {
  return new Date().toISOString();
}

function firstCrowdingSlot(rows: Array<{ timeSlot: string; expectedRiskLevel: string }>): string | undefined {
  return rows.find((r) => r.expectedRiskLevel === "혼잡" || r.expectedRiskLevel === "위험")?.timeSlot;
}

function maxDensitySlot(rows: HourlyVisitorHistory[]): HourlyVisitorHistory | undefined {
  return [...rows].sort((a, b) => b.density - a.density)[0];
}

function flattenForecasts(zoneForecasts: Record<string, CrowdForecast[]>): CrowdForecast[] {
  return Object.values(zoneForecasts).flat();
}

function currentObservationsFromResults(
  results: DensityAnalysisResult[] | undefined,
): Record<string, { people: number; density: number; trendPerHour: number }> | undefined {
  if (!results?.length) return undefined;
  const observations: Record<string, { people: number; density: number; trendPerHour: number }> = {};
  for (const result of results) {
    if (result.detectedPeople === null || result.adjustedDensity === null) continue;
    const trendPerHour =
      result.trend === "증가" ? Math.max(20, result.detectedPeople * 0.08) :
      result.trend === "감소" ? -Math.max(10, result.detectedPeople * 0.05) :
      0;
    observations[result.zoneId] = {
      people: result.detectedPeople,
      density: result.adjustedDensity,
      trendPerHour,
    };
  }
  return observations;
}

export class ForecastService {
  private policy = loadForecastPolicy();
  private model = new WeightedVisitorForecastModel(this.policy);
  private channels = createDefaultChannels();

  constructor(
    private store: ForecastDataStore = sharedForecastDataStore,
    private weatherProvider: WeatherProvider = new KmaVilageForecastProvider(new MockWeatherProvider()),
    private telecomProvider: SkPuzzlePlaceCongestionProvider | {
      getCongestion(options?: {
        live?: boolean;
        forceRefresh?: boolean;
      }): Promise<TelecomProviderResult>;
    } = sharedTelecomProvider,
  ) {}

  async getTelecomCongestion(options?: { live?: boolean; forceRefresh?: boolean }) {
    return this.telecomProvider.getCongestion(options);
  }

  async getOverview(
    request: ForecastRequest,
    currentResults?: DensityAnalysisResult[],
  ): Promise<ForecastSummary> {
    validateIsoDate(request.targetDate);
    const mode = request.mode;
    const compareYear =
      request.compareYear ?? Number(request.targetDate.slice(0, 4)) - 1;
    const compareDate = compareDateByYear(request.targetDate, compareYear);
    const generatedAt = nowIso();
    const zoneIds = request.zoneIds?.length
      ? request.zoneIds
      : loadZoneCatalog().zones.map((z) => z.zoneId);
    const zones = loadZoneCatalog().zones.filter((z) => zoneIds.includes(z.zoneId));
    const weather = request.useWeather === false
      ? await new MockWeatherProvider(this.store).getWeather({
          targetDate: request.targetDate,
          mode,
          targetTime: request.targetTime,
        })
      : await this.weatherProvider.getWeather({
          targetDate: request.targetDate,
          mode,
          targetTime: request.targetTime,
        });
    // SK는 기본 비활성. useTelecom=true(테스트)일 때만 실호출
    const telecom = await this.telecomProvider.getCongestion({
      live: request.useTelecom === true,
    });
    // 광안리 중심 좌표 측정값 → 전체 구역 예측에 동일 적용
    const centerTelecom = telecom.places[0];
    const telecomMultipliers: Record<string, { multiplier: number; label: string }> = {};
    if (request.useTelecom === true && centerTelecom) {
      for (const zone of zones) {
        telecomMultipliers[zone.zoneId] = {
          multiplier: centerTelecom.forecastMultiplier,
          label: `중심(${centerTelecom.congestionLabel})`,
        };
      }
    }
    const weatherKey = describeWeatherKey(weather.observation);
    const sameDateHistory = this.store.listVisitorHistory({ date: compareDate });
    const similarWeatherDates = this.store
      .findSimilarWeather(weatherKey, request.targetDate)
      .map((w) => w.date);
    const similarWeatherHistory = this.store
      .listVisitorHistory()
      .filter((row) => similarWeatherDates.includes(row.date));
    const events = request.useEventData === false
      ? []
      : this.store.listEvents({ date: request.targetDate, includeCancelled: true });
    const zoneForecasts = this.model.forecast({
      mode,
      targetDate: request.targetDate,
      zones,
      weatherForecasts: weather.forecasts,
      events,
      sameDateHistory,
      similarWeatherHistory,
      currentObservations: currentObservationsFromResults(currentResults),
      telecomMultipliers,
      generatedAt,
    });
    const historicalComparison = this.buildHistoricalComparison(
      request.targetDate,
      compareDate,
      weather.observation.source,
    );
    const busiestSlots = flattenForecasts(zoneForecasts)
      .sort((a, b) => b.expectedDensity - a.expectedDensity)
      .slice(0, 5);
    const proactiveNotifications = await this.createProactiveNotifications(
      flattenForecasts(zoneForecasts),
      mode,
      Boolean(request.sendTestNotification),
      request.createdBy ?? "system",
    );
    const dataWarnings: string[] = [];
    if (weather.apiStatus !== "connected") dataWarnings.push(weather.message);
    if (
      request.useTelecom === true &&
      telecom.apiStatus !== "connected" &&
      telecom.apiStatus !== "cached"
    ) {
      dataWarnings.push(telecom.message);
    }
    for (const zone of zones) {
      if (!this.store.hasEnoughVisitorHistory(zone.zoneId, request.targetDate)) {
        dataWarnings.push(`${zone.zoneName}: 과거 방문객 기록이 부족하여 예측 신뢰도가 낮아질 수 있습니다.`);
      }
    }
    if (!events.some((e) => !e.isCancelled)) dataWarnings.push("선택 날짜에 적용 가능한 행사 데이터가 없거나 취소되었습니다.");

    return {
      mode,
      forecastDate: request.targetDate,
      generatedAt,
      weather: {
        officialForecast: weather.forecasts,
        currentObservation: weather.observation,
        apiStatus: weather.apiStatus,
        lastSuccessfulAt: weather.lastSuccessfulAt,
        message: weather.message,
      },
      telecom: {
        places: telecom.places.map((p) => ({
          zoneId: p.zoneId,
          zoneName: p.zoneName,
          poiId: p.poiId,
          poiName: p.poiName,
          congestionPerSquareMeter: p.congestionPerSquareMeter,
          congestionLevel: p.congestionLevel,
          congestionLabel: p.congestionLabel,
          forecastMultiplier: p.forecastMultiplier,
          measuredAt: p.measuredAt,
          source: p.source,
        })),
        apiStatus: telecom.apiStatus,
        lastSuccessfulAt: telecom.lastSuccessfulAt,
        message: telecom.message,
      },
      historicalComparison,
      zoneForecasts,
      busiestSlots,
      proactiveNotifications,
      dataWarnings,
    };
  }

  private buildHistoricalComparison(
    targetDate: string,
    compareDate: string,
    todayWeatherSource: HistoricalComparison["todayWeatherSource"],
  ): HistoricalComparison {
    const historicalWeather = this.store.getHistoricalWeather(compareDate);
    const hourlyPeople = this.store.listVisitorHistory({ date: compareDate });
    const max = maxDensitySlot(hourlyPeople);
    const totalVisitors = hourlyPeople.reduce((sum, row) => sum + row.people, 0);
    return {
      targetDate,
      compareDate,
      dayOfWeek: historicalWeather?.dayOfWeek ?? "-",
      todayWeatherSource,
      historicalWeather,
      totalVisitors: hourlyPeople.length ? totalVisitors : undefined,
      hourlyPeople,
      maxCrowdingTime: max?.timeSlot,
      maxDensity: max?.density,
      eventHeld: this.store.listEvents({ date: compareDate }).length > 0,
      note: historicalWeather
        ? "과거 날씨는 예측 보조 변수와 패턴 비교 용도로만 사용됩니다."
        : "비교 날짜의 과거 날씨 데이터가 없습니다. 과거 비교는 가능한 범위에서 패턴 비교 용도로만 사용됩니다.",
    };
  }

  async createProactiveNotifications(
    forecasts: CrowdForecast[],
    mode: ForecastMode,
    sendTestNotification: boolean,
    createdBy: string,
  ): Promise<ProactiveNotificationPreview[]> {
    const previews: ProactiveNotificationPreview[] = [];
    const byZone = new Map<string, CrowdForecast[]>();
    for (const forecast of forecasts) {
      if (
        forecast.crowdingProbability < this.policy.crowdingProbabilityThreshold &&
        forecast.expectedRiskLevel !== "혼잡" &&
        forecast.expectedRiskLevel !== "위험"
      ) {
        continue;
      }
      byZone.set(forecast.zoneId, [...(byZone.get(forecast.zoneId) ?? []), forecast]);
    }

    for (const rows of byZone.values()) {
      const sorted = rows.sort((a, b) => a.timeSlot.localeCompare(b.timeSlot));
      const first = sorted[0];
      const last = sorted[sorted.length - 1];
      const isDanger = sorted.some((r) => r.expectedRiskLevel === "위험");
      const bodyPrefix = mode === "simulation" ? "[시뮬레이션 알림 — 실제 발송 아님] " : "";
      const messageText = isDanger
        ? `${bodyPrefix}${first.forecastDate} ${first.timeSlot} 전후 광안리 ${first.zoneName}의 인구 밀집도가 위험 수준에 가까워질 가능성이 있습니다. 해당 시간대 방문을 피하고 다른 구역이나 시간을 선택해 주세요. 현재 데이터 기준 예상이며 실제 상황은 달라질 수 있습니다.`
        : `${bodyPrefix}${first.forecastDate} ${first.timeSlot}부터 ${last.timeSlot}까지 광안리 ${first.zoneName}이 혼잡할 가능성이 높습니다. ${first.recommendation.recommendedVisitTimes[0] ?? "이른 시간"} 또는 ${first.recommendation.recommendedVisitTimes.at(-1) ?? "늦은 시간"} 방문을 권장합니다. 방문 전 실시간 안전 지도를 확인해 주세요.`;
      const message: TouristNotificationMessage = {
        audience: "tourist",
        zoneId: first.zoneId,
        zoneName: first.zoneName,
        riskLevel: isDanger ? "위험" : "혼잡",
        title: mode === "simulation" ? "시뮬레이션 혼잡 예상 알림" : "광안리 혼잡 예상 안내",
        body: messageText,
        actionHint: "방문 시간 조정 또는 대체 구역 선택",
        safeAlternative: first.recommendation.alternativeZones.join(", "),
        channels: mode === "simulation" ? ["admin_dashboard"] : ["in_app_banner", "push", "map_overlay"],
        soundAndVibration: isDanger,
        priority: isDanger ? 3 : 2,
        updatedAt: first.generatedAt,
        showDensity: first.expectedDensity,
      };
      const realDelivery = mode === "live" && sendTestNotification;
      const deliveryRecords = realDelivery
        ? await sendNotification(this.channels, {
            eventId: `FORECAST-${first.forecastId}`,
            recipientId: "forecast-subscribers",
            recipientType: "tourist",
            message,
            pushEnabled: true,
          })
        : [];
      previews.push({
        previewId: `PN-${first.forecastId}`,
        mode,
        zoneId: first.zoneId,
        zoneName: first.zoneName,
        targetDate: first.forecastDate,
        expectedStartTime: first.timeSlot,
        expectedEndTime: last.timeSlot,
        expectedRiskLevel: isDanger ? "위험" : "혼잡",
        mainReasons: [...new Set(sorted.flatMap((r) => r.mainFactors))].slice(0, 5),
        avoidTimes: [...new Set(sorted.flatMap((r) => r.recommendation.avoidTimes))],
        recommendedVisitTimes: first.recommendation.recommendedVisitTimes,
        alternativeZones: first.recommendation.alternativeZones,
        message,
        deliveryRecords,
        realDelivery,
        createdAt: nowIso(),
        createdBy,
      });
    }
    return previews;
  }

  createSimulationScenario(request: ForecastRequest): SimulationScenario {
    if (request.mode !== "simulation") {
      throw new Error("시뮬레이션 시나리오는 mode=simulation에서만 생성할 수 있습니다.");
    }
    validateIsoDate(request.targetDate);
    return {
      simulationId: `SIM-${Date.now()}`,
      mode: "simulation",
      targetDate: request.targetDate,
      targetTime: request.targetTime ?? "09:00",
      zoneIds: request.zoneIds?.length ? request.zoneIds : loadZoneCatalog().zones.map((z) => z.zoneId),
      useHistoricalWeather: request.useWeather !== false,
      useEventData: request.useEventData !== false,
      useVirtualCrowd: true,
      sendTestNotification: Boolean(request.sendTestNotification),
      compareYear: request.compareYear,
      createdAt: nowIso(),
      createdBy: request.createdBy ?? "admin",
      dataRange: {
        from: "mock-history",
        to: request.targetDate,
      },
      realDelivery: false,
    };
  }

  async runBacktest(targetDate = previousDate(new Date().toISOString().slice(0, 10))): Promise<BacktestResult> {
    validateIsoDate(targetDate);
    const dataAvailableUntil = `${targetDate}T09:00:00.000Z`;
    const compareDate = compareDateByYear(targetDate, Number(targetDate.slice(0, 4)) - 1);
    const weather = await new MockWeatherProvider(this.store).getWeather({
      targetDate,
      mode: "simulation",
    });
    const zones = loadZoneCatalog().zones;
    const forecasts = this.model.forecast({
      mode: "simulation",
      targetDate,
      zones,
      weatherForecasts: weather.forecasts,
      events: this.store.listEvents({ date: targetDate }),
      sameDateHistory: this.store.listVisitorHistory({ date: compareDate, availableUntil: dataAvailableUntil }),
      similarWeatherHistory: this.store.listVisitorHistory({ availableUntil: dataAvailableUntil }),
      generatedAt: dataAvailableUntil,
    });
    const actualRows = this.store.listVisitorHistory({ date: targetDate });
    const rows = flattenForecasts(forecasts).map((forecast) => {
      const actual = actualRows.find(
        (row) => row.zoneId === forecast.zoneId && row.timeSlot === forecast.timeSlot,
      );
      const absoluteError = actual ? Math.abs(forecast.expectedPeople - actual.people) : null;
      const errorRate = actual && actual.people > 0 ? roundDensity(absoluteError! / actual.people, 3) : null;
      const alertPlannedAt =
        forecast.crowdingProbability >= this.policy.crowdingProbabilityThreshold
          ? `${targetDate}T${Math.max(0, Number(forecast.timeSlot.slice(0, 2)) - this.policy.proactiveNotificationLeadHours)
              .toString()
              .padStart(2, "0")}:00:00.000Z`
          : undefined;
      const predictedCrowded =
        forecast.expectedRiskLevel === "혼잡" || forecast.expectedRiskLevel === "위험";
      const actualCrowded =
        actual?.riskLevel === "혼잡" || actual?.riskLevel === "위험";
      const alertSuitability: AlertSuitability =
        !actual ? "데이터부족" :
        predictedCrowded === actualCrowded ? "적절" :
        predictedCrowded && !actualCrowded ? "불필요" :
        "늦음";
      return {
        zoneId: forecast.zoneId,
        zoneName: forecast.zoneName,
        timeSlot: forecast.timeSlot,
        predictedPeople: forecast.expectedPeople,
        actualPeople: actual?.people ?? null,
        absoluteError,
        errorRate,
        predictedRiskLevel: forecast.expectedRiskLevel,
        actualRiskLevel: actual?.riskLevel ?? "데이터없음",
        alertPlannedAt,
        alertSuitability,
        errorReasons: this.errorReasons(forecast, actual),
      };
    });
    const errors = rows.filter((r) => r.absoluteError !== null);
    const meanAbsoluteError = errors.length
      ? roundDensity(errors.reduce((sum, r) => sum + (r.absoluteError ?? 0), 0) / errors.length, 2)
      : null;
    const percentageRows = rows.filter((r) => r.errorRate !== null);
    const meanAbsolutePercentageError = percentageRows.length
      ? roundDensity(percentageRows.reduce((sum, r) => sum + (r.errorRate ?? 0), 0) / percentageRows.length, 3)
      : null;
    const predictedCrowdingStartTime = firstCrowdingSlot(flattenForecasts(forecasts));
    const actualCrowdingStartTime = actualRows.find(
      (row) => row.riskLevel === "혼잡" || row.riskLevel === "위험",
    )?.timeSlot;
    return {
      backtestId: `BT-${targetDate}-${Date.now()}`,
      mode: "simulation",
      targetDate,
      generatedAt: nowIso(),
      dataAvailableUntil,
      rows,
      predictedCrowdingStartTime,
      actualCrowdingStartTime,
      meanAbsoluteError,
      meanAbsolutePercentageError,
      crowdingStartMatched:
        predictedCrowdingStartTime && actualCrowdingStartTime
          ? predictedCrowdingStartTime === actualCrowdingStartTime
          : null,
      notes: [
        "백테스트는 전날 오전 시점(dataAvailableUntil) 이전 데이터만 입력으로 사용합니다.",
        "모의 데이터 기반 결과이며 실제 정확도 지표가 아닙니다.",
      ],
    };
  }

  private errorReasons(forecast: CrowdForecast, actual?: HourlyVisitorHistory): string[] {
    if (!actual) return ["실제 측정 데이터 없음"];
    const reasons: string[] = [];
    const delta = forecast.expectedPeople - actual.people;
    if (Math.abs(delta) > Math.max(300, actual.people * 0.25)) {
      reasons.push(delta > 0 ? "예측 과대: 실제 방문객이 예상보다 적음" : "예측 과소: 실제 혼잡이 더 빨리/크게 발생");
    }
    if (forecast.mainFactors.includes("행사 없음")) reasons.push("이벤트 영향 정보 부족 가능성");
    if (forecast.confidence < 0.65) reasons.push("과거 데이터 부족으로 낮은 신뢰도");
    return reasons.length ? reasons : ["허용 범위 내 오차"];
  }

  oneHourForecastForLatest(summary: ForecastSummary, latest: DensityAnalysisResult[]): Array<{
    zoneId: string;
    zoneName: string;
    expectedPeople: number;
    expectedDensity: number;
    expectedRiskLevel: string;
    crowdingProbability: number;
  }> {
    const nextHour = new Date();
    nextHour.setHours(nextHour.getHours() + 1);
    const slot = `${nextHour.getHours().toString().padStart(2, "0")}:00`;
    return latest.map((result) => {
      const forecasts = summary.zoneForecasts[result.zoneId] ?? [];
      const next = forecasts.find((f) => f.timeSlot === slot) ?? forecasts[0];
      return {
        zoneId: result.zoneId,
        zoneName: result.zoneName,
        expectedPeople: next?.expectedPeople ?? result.detectedPeople ?? 0,
        expectedDensity: next?.expectedDensity ?? result.adjustedDensity ?? 0,
        expectedRiskLevel: next?.expectedRiskLevel ?? result.riskLevel,
        crowdingProbability: next?.crowdingProbability ?? 0,
      };
    });
  }
}

export const sharedForecastService = new ForecastService();

export function expectedRiskForPeople(people: number, area: number): RiskLevel {
  const density = roundDensity(people / area, 2);
  return classifyByDensityOnly(density, loadThresholds());
}
