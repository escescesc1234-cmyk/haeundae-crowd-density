import type { RiskLevel, ZoneType } from "../types/index.js";
import type {
  NotificationDeliveryRecord,
  TouristNotificationMessage,
} from "../notification/types.js";

export type ForecastMode = "live" | "simulation";
export type DataQuality =
  | "realtime"
  | "historical"
  | "forecast"
  | "simulation"
  | "missing"
  | "stale"
  | "low_confidence"
  | "mock";
export type WeatherSource =
  | "kma_vilage_fcst"
  | "historical_weather"
  | "mock"
  | "last_known_good";
export type SkyCondition = "맑음" | "구름많음" | "흐림" | "비" | "눈" | "소나기";
export type AlertSuitability = "적절" | "빠름" | "늦음" | "불필요" | "데이터부족";

export interface ForecastPolicy {
  baseHourlyVisitorsByZone: Record<string, number>;
  weekendMultiplier: number;
  holidayMultiplier: number;
  vacationMultiplier: number;
  peakSeasonMultiplier: number;
  rainyMultiplier: number;
  heatWaveMultiplier: number;
  pleasantWeatherMultiplier: number;
  strongWindMultiplier: number;
  eventDistanceDecayMeters: number;
  trendWeight: number;
  historicalWeight: number;
  similarWeatherWeight: number;
  eventWeight: number;
  uncertaintyBaseRatio: number;
  lowDataConfidencePenalty: number;
  crowdingProbabilityThreshold: number;
  proactiveNotificationLeadHours: number;
  simulationAllowsRealDelivery: false;
}

export interface DataStamp {
  source: string;
  observedAt: string;
  quality: DataQuality;
  isMock: boolean;
}

export interface CrowdObservation {
  observationId: string;
  mode: ForecastMode;
  zoneId: string;
  zoneName: string;
  zoneType: ZoneType;
  observedAt: string;
  people: number;
  effectiveAreaSquareMeters: number;
  density: number;
  riskLevel: RiskLevel;
  confidence: number;
  source: "cctv_detection" | "manual" | "test" | "mock_history";
  dataQuality: DataQuality;
}

export interface WeatherObservation {
  weatherId: string;
  mode: ForecastMode;
  locationName: string;
  observedAt: string;
  temperatureCelsius: number;
  feelsLikeCelsius: number;
  precipitationMm: number;
  precipitationProbability: number;
  skyCondition: SkyCondition;
  humidityPercent: number;
  windSpeedMetersPerSecond: number;
  windDirectionDegrees: number;
  weatherAlerts: string[];
  source: WeatherSource;
  dataQuality: DataQuality;
}

export interface WeatherForecast {
  forecastId: string;
  mode: ForecastMode;
  locationName: string;
  baseTime: string;
  targetTime: string;
  temperatureCelsius: number;
  feelsLikeCelsius: number;
  precipitationProbability: number;
  precipitationMm: number;
  skyCondition: SkyCondition;
  humidityPercent: number;
  windSpeedMetersPerSecond: number;
  windDirectionDegrees: number;
  weatherAlerts: string[];
  waveHeightMeters?: number;
  waterTemperatureCelsius?: number;
  tideCondition?: string;
  marineAlerts?: string[];
  source: WeatherSource;
  dataQuality: DataQuality;
}

export interface HistoricalWeather extends WeatherObservation {
  date: string;
  dayOfWeek: string;
  similarWeatherKey: string;
}

export interface BeachEvent {
  eventId: string;
  name: string;
  date: string;
  startTime: string;
  endTime: string;
  locationName: string;
  zoneId?: string;
  expectedParticipants: number;
  distanceToZoneMeters: number;
  eventType: "festival" | "concert" | "fireworks" | "sports" | "market" | "none";
  isCancelled?: boolean;
  source: "admin" | "public_event" | "mock";
}

export interface BeachZone {
  zoneId: string;
  zoneName: string;
  zoneType: ZoneType;
  effectiveAreaSquareMeters: number;
}

export interface RiskAssessment {
  riskLevel: RiskLevel;
  density: number;
  thresholds: {
    congestionStartDensity: number;
    criticalDensity: number;
    highRiskDensity: number;
  };
  reason: string;
}

export interface HourlyVisitorHistory {
  date: string;
  zoneId: string;
  timeSlot: string;
  people: number;
  density: number;
  riskLevel: RiskLevel;
  source: "mock_history" | "cctv_detection" | "manual";
  availableAt: string;
}

export interface ForecastFactor {
  name: string;
  multiplier: number;
  explanation: string;
}

export interface CrowdForecast {
  forecastId: string;
  mode: ForecastMode;
  zoneId: string;
  zoneName: string;
  forecastDate: string;
  timeSlot: string;
  expectedPeople: number;
  minimumExpectedPeople: number;
  maximumExpectedPeople: number;
  expectedDensity: number;
  expectedRiskLevel: RiskLevel;
  crowdingProbability: number;
  confidence: number;
  mainFactors: string[];
  factors: ForecastFactor[];
  recommendation: {
    recommendedVisitTimes: string[];
    avoidTimes: string[];
    alternativeZones: string[];
  };
  dataSources: DataStamp[];
  generatedAt: string;
}

export interface ForecastSummary {
  mode: ForecastMode;
  forecastDate: string;
  generatedAt: string;
  weather: {
    officialForecast: WeatherForecast[];
    currentObservation: WeatherObservation;
    apiStatus: "connected" | "mock" | "failed" | "missing_api_key";
    lastSuccessfulAt?: string;
    message: string;
  };
  /** SK 장소 혼잡도 등 통신사 보조 소스 (밀도 판정 본선 아님) */
  telecom?: {
    places: Array<{
      zoneId: string;
      zoneName: string;
      poiId: string;
      poiName: string;
      congestionPerSquareMeter: number | null;
      congestionLevel: number | null;
      congestionLabel: string;
      forecastMultiplier: number;
      measuredAt: string;
      source: string;
    }>;
    apiStatus: "connected" | "mock" | "idle" | "failed" | "missing_api_key" | "not_subscribed" | "quota_exceeded" | "cached";
    lastSuccessfulAt?: string;
    message: string;
  };
  historicalComparison: HistoricalComparison;
  zoneForecasts: Record<string, CrowdForecast[]>;
  busiestSlots: CrowdForecast[];
  proactiveNotifications: ProactiveNotificationPreview[];
  dataWarnings: string[];
}

export interface HistoricalComparison {
  targetDate: string;
  compareDate: string;
  dayOfWeek: string;
  todayWeatherSource: WeatherSource;
  historicalWeather?: HistoricalWeather;
  totalVisitors?: number;
  hourlyPeople?: HourlyVisitorHistory[];
  maxCrowdingTime?: string;
  maxDensity?: number;
  eventHeld: boolean;
  note: string;
}

export interface SimulationScenario {
  simulationId: string;
  mode: "simulation";
  targetDate: string;
  targetTime: string;
  zoneIds: string[];
  useHistoricalWeather: boolean;
  useEventData: boolean;
  useVirtualCrowd: boolean;
  sendTestNotification: boolean;
  compareYear?: number;
  createdAt: string;
  createdBy: string;
  dataRange: {
    from: string;
    to: string;
  };
  realDelivery: false;
}

export interface BacktestRow {
  zoneId: string;
  zoneName: string;
  timeSlot: string;
  predictedPeople: number;
  actualPeople: number | null;
  absoluteError: number | null;
  errorRate: number | null;
  predictedRiskLevel: RiskLevel;
  actualRiskLevel: RiskLevel | "데이터없음";
  alertPlannedAt?: string;
  alertSuitability: AlertSuitability;
  errorReasons: string[];
}

export interface BacktestResult {
  backtestId: string;
  mode: "simulation";
  targetDate: string;
  generatedAt: string;
  dataAvailableUntil: string;
  rows: BacktestRow[];
  predictedCrowdingStartTime?: string;
  actualCrowdingStartTime?: string;
  meanAbsoluteError: number | null;
  meanAbsolutePercentageError: number | null;
  crowdingStartMatched: boolean | null;
  notes: string[];
}

export interface ProactiveNotificationPreview {
  previewId: string;
  mode: ForecastMode;
  zoneId: string;
  zoneName: string;
  targetDate: string;
  expectedStartTime: string;
  expectedEndTime: string;
  expectedRiskLevel: RiskLevel;
  mainReasons: string[];
  avoidTimes: string[];
  recommendedVisitTimes: string[];
  alternativeZones: string[];
  message: TouristNotificationMessage;
  deliveryRecords: NotificationDeliveryRecord[];
  realDelivery: boolean;
  createdAt: string;
  createdBy?: string;
}

export interface ForecastRequest {
  mode: ForecastMode;
  targetDate: string;
  targetTime?: string;
  zoneIds?: string[];
  compareYear?: number;
  useWeather?: boolean;
  useEventData?: boolean;
  /** true일 때만 SK 장소 혼잡도 실호출 (기본 false — 새로고침마다 호출 금지) */
  useTelecom?: boolean;
  sendTestNotification?: boolean;
  createdBy?: string;
}
