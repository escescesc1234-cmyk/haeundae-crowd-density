/**
 * SK open API — 지오비전 퍼즐 「장소 혼잡도」 보조 소스
 * 밀도 판정 본선(CCTV/수동)을 대체하지 않고, 예측·사전알림 가중치로만 사용
 *
 * Free 요금제는 월 호출 한도가 매우 작으므로 캐시·쿨다운으로 호출을 줄입니다.
 */

import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export type TelecomApiStatus =
  | "connected"
  | "mock"
  | "idle"
  | "failed"
  | "missing_api_key"
  | "not_subscribed"
  | "quota_exceeded"
  | "cached";

export type CongestionLevelLabel = "여유" | "보통" | "혼잡" | "매우 혼잡" | "알수없음";

export interface TelecomPlaceCongestion {
  zoneId: string;
  zoneName: string;
  poiId: string;
  poiName: string;
  lat: number;
  lng: number;
  /** 1㎡당 추정 방문자 수 (SK 혼잡도 정의) */
  congestionPerSquareMeter: number | null;
  congestionLevel: 1 | 2 | 3 | 4 | null;
  congestionLabel: CongestionLevelLabel;
  /** 예측 가중치용 배수 (여유~매우혼잡) */
  forecastMultiplier: number;
  measuredAt: string;
  source: "sk_puzzle_place" | "mock";
  raw?: unknown;
}

export interface TelecomProviderResult {
  places: TelecomPlaceCongestion[];
  apiStatus: TelecomApiStatus;
  lastSuccessfulAt?: string;
  message: string;
}

interface PoiMapping {
  zoneId: string;
  zoneName: string;
  poiId: string;
  poiName: string;
  lat: number;
  lng: number;
}

interface MappingFile {
  provider: string;
  notes?: string[];
  places: PoiMapping[];
}

/** 기본 10분 캐시 — Free 한도 보호 */
const CACHE_TTL_MS = Number(process.env.SK_TELECOM_CACHE_MS ?? 10 * 60 * 1000);
/** 429 이후 재시도 금지 시간 (기본 6시간) */
const QUOTA_COOLDOWN_MS = Number(process.env.SK_TELECOM_QUOTA_COOLDOWN_MS ?? 6 * 60 * 60 * 1000);

function levelLabel(level: number | null): CongestionLevelLabel {
  if (level === 1) return "여유";
  if (level === 2) return "보통";
  if (level === 3) return "혼잡";
  if (level === 4) return "매우 혼잡";
  return "알수없음";
}

function levelMultiplier(level: number | null): number {
  if (level === 1) return 0.92;
  if (level === 2) return 1.0;
  if (level === 3) return 1.12;
  if (level === 4) return 1.22;
  return 1;
}

function loadPoiMappings(): PoiMapping[] {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
  const path = join(root, "config", "telecom.gwangalli.json");
  if (!existsSync(path)) return [];
  const json = JSON.parse(readFileSync(path, "utf8")) as MappingFile;
  // 중심 1지점만 사용 (설정에 여러 개가 있어도 첫 항목)
  return (json.places ?? []).slice(0, 1);
}

function mockPlace(p: PoiMapping): TelecomPlaceCongestion {
  const hour = new Date().getHours();
  const level = (hour >= 13 && hour <= 16 ? 3 : hour >= 11 && hour <= 17 ? 2 : 1) as 1 | 2 | 3 | 4;
  const perSq =
    level === 1 ? 0.015 : level === 2 ? 0.035 : level === 3 ? 0.12 : 0.35;
  return {
    zoneId: p.zoneId,
    zoneName: p.zoneName,
    poiId: p.poiId,
    poiName: p.poiName,
    lat: p.lat,
    lng: p.lng,
    congestionPerSquareMeter: perSq,
    congestionLevel: level,
    congestionLabel: levelLabel(level),
    forecastMultiplier: levelMultiplier(level),
    measuredAt: new Date().toISOString(),
    source: "mock",
  };
}

function pickNumber(...values: unknown[]): number | null {
  for (const v of values) {
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) {
      return Number(v);
    }
  }
  return null;
}

function parseRealtimePayload(json: unknown, p: PoiMapping): TelecomPlaceCongestion {
  const root = (json ?? {}) as Record<string, unknown>;
  const status = (root.status ?? {}) as Record<string, unknown>;
  const code = String(status.code ?? "");
  if (code && code !== "00") {
    throw new Error(String(status.message ?? `SK API 오류 코드 ${code}`));
  }

  const contents = (root.contents ?? {}) as Record<string, unknown>;
  const rltmNode = contents.rltm ?? root.rltm ?? contents;
  const nested = (Array.isArray(rltmNode) ? rltmNode[0] : rltmNode) as Record<string, unknown>;
  if (!nested || typeof nested !== "object") {
    throw new Error("SK 응답에 혼잡도(rltm) 데이터가 없습니다.");
  }

  const congestion = pickNumber(
    nested.congestion,
    nested.congestionAvg,
    nested.value,
  );
  let level = pickNumber(
    nested.congestionLevel,
    nested.level,
    nested.congestionLvl,
  );
  if (level != null) level = Math.max(1, Math.min(4, Math.round(level)));
  if (level == null && congestion != null) {
    if (congestion < 0.025) level = 1;
    else if (congestion < 0.05) level = 2;
    else if (congestion < 0.3) level = 3;
    else level = 4;
  }
  const typedLevel = (level as 1 | 2 | 3 | 4 | null) ?? null;
  const poiName =
    typeof contents.poiName === "string" && contents.poiName
      ? contents.poiName
      : p.poiName;

  return {
    zoneId: p.zoneId,
    zoneName: p.zoneName,
    poiId: p.poiId,
    poiName,
    lat: p.lat,
    lng: p.lng,
    congestionPerSquareMeter: congestion,
    congestionLevel: typedLevel,
    congestionLabel: levelLabel(typedLevel),
    forecastMultiplier: levelMultiplier(typedLevel),
    measuredAt: new Date().toISOString(),
    source: "sk_puzzle_place",
    raw: json,
  };
}

function isQuotaError(status: number, body: string): boolean {
  return status === 429 || body.includes("QUOTA_EXCEEDED") || body.includes("Limit Exceeded");
}

export class MockTelecomProvider {
  async getCongestion(): Promise<TelecomProviderResult> {
    const places = loadPoiMappings().map(mockPlace);
    return {
      places,
      apiStatus: "mock",
      lastSuccessfulAt: new Date().toISOString(),
      message:
        "SK open API 키가 없거나 개발용 모의 혼잡도를 사용합니다. 밀도 판정 본선에는 사용하지 않습니다.",
    };
  }
}

/** 화면 새로고침·예측 기본 경로에서는 SK를 호출하지 않음. live:true 일 때만 실호출 */
export class SkPuzzlePlaceCongestionProvider {
  private lastGood?: TelecomProviderResult;
  private cache?: { at: number; result: TelecomProviderResult };
  private quotaBlockedUntil = 0;

  constructor(private fallback = new MockTelecomProvider()) {}

  async getCongestion(options?: {
    /** true일 때만 SK open API 실호출 (테스트/수동 연결) */
    live?: boolean;
    forceRefresh?: boolean;
  }): Promise<TelecomProviderResult> {
    if (!options?.live) {
      const fallback = await this.fallback.getCongestion();
      return {
        ...fallback,
        apiStatus: "idle",
        message:
          "SK 장소 혼잡도 API는 기본 비활성입니다. 관리자 운영 도구에서 「SK API 테스트 연결」을 눌렀을 때만 호출합니다.",
      };
    }

    const appKey = process.env.SK_OPEN_API_APP_KEY?.trim();
    const baseUrl =
      process.env.SK_PUZZLE_PLACE_BASE_URL ??
      "https://apis.openapi.sk.com/puzzle/place/congestion/rltm/pois";
    if (!appKey) {
      const fallback = await this.fallback.getCongestion();
      return {
        ...fallback,
        apiStatus: "missing_api_key",
        message:
          "SK_OPEN_API_APP_KEY가 없어 통신사 혼잡도 API를 호출하지 않았습니다. 모의 데이터를 사용합니다.",
      };
    }

    const mappings = loadPoiMappings();
    if (!mappings.length) {
      return {
        places: [],
        apiStatus: "failed",
        message: "config/telecom.gwangalli.json 에 POI 매핑이 없습니다.",
      };
    }

    const now = Date.now();
    if (!options?.forceRefresh && this.cache && now - this.cache.at < CACHE_TTL_MS) {
      return {
        ...this.cache.result,
        apiStatus:
          this.cache.result.apiStatus === "connected" ? "cached" : this.cache.result.apiStatus,
        message: `${this.cache.result.message} (캐시 ${Math.round(CACHE_TTL_MS / 60000)}분)`,
      };
    }

    if (!options?.forceRefresh && now < this.quotaBlockedUntil) {
      const remainMin = Math.ceil((this.quotaBlockedUntil - now) / 60000);
      if (this.lastGood) {
        return {
          ...this.lastGood,
          apiStatus: "quota_exceeded",
          message: `SK API 호출 한도(QUOTA_EXCEEDED)로 ${remainMin}분간 재호출을 중단합니다. 마지막 정상 데이터를 사용합니다.`,
        };
      }
      const fallback = await this.fallback.getCongestion();
      return {
        ...fallback,
        apiStatus: "quota_exceeded",
        message: `SK API 호출 한도(QUOTA_EXCEEDED)로 ${remainMin}분간 재호출을 중단합니다. Free 요금제는 월 호출 한도가 작습니다. 모의 데이터를 사용합니다.`,
      };
    }

    try {
      // 광안리 중심 1회만 호출
      const p = mappings[0];
      const url = new URL(`${baseUrl}/${encodeURIComponent(p.poiId)}`);
      url.searchParams.set("lat", String(p.lat));
      url.searchParams.set("lng", String(p.lng));
      const response = await fetch(url, {
        headers: {
          Accept: "application/json",
          appKey,
        },
      });
      const text = await response.text();

      if (isQuotaError(response.status, text)) {
        this.quotaBlockedUntil = now + QUOTA_COOLDOWN_MS;
        if (this.lastGood) {
          const result: TelecomProviderResult = {
            ...this.lastGood,
            apiStatus: "quota_exceeded",
            message:
              "SK API 호출 한도 초과(429 QUOTA_EXCEEDED). Free 요금제는 호출 횟수가 매우 적습니다. 잠시 후 다시 시도하거나 Basic으로 업그레이드하세요. 마지막 정상 데이터를 사용합니다.",
          };
          this.cache = { at: now, result };
          return result;
        }
        const fallback = await this.fallback.getCongestion();
        const result: TelecomProviderResult = {
          ...fallback,
          apiStatus: "quota_exceeded",
          message:
            "SK API 호출 한도 초과(429 QUOTA_EXCEEDED). 화면을 새로고침할 때마다 호출하지 않도록 캐시를 적용했습니다. 한도 회복 전까지 모의 데이터를 사용합니다.",
        };
        this.cache = { at: now, result };
        return result;
      }

      if (response.status === 403) {
        const fallback = await this.fallback.getCongestion();
        return {
          ...fallback,
          apiStatus: "not_subscribed",
          message:
            "SK open API가 403을 반환했습니다. 대시보드에서 「장소 혼잡도」 상품 구독·요금제를 확인하세요. 모의 데이터로 대체합니다.",
        };
      }

      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${text.slice(0, 160)}`);
      }

      const json = JSON.parse(text) as unknown;
      const place = parseRealtimePayload(json, p);
      this.lastGood = {
        places: [place],
        apiStatus: "connected",
        lastSuccessfulAt: new Date().toISOString(),
        message: `SK 장소 혼잡도(중심 ${p.lat}, ${p.lng})를 보조 소스로 사용합니다.`,
      };
      this.cache = { at: now, result: this.lastGood };
      this.quotaBlockedUntil = 0;
      return this.lastGood;
    } catch (error) {
      if (this.lastGood) {
        return {
          ...this.lastGood,
          apiStatus: "failed",
          message: `통신사 혼잡도 호출 실패: ${error instanceof Error ? error.message : String(error)}. 마지막 정상 데이터를 표시합니다.`,
        };
      }
      const fallback = await this.fallback.getCongestion();
      return {
        ...fallback,
        apiStatus: "failed",
        message: `통신사 혼잡도 호출 실패: ${error instanceof Error ? error.message : String(error)}. 모의 데이터를 사용합니다.`,
      };
    }
  }
}

export const sharedTelecomProvider = new SkPuzzlePlaceCongestionProvider();
