/**
 * WaveGuard 관광객·관리자 공통 대시보드 페이로드.
 * 날짜·기상·예상 방문자·혼잡/위험 비율·현재 위험도는 한 소스에서만 계산한다.
 */

import type { DensityAnalysisResult, RiskLevel } from "../types/index.js";
import type { ForecastSummary } from "../forecast/types.js";
import type {
  TelecomApiStatus,
  TelecomProviderResult,
} from "../forecast/telecomProvider.js";
import { judgeBeachWide } from "../density/beachWide.js";

export interface WaveGuardDashboard {
  beachId: string;
  beachName: string;
  date: string;
  dateLabel: string;
  weather: {
    skyCondition: string;
    temperatureCelsius: number;
    feelsLikeCelsius: number;
    apiStatus: string;
  };
  expectedVisitors: number;
  /** 안전·주의(혼잡)·위험 비율 — 합계 100 */
  distribution: {
    안전: number;
    주의: number;
    위험: number;
  };
  currentRisk: {
    percent: number;
    /** UI 표시용: 혼잡 → 주의 */
    displayLevel: "안전" | "주의" | "위험" | "데이터없음";
    riskLevel: RiskLevel;
  };
  copy: {
    tourist: { 안전: string; 주의: string; 위험: string };
    admin: { 안전: string; 주의: string; 위험: string };
    touristGuide: Record<string, string>;
    adminGuide: Record<string, string>;
  };
  zones: Array<{
    zoneId: string;
    zoneName: string;
    riskLevel: RiskLevel;
    displayLevel: "안전" | "주의" | "위험" | "데이터없음";
    adjustedDensity: number | null;
  }>;
  /** SK 혼잡도가 있을 때 지도·위험 카드에 우선 반영 */
  map?: {
    displayLevel: "안전" | "주의" | "위험" | "데이터없음";
    source: "sk" | "density";
  };
  telecom?: {
    apiStatus: TelecomApiStatus;
    message: string;
    lastSuccessfulAt?: string;
    poiName?: string;
    congestionLabel?: string;
    congestionLevel?: 1 | 2 | 3 | 4 | null;
    measuredAt?: string;
    refreshIntervalMinutes: number;
  };
  generatedAt: string;
}

/** SK 혼잡도 등급(1~4) → WaveGuard 표시 등급 */
export function skCongestionToDisplayLevel(
  level: 1 | 2 | 3 | 4 | null,
): "안전" | "주의" | "위험" | "데이터없음" {
  if (level === 1) return "안전";
  if (level === 2) return "주의";
  if (level === 3) return "주의";
  if (level === 4) return "위험";
  return "데이터없음";
}

export function skCongestionToPercent(level: 1 | 2 | 3 | 4 | null): number {
  if (level === 1) return 28;
  if (level === 2) return 52;
  if (level === 3) return 76;
  if (level === 4) return 93;
  return 0;
}

export function isSkTelecomActive(status: TelecomApiStatus): boolean {
  return status === "connected" || status === "cached";
}

function displayLevelToRisk(
  level: "안전" | "주의" | "위험" | "데이터없음",
): RiskLevel {
  if (level === "안전") return "안전";
  if (level === "주의") return "혼잡";
  if (level === "위험") return "위험";
  return "데이터없음";
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

function displayLevel(
  level: RiskLevel | string,
): "안전" | "주의" | "위험" | "데이터없음" {
  if (level === "안전") return "안전";
  if (level === "혼잡") return "주의";
  if (level === "위험") return "위험";
  return "데이터없음";
}

function formatDateLabel(isoDate: string): string {
  const d = new Date(`${isoDate}T12:00:00`);
  if (Number.isNaN(d.getTime())) return isoDate;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}.${m}.${day} ${WEEKDAYS[d.getDay()]}`;
}

function currentRiskPercent(results: DensityAnalysisResult[]): number {
  const measured = results.filter(
    (r) =>
      r.adjustedDensity != null &&
      r.thresholds?.highRiskDensity &&
      r.detectedPeople != null,
  );
  if (!measured.length) return 0;
  return Math.max(
    ...measured.map((r) =>
      Math.min(
        100,
        Math.round(
          ((r.adjustedDensity as number) / r.thresholds.highRiskDensity) * 100,
        ),
      ),
    ),
  );
}

function buildDistribution(
  summary: ForecastSummary,
  overall: RiskLevel,
): { 안전: number; 주의: number; 위험: number } {
  const slots = Object.values(summary.zoneForecasts).flat();
  if (slots.length) {
    let safe = 0;
    let caution = 0;
    let danger = 0;
    for (const s of slots) {
      if (s.expectedRiskLevel === "위험") danger += 1;
      else if (s.expectedRiskLevel === "혼잡") caution += 1;
      else safe += 1;
    }
    const total = safe + caution + danger;
    if (total > 0) {
      const 안전 = Math.round((safe / total) * 100);
      const 주의 = Math.round((caution / total) * 100);
      const 위험 = Math.max(0, 100 - 안전 - 주의);
      return { 안전, 주의, 위험 };
    }
  }
  // 예측 슬롯이 없으면 현재 전체 등급 기준으로 대표 비율
  if (overall === "위험") return { 안전: 20, 주의: 30, 위험: 50 };
  if (overall === "혼잡") return { 안전: 35, 주의: 45, 위험: 20 };
  if (overall === "안전") return { 안전: 70, 주의: 20, 위험: 10 };
  return { 안전: 58, 주의: 27, 위험: 15 };
}

function expectedVisitorTotal(summary: ForecastSummary): number {
  const bySlot = new Map<string, number>();
  for (const list of Object.values(summary.zoneForecasts)) {
    for (const f of list) {
      bySlot.set(f.timeSlot, (bySlot.get(f.timeSlot) ?? 0) + f.expectedPeople);
    }
  }
  if (!bySlot.size) return 0;
  return Math.max(...bySlot.values());
}

const SK_REFRESH_MINUTES = Number.isFinite(
  Number(process.env.SK_TELECOM_CACHE_MS),
)
  ? Number(process.env.SK_TELECOM_CACHE_MS) / 60000
  : 10;

export function buildWaveGuardDashboard(
  results: DensityAnalysisResult[],
  summary: ForecastSummary,
  beach: { beachId: string; beachName: string },
  telecom?: TelecomProviderResult,
): WaveGuardDashboard {
  const judgment = judgeBeachWide(results);
  const overall = judgment.overallRiskLevel;
  const weather = summary.weather.currentObservation;
  const percent = currentRiskPercent(results);

  const base: WaveGuardDashboard = {
    beachId: beach.beachId,
    beachName: beach.beachName,
    date: summary.forecastDate,
    dateLabel: formatDateLabel(summary.forecastDate),
    weather: {
      skyCondition: weather.skyCondition,
      temperatureCelsius: weather.temperatureCelsius,
      feelsLikeCelsius: weather.feelsLikeCelsius,
      apiStatus: summary.weather.apiStatus,
    },
    expectedVisitors: expectedVisitorTotal(summary),
    distribution: buildDistribution(summary, overall),
    currentRisk: {
      percent,
      displayLevel: displayLevel(overall),
      riskLevel: overall,
    },
    copy: {
      tourist: {
        안전: "여유로운 상태입니다. 안심하고 해수욕을 즐겨보세요.",
        주의: "다소 붐비는 상태입니다. 주변 상황을 살피며 안전에 주의하세요.",
        위험: "매우 혼잡한 상태입니다. 안전을 위해 방문·접근을 자제해 주세요.",
      },
      admin: {
        안전: "관리 가능한 수준입니다. 기본 순찰을 유지하세요.",
        주의: "혼잡이 증가하고 있습니다. 안내 방송과 현장 점검을 강화하세요.",
        위험: "즉시 대응이 필요한 상태입니다. 경보 방송과 인력 투입을 검토하세요.",
      },
      touristGuide: {
        안전: "여유로운 상태입니다. 안심하고 해수욕을 즐겨보세요.",
        주의: "위험도가 다소 높습니다. 주변을 살피며 주의하세요.",
        위험: "위험 수준입니다. 해수욕장 외곽으로 이동하거나 방문을 자제하세요.",
        데이터없음: "실측 데이터를 기다리는 중입니다.",
      },
      adminGuide: {
        안전: "위험도가 낮습니다. 기본 순찰을 유지하세요.",
        주의: "위험도가 다소 높습니다. 현장 모니터링과 안전 안내를 강화하세요.",
        위험: "위험도가 높습니다. 즉시 현장 확인과 통제를 검토하세요.",
        데이터없음: "실측 데이터가 없습니다. CCTV·수동 입력을 확인하세요.",
      },
    },
    zones: results.map((r) => ({
      zoneId: r.zoneId,
      zoneName: r.zoneName,
      riskLevel: r.riskLevel,
      displayLevel: displayLevel(r.riskLevel),
      adjustedDensity: r.adjustedDensity,
    })),
    generatedAt: new Date().toISOString(),
  };

  if (!telecom) return base;

  const place = telecom.places[0];
  base.telecom = {
    apiStatus: telecom.apiStatus,
    message: telecom.message,
    lastSuccessfulAt: telecom.lastSuccessfulAt,
    poiName: place?.poiName,
    congestionLabel: place?.congestionLabel,
    congestionLevel: place?.congestionLevel ?? null,
    measuredAt: place?.measuredAt,
    refreshIntervalMinutes: SK_REFRESH_MINUTES,
  };

  if (
    isSkTelecomActive(telecom.apiStatus) &&
    place?.congestionLevel != null
  ) {
    const skDisplay = skCongestionToDisplayLevel(place.congestionLevel);
    base.map = { displayLevel: skDisplay, source: "sk" };
    base.currentRisk = {
      percent: skCongestionToPercent(place.congestionLevel),
      displayLevel: skDisplay,
      riskLevel: displayLevelToRisk(skDisplay),
    };
  }

  return base;
}
