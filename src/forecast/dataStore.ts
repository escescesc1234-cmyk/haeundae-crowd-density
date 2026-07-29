import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type {
  BeachEvent,
  HistoricalWeather,
  HourlyVisitorHistory,
} from "./types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..");
const DATA_DIR = join(ROOT, "data", "forecast");

function readJson<T>(path: string, fallback: T): T {
  if (!existsSync(path)) return fallback;
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

export class ForecastDataStore {
  private weather = readJson<HistoricalWeather[]>(
    join(DATA_DIR, "historical-weather.json"),
    [],
  );
  private visitors = readJson<HourlyVisitorHistory[]>(
    join(DATA_DIR, "historical-visitors.json"),
    [],
  );
  private events = readJson<BeachEvent[]>(join(DATA_DIR, "events.json"), []);

  listHistoricalWeather(): HistoricalWeather[] {
    return [...this.weather];
  }

  getHistoricalWeather(date: string): HistoricalWeather | undefined {
    return this.weather.find((w) => w.date === date);
  }

  findSimilarWeather(weatherKey: string, beforeDate?: string): HistoricalWeather[] {
    return this.weather.filter(
      (w) =>
        w.similarWeatherKey === weatherKey &&
        (beforeDate === undefined || w.date < beforeDate),
    );
  }

  listVisitorHistory(params?: {
    date?: string;
    zoneId?: string;
    availableUntil?: string;
  }): HourlyVisitorHistory[] {
    return this.visitors.filter((v) => {
      if (params?.date && v.date !== params.date) return false;
      if (params?.zoneId && v.zoneId !== params.zoneId) return false;
      if (params?.availableUntil && v.availableAt > params.availableUntil) return false;
      return true;
    });
  }

  listEvents(params?: { date?: string; zoneId?: string; includeCancelled?: boolean }): BeachEvent[] {
    return this.events.filter((e) => {
      if (params?.date && e.date !== params.date) return false;
      if (params?.zoneId && e.zoneId && e.zoneId !== params.zoneId) return false;
      if (!params?.includeCancelled && e.isCancelled) return false;
      return true;
    });
  }

  hasEnoughVisitorHistory(zoneId: string, beforeDate: string, minimumRows = 3): boolean {
    return this.listVisitorHistory({ zoneId }).filter((v) => v.date < beforeDate).length >= minimumRows;
  }
}

export const sharedForecastDataStore = new ForecastDataStore();
