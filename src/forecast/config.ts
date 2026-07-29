import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ForecastPolicy } from "./types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..");
const CONFIG_PATH = join(ROOT, "config", "forecast.default.json");

export const DEFAULT_FORECAST_POLICY: ForecastPolicy = {
  baseHourlyVisitorsByZone: {
    "GWANGALLI-ZONE-CENTER": 420,
  },
  weekendMultiplier: 1.45,
  holidayMultiplier: 1.35,
  vacationMultiplier: 1.25,
  peakSeasonMultiplier: 1.35,
  rainyMultiplier: 0.58,
  heatWaveMultiplier: 0.86,
  pleasantWeatherMultiplier: 1.18,
  strongWindMultiplier: 0.72,
  eventDistanceDecayMeters: 900,
  trendWeight: 0.18,
  historicalWeight: 0.35,
  similarWeatherWeight: 0.22,
  eventWeight: 0.25,
  uncertaintyBaseRatio: 0.16,
  lowDataConfidencePenalty: 0.18,
  crowdingProbabilityThreshold: 0.68,
  proactiveNotificationLeadHours: 3,
  simulationAllowsRealDelivery: false,
};

export function loadForecastPolicy(): ForecastPolicy {
  if (!existsSync(CONFIG_PATH)) return DEFAULT_FORECAST_POLICY;
  const raw = JSON.parse(readFileSync(CONFIG_PATH, "utf-8")) as Partial<ForecastPolicy>;
  return {
    ...DEFAULT_FORECAST_POLICY,
    ...raw,
    baseHourlyVisitorsByZone: {
      ...DEFAULT_FORECAST_POLICY.baseHourlyVisitorsByZone,
      ...(raw.baseHourlyVisitorsByZone ?? {}),
    },
    simulationAllowsRealDelivery: false,
  };
}

export function validateIsoDate(date: string): void {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new Error("날짜 형식 오류: YYYY-MM-DD 형식이어야 합니다.");
  }
  const parsed = new Date(`${date}T00:00:00.000Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    throw new Error(`존재하지 않는 날짜입니다: ${date}`);
  }
}

export function compareDateByYear(targetDate: string, compareYear: number): string {
  validateIsoDate(targetDate);
  const [, month, day] = targetDate.split("-");
  const candidate = `${compareYear}-${month}-${day}`;
  const parsed = new Date(`${candidate}T00:00:00.000Z`);
  if (!Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === candidate) {
    return candidate;
  }
  // 윤년 2월 29일은 비교 연도에 없을 수 있으므로 2월 28일로 보정한다.
  if (month === "02" && day === "29") {
    return `${compareYear}-02-28`;
  }
  throw new Error(`비교 날짜를 만들 수 없습니다: ${targetDate}, ${compareYear}`);
}

export function previousDate(date: string): string {
  validateIsoDate(date);
  const d = new Date(`${date}T00:00:00.000Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

export function isWeekend(date: string): boolean {
  const day = new Date(`${date}T00:00:00.000Z`).getUTCDay();
  return day === 0 || day === 6;
}

export function isPeakSeason(date: string): boolean {
  const monthDay = date.slice(5);
  return monthDay >= "07-01" && monthDay <= "08-31";
}

export function isVacationSeason(date: string): boolean {
  const monthDay = date.slice(5);
  return monthDay >= "07-15" && monthDay <= "08-20";
}

export const FORECAST_TIME_SLOTS = [
  "09:00",
  "10:00",
  "11:00",
  "12:00",
  "13:00",
  "14:00",
  "15:00",
  "16:00",
  "17:00",
  "18:00",
];
