import type {
  ForecastMode,
  SkyCondition,
  WeatherForecast,
  WeatherObservation,
  WeatherSource,
} from "./types.js";
import { FORECAST_TIME_SLOTS, validateIsoDate } from "./config.js";
import { ForecastDataStore, sharedForecastDataStore } from "./dataStore.js";

export interface WeatherProviderResult {
  observation: WeatherObservation;
  forecasts: WeatherForecast[];
  apiStatus: "connected" | "mock" | "failed" | "missing_api_key";
  lastSuccessfulAt?: string;
  message: string;
}

export interface WeatherProvider {
  getWeather(params: {
    targetDate: string;
    mode: ForecastMode;
    targetTime?: string;
  }): Promise<WeatherProviderResult>;
}

function weatherKeyFromConditions(sky: SkyCondition, temp: number, alerts: string[]): string {
  if (alerts.some((a) => a.includes("폭염"))) return "heatwave-clear";
  if (sky === "비" || sky === "소나기") return "rainy-weekday";
  if (temp >= 29) return "hot-clear";
  if (temp <= 12) return "cool-windy";
  return "mild";
}

function feelsLike(temp: number, humidity: number, wind: number): number {
  const humidityEffect = humidity >= 70 ? 1.5 : humidity >= 60 ? 0.8 : 0;
  const windEffect = wind >= 6 ? -1 : 0;
  return Math.round((temp + humidityEffect + windEffect) * 10) / 10;
}

function makeMockForecast(
  date: string,
  mode: ForecastMode,
  source: WeatherSource,
  statusQuality: WeatherForecast["dataQuality"],
): WeatherForecast[] {
  const isRainy = date === "2026-07-15";
  const isHeat = date === "2026-07-16" || date === "2026-07-18";
  return FORECAST_TIME_SLOTS.map((slot, index) => {
    const hour = Number(slot.slice(0, 2));
    const temp = isRainy ? 24 + Math.max(0, index - 2) * 0.2 : isHeat ? 31 + Math.max(0, index - 2) * 0.4 : 28 + Math.sin(index / 2) * 2;
    const humidity = isRainy ? 88 : isHeat ? 68 : 62;
    const wind = isRainy ? 5.2 : isHeat ? 2.8 : 3.3;
    const sky: SkyCondition = isRainy ? "비" : isHeat ? "맑음" : hour >= 16 ? "구름많음" : "맑음";
    const alerts = isHeat ? ["폭염주의보"] : [];
    return {
      forecastId: `WF-${date}-${slot}`,
      mode,
      locationName: "광안리 해수욕장",
      baseTime: `${date}T00:00:00.000Z`,
      targetTime: `${date}T${slot}:00.000Z`,
      temperatureCelsius: Math.round(temp * 10) / 10,
      feelsLikeCelsius: feelsLike(temp, humidity, wind),
      precipitationProbability: isRainy ? 80 : isHeat ? 5 : 20,
      precipitationMm: isRainy ? 12 : 0,
      skyCondition: sky,
      humidityPercent: humidity,
      windSpeedMetersPerSecond: wind,
      windDirectionDegrees: isRainy ? 190 : 150,
      weatherAlerts: alerts,
      waveHeightMeters: isRainy ? 1.4 : 0.6,
      waterTemperatureCelsius: 24,
      tideCondition: "보통",
      marineAlerts: [],
      source,
      dataQuality: statusQuality,
    };
  });
}

export class MockWeatherProvider implements WeatherProvider {
  constructor(private store: ForecastDataStore = sharedForecastDataStore) {}

  async getWeather(params: {
    targetDate: string;
    mode: ForecastMode;
    targetTime?: string;
  }): Promise<WeatherProviderResult> {
    validateIsoDate(params.targetDate);
    const historical = this.store.getHistoricalWeather(params.targetDate);
    const forecasts = makeMockForecast(params.targetDate, params.mode, "mock", "mock");
    const first = forecasts[0];
    const observation: WeatherObservation = historical
      ? {
          ...historical,
          weatherId: `OBS-${params.targetDate}`,
          mode: params.mode,
          source: "historical_weather",
          dataQuality: params.mode === "simulation" ? "simulation" : "historical",
        }
      : {
          weatherId: `OBS-${params.targetDate}`,
          mode: params.mode,
          locationName: "광안리 해수욕장",
          observedAt: first.baseTime,
          temperatureCelsius: first.temperatureCelsius,
          feelsLikeCelsius: first.feelsLikeCelsius,
          precipitationMm: first.precipitationMm,
          precipitationProbability: first.precipitationProbability,
          skyCondition: first.skyCondition,
          humidityPercent: first.humidityPercent,
          windSpeedMetersPerSecond: first.windSpeedMetersPerSecond,
          windDirectionDegrees: first.windDirectionDegrees,
          weatherAlerts: first.weatherAlerts,
          source: "mock",
          dataQuality: "mock",
        };

    return {
      observation,
      forecasts,
      apiStatus: "mock",
      lastSuccessfulAt: first.baseTime,
      message: "기상청 API 키가 없거나 개발 환경이므로 테스트용 모의 기상 데이터를 사용합니다.",
    };
  }
}

interface KmaItem {
  category: string;
  fcstDate: string;
  fcstTime: string;
  fcstValue: string;
}

function skyFromKma(items: KmaItem[], date: string, time: string): SkyCondition {
  const find = (category: string) =>
    items.find((i) => i.category === category && i.fcstDate === date && i.fcstTime === time)?.fcstValue;
  const pty = find("PTY");
  if (pty && pty !== "0") return pty === "3" ? "눈" : "비";
  const sky = find("SKY");
  if (sky === "1") return "맑음";
  if (sky === "3") return "구름많음";
  if (sky === "4") return "흐림";
  return "맑음";
}

/** 단기예보 발표시각(02/05/08/11/14/17/20/23). 발표 후 약 10분부터 제공. */
const KMA_BASE_TIMES = ["0200", "0500", "0800", "1100", "1400", "1700", "2000", "2300"] as const;

function kstParts(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return {
    date: `${get("year")}${get("month")}${get("day")}`,
    hour: Number(get("hour")),
    minute: Number(get("minute")),
  };
}

export function resolveKmaBaseDateTime(targetDateYmd: string, fixedBaseTime?: string): {
  baseDate: string;
  baseTime: string;
} {
  if (fixedBaseTime) {
    return { baseDate: targetDateYmd, baseTime: fixedBaseTime };
  }
  const kst = kstParts();
  const minutes = kst.hour * 60 + kst.minute;
  let chosen: string = KMA_BASE_TIMES[0];
  let baseDate = kst.date;
  for (const t of KMA_BASE_TIMES) {
    const h = Number(t.slice(0, 2));
    if (minutes >= h * 60 + 10) chosen = t;
  }
  if (minutes < 2 * 60 + 10) {
    const d = new Date(`${kst.date.slice(0, 4)}-${kst.date.slice(4, 6)}-${kst.date.slice(6, 8)}T12:00:00+09:00`);
    d.setDate(d.getDate() - 1);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    baseDate = `${y}${m}${day}`;
    chosen = "2300";
  }
  if (targetDateYmd !== kst.date) {
    return { baseDate: targetDateYmd, baseTime: "0800" };
  }
  return { baseDate, baseTime: chosen };
}

export class KmaVilageForecastProvider implements WeatherProvider {
  private lastGood?: WeatherProviderResult;

  constructor(private fallback: WeatherProvider = new MockWeatherProvider()) {}

  async getWeather(params: {
    targetDate: string;
    mode: ForecastMode;
    targetTime?: string;
  }): Promise<WeatherProviderResult> {
    validateIsoDate(params.targetDate);
    const serviceKey = process.env.KMA_SERVICE_KEY;
    const baseUrl =
      process.env.KMA_API_BASE_URL ??
      "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst";
    if (!serviceKey) {
      const fallback = await this.fallback.getWeather(params);
      return {
        ...fallback,
        apiStatus: "missing_api_key",
        message: "KMA_SERVICE_KEY가 없어 기상청 API를 호출하지 않았습니다. 모의 데이터를 사용합니다.",
      };
    }

    try {
      const targetYmd = params.targetDate.replaceAll("-", "");
      const { baseDate, baseTime } = resolveKmaBaseDateTime(
        targetYmd,
        process.env.KMA_BASE_TIME || undefined,
      );
      const nx = process.env.KMA_NX ?? "98";
      const ny = process.env.KMA_NY ?? "76";
      // serviceKey는 URLSearchParams 재인코딩을 피하기 위해 수동 조립
      const qs = [
        `serviceKey=${serviceKey}`,
        "pageNo=1",
        "numOfRows=1000",
        "dataType=JSON",
        `base_date=${baseDate}`,
        `base_time=${baseTime}`,
        `nx=${encodeURIComponent(nx)}`,
        `ny=${encodeURIComponent(ny)}`,
      ].join("&");
      const response = await fetch(`${baseUrl}?${qs}`);
      if (!response.ok) throw new Error(`기상청 API HTTP 오류: ${response.status}`);
      const json = (await response.json()) as {
        response?: { body?: { items?: { item?: KmaItem[] } }; header?: { resultMsg?: string } };
      };
      const items = json.response?.body?.items?.item;
      if (!items?.length) {
        throw new Error(json.response?.header?.resultMsg ?? "기상청 응답에 예보 항목이 없습니다.");
      }

      const forecasts = FORECAST_TIME_SLOTS.map((slot): WeatherForecast | null => {
        const fcstTime = slot.replace(":", "");
        const find = (category: string) =>
          items.find((i) => i.category === category && i.fcstDate === targetYmd && i.fcstTime === fcstTime)?.fcstValue;
        const tmpRaw = find("TMP") ?? find("T1H");
        if (tmpRaw == null) return null;
        const temp = Number(tmpRaw);
        const pop = Number(find("POP") ?? 0);
        const reh = Number(find("REH") ?? 60);
        const wsd = Number(find("WSD") ?? 0);
        const pcpRaw = find("PCP") ?? "0";
        const precipitation = pcpRaw.includes("강수없음") ? 0 : Number(pcpRaw.replace(/[^0-9.]/g, "")) || 0;
        return {
          forecastId: `KMA-${baseDate}-${fcstTime}`,
          mode: params.mode,
          locationName: "광안리 해수욕장",
          baseTime: `${params.targetDate}T${baseTime.slice(0, 2)}:00:00.000Z`,
          targetTime: `${params.targetDate}T${slot}:00.000Z`,
          temperatureCelsius: temp,
          feelsLikeCelsius: feelsLike(temp, reh, wsd),
          precipitationProbability: pop,
          precipitationMm: precipitation,
          skyCondition: skyFromKma(items, targetYmd, fcstTime),
          humidityPercent: reh,
          windSpeedMetersPerSecond: wsd,
          windDirectionDegrees: Number(find("VEC") ?? 0),
          weatherAlerts: [] as string[],
          source: "kma_vilage_fcst",
          dataQuality: "forecast",
        };
      }).filter((f): f is WeatherForecast => f != null);

      if (!forecasts.length) {
        throw new Error("기상청 응답에서 유효한 시간대 예보를 찾지 못했습니다.");
      }

      const preferHour = Number((params.targetTime ?? `${String(kstParts().hour).padStart(2, "0")}:00`).slice(0, 2));
      const current =
        forecasts.find((f) => Number(f.targetTime.slice(11, 13)) >= preferHour) ?? forecasts[0];

      const observation: WeatherObservation = {
        weatherId: `KMA-OBS-${baseDate}`,
        mode: params.mode,
        locationName: "광안리 해수욕장",
        observedAt: current.targetTime,
        temperatureCelsius: current.temperatureCelsius,
        feelsLikeCelsius: current.feelsLikeCelsius,
        precipitationMm: current.precipitationMm,
        precipitationProbability: current.precipitationProbability,
        skyCondition: current.skyCondition,
        humidityPercent: current.humidityPercent,
        windSpeedMetersPerSecond: current.windSpeedMetersPerSecond,
        windDirectionDegrees: current.windDirectionDegrees,
        weatherAlerts: current.weatherAlerts,
        source: "kma_vilage_fcst",
        dataQuality: "forecast",
      };
      this.lastGood = {
        observation,
        forecasts,
        apiStatus: "connected",
        lastSuccessfulAt: new Date().toISOString(),
        message: "기상청 단기예보 조회서비스(getVilageFcst) 응답을 사용했습니다.",
      };
      return this.lastGood;
    } catch (error) {
      if (this.lastGood) {
        return {
          ...this.lastGood,
          apiStatus: "failed",
          observation: {
            ...this.lastGood.observation,
            source: "last_known_good",
            dataQuality: "stale",
          },
          forecasts: this.lastGood.forecasts.map((f) => ({
            ...f,
            source: "last_known_good",
            dataQuality: "stale",
          })),
          message: `기상청 API 호출 실패: ${error instanceof Error ? error.message : String(error)}. 마지막 정상 데이터를 표시합니다.`,
        };
      }
      const fallback = await this.fallback.getWeather(params);
      return {
        ...fallback,
        apiStatus: "failed",
        message: `기상청 API 호출 실패: ${error instanceof Error ? error.message : String(error)}. 모의 데이터를 사용합니다.`,
      };
    }
  }
}

export function describeWeatherKey(weather: WeatherObservation | WeatherForecast): string {
  return weatherKeyFromConditions(
    weather.skyCondition,
    weather.temperatureCelsius,
    weather.weatherAlerts,
  );
}
