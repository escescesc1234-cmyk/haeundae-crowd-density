/**
 * 해운대/광안리 군중밀도 분석 서비스 HTTP 클라이언트.
 * 밀도·위험등급 공식을 재구현하지 않고 외부(또는 동일) 서버 API만 소비한다.
 */

import {
  DensityApiError,
  type AdminZoneView,
  type DensityAnalysisResult,
  type DensityClientOptions,
  type DensityHealthResponse,
  type ManualAnalyzeRequest,
  type TouristZoneView,
  type RealtimeVisionMeta,
  type RealtimeVisionModelInfo,
  type RealtimeVisionStatus,
  type VisionAnalyzeRequest,
  type VisionAnalyzeResponse,
  visionOutputUrl,
} from "./densityApiTypes.js";

export { DensityApiError, visionOutputUrl } from "./densityApiTypes.js";
export type * from "./densityApiTypes.js";

const DEFAULT_ZONE = "GWANGALLI-ZONE-CENTER";
const CONNECT_FAIL = "밀도 분석 서비스 연결 실패";

function normalizeBase(url: string | undefined): string {
  if (!url) return "";
  return url.replace(/\/$/, "");
}

export class DensityApiClient {
  readonly baseUrl: string;
  readonly manualTimeoutMs: number;
  readonly visionTimeoutMs: number;
  readonly defaultTimeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: DensityClientOptions = {}) {
    this.baseUrl = normalizeBase(
      options.baseUrl ??
        process.env.DENSITY_API_BASE_URL ??
        "http://localhost:3780",
    );
    this.manualTimeoutMs = options.manualTimeoutMs ?? 10_000;
    this.visionTimeoutMs = options.visionTimeoutMs ?? 180_000;
    this.defaultTimeoutMs = options.defaultTimeoutMs ?? 10_000;
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  }

  /** 안전지도/열지도 정적 URL 생성 */
  visionAssetUrl(relativePath: string | null | undefined): string | null {
    return visionOutputUrl(this.baseUrl, relativePath);
  }

  async health(): Promise<DensityHealthResponse> {
    return this.requestJson<DensityHealthResponse>("/api/health", {
      method: "GET",
      timeoutMs: this.defaultTimeoutMs,
    });
  }

  async analyzeManual(
    body: ManualAnalyzeRequest,
  ): Promise<DensityAnalysisResult> {
    return this.requestJson<DensityAnalysisResult>("/api/analyze/manual", {
      method: "POST",
      timeoutMs: this.manualTimeoutMs,
      body: {
        zoneId: body.zoneId || DEFAULT_ZONE,
        detectedPeople: body.detectedPeople,
        measuredAt: body.measuredAt,
        notify: body.notify ?? false,
        skipHysteresis: body.skipHysteresis,
        effectiveAreaSquareMeters: body.effectiveAreaSquareMeters,
        confidence: body.confidence,
        dataSource: body.dataSource,
        isTestData: body.isTestData,
      },
    });
  }

  async getResults(): Promise<DensityAnalysisResult[]> {
    return this.requestJson<DensityAnalysisResult[]>("/api/results", {
      method: "GET",
      timeoutMs: this.defaultTimeoutMs,
    });
  }

  async getTouristZones(): Promise<TouristZoneView[]> {
    return this.requestJson<TouristZoneView[]>("/api/tourist/zones", {
      method: "GET",
      timeoutMs: this.defaultTimeoutMs,
    });
  }

  async getTouristBeach(): Promise<unknown> {
    return this.requestJson("/api/tourist/beach", {
      method: "GET",
      timeoutMs: this.defaultTimeoutMs,
    });
  }

  async getAdminZones(): Promise<AdminZoneView[]> {
    return this.requestJson<AdminZoneView[]>("/api/admin/zones", {
      method: "GET",
      timeoutMs: this.defaultTimeoutMs,
    });
  }

  async analyzeVision(
    body: VisionAnalyzeRequest,
  ): Promise<VisionAnalyzeResponse> {
    return this.requestJson<VisionAnalyzeResponse>("/api/analyze/vision", {
      method: "POST",
      timeoutMs: this.visionTimeoutMs,
      body: {
        imagePath: body.imagePath,
        zoneId: body.zoneId || DEFAULT_ZONE,
        skipHysteresis: body.skipHysteresis ?? true,
        notify: body.notify ?? false,
        calibrationPath: body.calibrationPath,
        useHomographyArea: body.useHomographyArea,
      },
    });
  }

  /** 실시간 AI 비전 URL·모델 계약 (스트림은 meta.streamUrl 직접 사용) */
  async getRealtimeVision(): Promise<RealtimeVisionMeta> {
    return this.requestJson<RealtimeVisionMeta>("/api/vision/realtime", {
      method: "GET",
      timeoutMs: this.defaultTimeoutMs,
    });
  }

  /** 실시간 상태 프록시 (:8790/api/status). 비전 서버 미기동 시 502 */
  async getRealtimeVisionStatus(): Promise<RealtimeVisionStatus> {
    return this.requestJson<RealtimeVisionStatus>(
      "/api/vision/realtime/status",
      {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      },
    );
  }

  /** 로드된 YOLO 가중치 메타 프록시 */
  async getRealtimeVisionModel(): Promise<RealtimeVisionModelInfo> {
    return this.requestJson<RealtimeVisionModelInfo>(
      "/api/vision/realtime/model",
      {
        method: "GET",
        timeoutMs: this.defaultTimeoutMs,
      },
    );
  }

  /**
   * 실시간 status 폴링. 다른 앱 UI에서 setInterval 대신 사용.
   * @returns stop() 호출로 중지
   */
  startRealtimePolling(
    onUpdate: (status: RealtimeVisionStatus) => void,
    options?: {
      intervalMs?: number;
      onError?: (err: DensityApiError) => void;
    },
  ): { stop: () => void } {
    const intervalMs = options?.intervalMs ?? 2_000;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (stopped) return;
      try {
        const status = await this.getRealtimeVisionStatus();
        if (!stopped) onUpdate(status);
      } catch (err) {
        if (!stopped && options?.onError) {
          options.onError(
            err instanceof DensityApiError
              ? err
              : new DensityApiError(String(err), null),
          );
        }
      } finally {
        if (!stopped) {
          timer = setTimeout(() => void tick(), intervalMs);
        }
      }
    };

    void tick();
    return {
      stop: () => {
        stopped = true;
        if (timer) clearTimeout(timer);
      },
    };
  }

  async getWaveguardDashboard(params?: {
    date?: string;
    telecomRefresh?: boolean;
  }): Promise<unknown> {
    const q = new URLSearchParams();
    if (params?.date) q.set("date", params.date);
    if (params?.telecomRefresh != null) {
      q.set("telecomRefresh", String(params.telecomRefresh));
    }
    const qs = q.toString();
    return this.requestJson(
      `/api/waveguard/dashboard${qs ? `?${qs}` : ""}`,
      { method: "GET", timeoutMs: this.defaultTimeoutMs },
    );
  }

  private async requestJson<T>(
    path: string,
    opts: {
      method: string;
      timeoutMs: number;
      body?: unknown;
    },
  ): Promise<T> {
    const url = `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeoutMs);
    try {
      const res = await this.fetchImpl(url, {
        method: opts.method,
        headers:
          opts.body !== undefined
            ? { "Content-Type": "application/json" }
            : undefined,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
      let data: unknown = null;
      const text = await res.text();
      if (text) {
        try {
          data = JSON.parse(text) as unknown;
        } catch {
          data = text;
        }
      }
      if (!res.ok) {
        const errMsg =
          data &&
          typeof data === "object" &&
          data !== null &&
          "error" in data &&
          typeof (data as { error: unknown }).error === "string"
            ? (data as { error: string }).error
            : `${CONNECT_FAIL} (HTTP ${res.status})`;
        throw new DensityApiError(errMsg, res.status, data);
      }
      return data as T;
    } catch (err) {
      if (err instanceof DensityApiError) throw err;
      if (err instanceof Error && err.name === "AbortError") {
        throw new DensityApiError(
          `${CONNECT_FAIL} (타임아웃 ${opts.timeoutMs}ms)`,
          null,
        );
      }
      throw new DensityApiError(
        `${CONNECT_FAIL}: ${err instanceof Error ? err.message : String(err)}`,
        null,
      );
    } finally {
      clearTimeout(timer);
    }
  }
}

/** 환경변수 DENSITY_API_BASE_URL 기준 기본 클라이언트 */
export function createDensityApiClient(
  options: DensityClientOptions = {},
): DensityApiClient {
  return new DensityApiClient(options);
}
