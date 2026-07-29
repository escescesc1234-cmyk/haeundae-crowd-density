import { loadThresholds } from "../config/thresholds.js";
import { roundDensity } from "../density/calculator.js";
import { classifyByDensityOnly } from "../density/hysteresis.js";
import type { RiskLevel, ZoneDefinition } from "../types/index.js";
import { loadZoneCatalog } from "../zone/zoneService.js";
import {
  FORECAST_TIME_SLOTS,
  isPeakSeason,
  isVacationSeason,
  isWeekend,
  loadForecastPolicy,
} from "./config.js";
import type {
  BeachEvent,
  CrowdForecast,
  ForecastFactor,
  ForecastMode,
  ForecastPolicy,
  HourlyVisitorHistory,
  WeatherForecast,
} from "./types.js";

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function timeMultiplier(slot: string): number {
  const hour = Number(slot.slice(0, 2));
  if (hour <= 10) return 0.55;
  if (hour === 11) return 0.82;
  if (hour === 12) return 1.05;
  if (hour === 13) return 1.25;
  if (hour === 14 || hour === 15) return 1.45;
  if (hour === 16) return 1.2;
  if (hour === 17) return 0.9;
  return 0.55;
}

function weatherMultiplier(weather: WeatherForecast, policy: ForecastPolicy): ForecastFactor {
  if (weather.precipitationMm > 0 || weather.precipitationProbability >= 60) {
    return {
      name: "비 예보",
      multiplier: policy.rainyMultiplier,
      explanation: "강수 예보로 방문객 감소 보정",
    };
  }
  if (weather.weatherAlerts.some((a) => a.includes("폭염")) || weather.feelsLikeCelsius >= 35) {
    return {
      name: "폭염 가능성",
      multiplier: policy.heatWaveMultiplier,
      explanation: "체감온도와 특보로 체류 시간 감소 보정",
    };
  }
  if (weather.windSpeedMetersPerSecond >= 8) {
    return {
      name: "강풍",
      multiplier: policy.strongWindMultiplier,
      explanation: "강한 바람으로 해변 이용 감소 보정",
    };
  }
  if (weather.skyCondition === "맑음" && weather.temperatureCelsius >= 26 && weather.temperatureCelsius <= 32) {
    return {
      name: "맑고 더운 날씨",
      multiplier: policy.pleasantWeatherMultiplier,
      explanation: "해수욕장 방문 선호 조건",
    };
  }
  return {
    name: "보통 날씨",
    multiplier: 1,
    explanation: "날씨 보정 없음",
  };
}

function eventMultiplier(
  events: BeachEvent[],
  zoneId: string,
  slot: string,
  policy: ForecastPolicy,
): ForecastFactor {
  const active = events.filter((e) => {
    if (e.isCancelled) return false;
    if (e.zoneId && e.zoneId !== zoneId) return false;
    return e.startTime <= slot && e.endTime >= slot;
  });
  if (!active.length) {
    return { name: "행사 없음", multiplier: 1, explanation: "해당 시간대 행사 영향 없음" };
  }
  const strongest = active.reduce((best, event) => {
    const distanceEffect = Math.max(
      0,
      1 - event.distanceToZoneMeters / policy.eventDistanceDecayMeters,
    );
    const participantEffect = clamp(event.expectedParticipants / 5000, 0.05, 1.1);
    const multiplier = 1 + policy.eventWeight * distanceEffect * participantEffect;
    return multiplier > best.multiplier ? { event, multiplier } : best;
  }, { event: active[0], multiplier: 1 });
  return {
    name: `행사: ${strongest.event.name}`,
    multiplier: Math.round(strongest.multiplier * 100) / 100,
    explanation: `${strongest.event.eventType} 행사와 구역 거리 ${strongest.event.distanceToZoneMeters}m`,
  };
}

function averagePeople(rows: HourlyVisitorHistory[], slot: string): number | null {
  const values = rows.filter((r) => r.timeSlot === slot).map((r) => r.people);
  if (!values.length) return null;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

function riskFromDensity(density: number): RiskLevel {
  return classifyByDensityOnly(density, loadThresholds());
}

function alternativeZones(allZones: ZoneDefinition[], currentZoneId: string): string[] {
  return allZones
    .filter((z) => z.zoneId !== currentZoneId)
    .slice(0, 2)
    .map((z) => z.zoneName);
}

export interface ForecastModelInput {
  mode: ForecastMode;
  targetDate: string;
  zones?: ZoneDefinition[];
  weatherForecasts: WeatherForecast[];
  events: BeachEvent[];
  sameDateHistory: HourlyVisitorHistory[];
  similarWeatherHistory: HourlyVisitorHistory[];
  currentObservations?: Record<string, { people: number; density: number; trendPerHour: number }>;
  /** zoneId → SK 혼잡도 기반 보조 배수 */
  telecomMultipliers?: Record<string, { multiplier: number; label: string }>;
  generatedAt?: string;
}

export class WeightedVisitorForecastModel {
  constructor(private policy: ForecastPolicy = loadForecastPolicy()) {}

  forecast(input: ForecastModelInput): Record<string, CrowdForecast[]> {
    const catalog = loadZoneCatalog();
    const zones = input.zones ?? catalog.zones;
    const generatedAt = input.generatedAt ?? new Date().toISOString();
    const result: Record<string, CrowdForecast[]> = {};

    for (const zone of zones) {
      result[zone.zoneId] = FORECAST_TIME_SLOTS.map((slot) => {
        const weather =
          input.weatherForecasts.find((w) => w.targetTime.includes(`T${slot}`)) ??
          input.weatherForecasts[0];
        const factors: ForecastFactor[] = [];
        const base =
          this.policy.baseHourlyVisitorsByZone[zone.zoneId] ??
          Math.max(50, zone.effectiveAreaSquareMeters * 0.35);

        factors.push({
          name: "기본 시간대",
          multiplier: timeMultiplier(slot),
          explanation: `${slot} 시간대 기본 방문 패턴`,
        });
        if (isWeekend(input.targetDate)) {
          factors.push({
            name: "주말",
            multiplier: this.policy.weekendMultiplier,
            explanation: "주말 방문 증가 보정",
          });
        }
        if (isPeakSeason(input.targetDate)) {
          factors.push({
            name: "성수기",
            multiplier: this.policy.peakSeasonMultiplier,
            explanation: "7~8월 성수기 보정",
          });
        }
        if (isVacationSeason(input.targetDate)) {
          factors.push({
            name: "방학·휴가철",
            multiplier: this.policy.vacationMultiplier,
            explanation: "휴가철 방문 증가 보정",
          });
        }
        factors.push(weatherMultiplier(weather, this.policy));
        factors.push(eventMultiplier(input.events, zone.zoneId, slot, this.policy));

        const sameDateAvg = averagePeople(
          input.sameDateHistory.filter((h) => h.zoneId === zone.zoneId),
          slot,
        );
        if (sameDateAvg !== null) {
          factors.push({
            name: "작년 같은 날짜",
            multiplier: 1 + ((sameDateAvg / Math.max(base, 1)) - 1) * this.policy.historicalWeight,
            explanation: `작년 같은 날짜 ${slot} 방문객 ${Math.round(sameDateAvg)}명`,
          });
        }

        const similarAvg = averagePeople(
          input.similarWeatherHistory.filter((h) => h.zoneId === zone.zoneId),
          slot,
        );
        if (similarAvg !== null) {
          factors.push({
            name: "비슷한 날씨",
            multiplier: 1 + ((similarAvg / Math.max(base, 1)) - 1) * this.policy.similarWeatherWeight,
            explanation: `비슷한 날씨 조건의 평균 ${Math.round(similarAvg)}명`,
          });
        }

        const current = input.currentObservations?.[zone.zoneId];
        if (current) {
          const projected = current.people + current.trendPerHour;
          factors.push({
            name: "현재 증가 추세",
            multiplier: 1 + ((projected / Math.max(base, 1)) - 1) * this.policy.trendWeight,
            explanation: `최근 1시간 추세 반영 (${Math.round(current.trendPerHour)}명/시간)`,
          });
        }

        const telecom = input.telecomMultipliers?.[zone.zoneId];
        if (telecom) {
          factors.push({
            name: "통신사 장소 혼잡도(보조)",
            multiplier: telecom.multiplier,
            explanation: `SK 장소 혼잡도 ${telecom.label} — 밀도 판정 본선이 아닌 예측 보조`,
          });
        }

        const multiplied = factors.reduce((value, factor) => value * factor.multiplier, base);
        const expectedPeople = Math.max(0, Math.round(multiplied));
        const expectedDensity = roundDensity(expectedPeople / zone.effectiveAreaSquareMeters, 2);
        const expectedRiskLevel = riskFromDensity(expectedDensity);
        const uncertaintyRatio =
          this.policy.uncertaintyBaseRatio +
          (sameDateAvg === null ? this.policy.lowDataConfidencePenalty : 0) +
          (similarAvg === null ? this.policy.lowDataConfidencePenalty / 2 : 0);
        const crowdingProbability = clamp(
          expectedDensity / loadThresholds().congestionStartDensity,
          0,
          1,
        );
        const confidence = clamp(1 - uncertaintyRatio, 0.35, 0.9);
        const avoidTimes =
          expectedRiskLevel === "혼잡" || expectedRiskLevel === "위험" ? [slot] : [];
        const recommendedVisitTimes = FORECAST_TIME_SLOTS.filter((candidate) => {
          const hour = Number(candidate.slice(0, 2));
          return hour <= 10 || hour >= 18;
        });

        return {
          forecastId: `CF-${input.mode}-${zone.zoneId}-${input.targetDate}-${slot}`,
          mode: input.mode,
          zoneId: zone.zoneId,
          zoneName: zone.zoneName,
          forecastDate: input.targetDate,
          timeSlot: slot,
          expectedPeople,
          minimumExpectedPeople: Math.max(0, Math.round(expectedPeople * (1 - uncertaintyRatio))),
          maximumExpectedPeople: Math.round(expectedPeople * (1 + uncertaintyRatio)),
          expectedDensity,
          expectedRiskLevel,
          crowdingProbability: roundDensity(crowdingProbability, 2),
          confidence: roundDensity(confidence, 2),
          mainFactors: factors
            .filter((f) => Math.abs(f.multiplier - 1) >= 0.05)
            .map((f) => f.name)
            .slice(0, 8),
          factors,
          recommendation: {
            recommendedVisitTimes,
            avoidTimes,
            alternativeZones: alternativeZones(catalog.zones, zone.zoneId),
          },
          dataSources: [
            {
              source: weather.source,
              observedAt: weather.baseTime,
              quality: weather.dataQuality,
              isMock: weather.source === "mock",
            },
            {
              source: sameDateAvg === null ? "방문객 기록 부족" : "과거 방문객 데이터",
              observedAt: generatedAt,
              quality: sameDateAvg === null ? "missing" : "historical",
              isMock: true,
            },
          ],
          generatedAt,
        };
      });
    }
    return result;
  }
}
