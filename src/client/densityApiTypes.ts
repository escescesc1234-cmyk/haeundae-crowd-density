/**
 * 해운대/광안리 군중밀도 HTTP API 계약 타입.
 * 필드명은 서버 응답과 동일하게 유지한다 (엔진 재구현 금지).
 */

export type RiskLevel =
  | "안전"
  | "혼잡"
  | "위험"
  | "주의"
  | "데이터없음"
  | string;

export interface DensityHealthResponse {
  ok: boolean;
  service: string;
  disclaimer?: string;
}

export interface ManualAnalyzeRequest {
  zoneId: string;
  detectedPeople: number;
  measuredAt: string;
  notify?: boolean;
  skipHysteresis?: boolean;
  effectiveAreaSquareMeters?: number;
  confidence?: number;
  dataSource?: string;
  isTestData?: boolean;
}

/** 서버 DensityAnalysisResult 핵심 필드 (전체 필드는 서버가 반환) */
export interface DensityAnalysisResult {
  zoneId: string;
  zoneName: string;
  riskLevel: RiskLevel;
  detectedPeople: number | null;
  rawDensity: number | null;
  adjustedDensity: number | null;
  measuredAt: string;
  dataSource?: string;
  touristSummary?: Record<string, unknown>;
  adminSummary?: Record<string, unknown>;
  recommendedActions?: string[];
  warnings?: string[];
  errors?: string[];
  [key: string]: unknown;
}

export interface TouristZoneView {
  zoneId: string;
  zoneName: string;
  riskLevel: RiskLevel;
  riskLabel?: string;
  congestionHint?: string;
  recommendedAction?: string;
  colorCode?: string;
  updatedAt?: string;
  [key: string]: unknown;
}

export interface AdminZoneView {
  zoneId: string;
  zoneName: string;
  riskLevel: RiskLevel;
  detectedPeople: number | null;
  rawDensity: number | null;
  adjustedDensity: number | null;
  effectiveAreaSquareMeters?: number;
  reason?: string;
  [key: string]: unknown;
}

export interface VisionAnalyzeRequest {
  imagePath: string;
  zoneId?: string;
  skipHysteresis?: boolean;
  notify?: boolean;
  calibrationPath?: string;
  useHomographyArea?: boolean;
}

export interface VisionSafetyAlerts {
  hasDanger: boolean;
  dangerCellCount: number;
  touristMessage: string | null;
  managerMessage: string | null;
}

export interface VisionAnalyzeMeta {
  safetyMapRelativePath?: string;
  heatmapRelativePath?: string;
  roiPersonCount?: number;
  maxGridDensityPerM2?: number;
  [key: string]: unknown;
}

export interface VisionAnalyzeResponse {
  ok: boolean;
  analysis: DensityAnalysisResult;
  alerts: VisionSafetyAlerts;
  vision: VisionAnalyzeMeta;
  densityInput?: unknown;
  notification?: unknown;
  disclaimer?: string;
  error?: string;
}

/** GET /api/vision/realtime — 실시간 AI 비전 URL·계약 */
export interface RealtimeVisionMeta {
  ok: boolean;
  service: string;
  modelWeight?: string;
  uiUrl: string;
  streamUrl: string;
  streamYoloUrl?: string;
  streamSahi256Url?: string;
  statusUrl: string;
  modelInfoUrl?: string;
  proxiedStatusPath?: string;
  proxiedModelInfoPath?: string;
  howToStart?: string;
  grid?: Record<string, unknown>;
  alerts?: {
    tourist: string;
    manager: string;
  };
  [key: string]: unknown;
}

export interface DensityClientOptions {
  /** 예: http://localhost:3780 — 비우면 상대 경로(동일 오리진) */
  baseUrl?: string;
  /** POST /api/analyze/manual 기본 타임아웃 (ms) */
  manualTimeoutMs?: number;
  /** POST /api/analyze/vision 기본 타임아웃 (ms) */
  visionTimeoutMs?: number;
  /** GET 계열 기본 타임아웃 (ms) */
  defaultTimeoutMs?: number;
  fetchImpl?: typeof fetch;
}

export class DensityApiError extends Error {
  readonly status: number | null;
  readonly body: unknown;

  constructor(message: string, status: number | null = null, body?: unknown) {
    super(message);
    this.name = "DensityApiError";
    this.status = status;
    this.body = body;
  }
}

/** vision/output/... → /vision-output/... 정적 URL */
export function visionOutputUrl(
  baseUrl: string,
  relativePath: string | null | undefined,
): string | null {
  if (!relativePath) return null;
  const stripped = relativePath
    .replace(/^\/+/, "")
    .replace(/^vision\/output\//, "");
  const root = (baseUrl || "").replace(/\/$/, "");
  return `${root}/vision-output/${stripped}`;
}
