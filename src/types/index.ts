/**
 * 광안리 해수욕장 군중 밀집도 분석 — 공통 타입 정의
 * 관광객용 안전 지도 / 관리자 모니터링이 동일 모듈 결과를 소비한다.
 */

export type ZoneType =
  | "sand_beach"
  | "shoreline"
  | "swimming"
  | "entrance"
  | "facility"
  | "event"
  | "custom";

export type RiskLevel = "안전" | "혼잡" | "위험" | "오류" | "데이터없음";

export type DensityTrend = "증가" | "감소" | "유지" | "알수없음";

export type DataSource =
  | "manual"
  | "test"
  | "cctv_detection"
  /** YOLOv8 + SAHI 비전 파이프라인 (vision/analyze_for_app.py) */
  | "vision_yolo_sahi"
  /** 부산광역시 교통정보서비스센터 ITS CCTV 프레임 (data.go.kr/15120867) */
  | "busan_its_cctv"
  /** 해운대구 CCTV 정보 조회 API (apis.data.go.kr/3330000/HeaundaeCctvInfoService) */
  | "haeundae_cctv_api"
  /** 부산시설공단 공영주차장 실시간 현황 API (data.go.kr/15157490) */
  | "busan_parking_api"
  | "manager_override";

export type ManagerConfirmStatus =
  | "unconfirmed"
  | "confirmed"
  | "overridden"
  | "requires_review";

export interface DensityThresholds {
  congestionStartDensity: number;
  criticalDensity: number;
  highRiskDensity: number;
  measurementWindowSeconds: number;
  minimumConfidence: number;
  hysteresisMargin: number;
  minDurationSecondsForUpgrade: number;
  minDurationSecondsForDowngrade: number;
  immediateHighRiskMultiplier: number;
  rapidRisePerSecond: number;
  staleDataSeconds: number;
  disclaimer?: string;
}

export interface ThresholdChangeRecord {
  id: string;
  changedAt: string;
  changedBy: string;
  reason: string;
  targetZoneIds: string[] | "all";
  fieldVerified: boolean;
  before: Partial<DensityThresholds>;
  after: Partial<DensityThresholds>;
}

export interface ZoneDefinition {
  zoneId: string;
  zoneName: string;
  zoneType: ZoneType;
  totalAreaSquareMeters: number;
  excludedAreaSquareMeters: number;
  effectiveAreaSquareMeters: number;
  notes?: string;
  /** 구역별 임계값 오버라이드 (없으면 전역 설정 사용) */
  thresholdOverrides?: Partial<DensityThresholds>;
}

export interface AuxiliaryRiskFactors {
  densityIncreaseRatePerSecond?: number;
  directionalFlowRatio?: number;
  entranceCongestion?: boolean;
  opposingCrowdFlow?: boolean;
  stationaryObjectRatio?: number;
  suddenScatterOrCollapse?: boolean;
  waveHeightMeters?: number;
  tideCondition?: string;
  weatherCondition?: string;
  rescueStaffDeployed?: boolean;
  controlledArea?: boolean;
  cctvAnalysisConfidence?: number;
}

export interface DensityInput {
  zoneId: string;
  effectiveAreaSquareMeters?: number;
  detectedPeople?: number | null;
  measuredAt: string;
  confidence?: number;
  dataSource?: DataSource;
  cameraDetections?: CameraDetection[];
  boundaryDetections?: BoundaryDetection[];
  previousDensities?: number[];
  auxiliaryFactors?: AuxiliaryRiskFactors;
  managerConfirmed?: boolean;
  isTestData?: boolean;
}

export interface CameraDetection {
  cameraId: string;
  detectedPeople: number;
  confidence: number;
  trackedObjectIds?: string[];
  measuredAt: string;
}

export interface BoundaryDetection {
  trackId: string;
  zoneIds: string[];
  assignedZoneId: string;
}

export interface ZoneRuntimeState {
  zoneId: string;
  zoneName: string;
  zoneType: ZoneType;
  totalAreaSquareMeters: number;
  effectiveAreaSquareMeters: number;
  currentPeople: number | null;
  rawDensity: number | null;
  adjustedDensity: number | null;
  riskLevel: RiskLevel;
  measuredAt: string | null;
  dataSource: DataSource | null;
  confidence: number | null;
  managerConfirmStatus: ManagerConfirmStatus;
  densityHistory: DensitySample[];
  currentRiskSince?: string;
  pendingRiskLevel?: RiskLevel;
  pendingRiskSince?: string;
  lastManualOverride?: {
    riskLevel: RiskLevel;
    by: string;
    at: string;
    reason: string;
  };
}

export interface DensitySample {
  measuredAt: string;
  rawDensity: number;
  adjustedDensity: number;
  detectedPeople: number;
  confidence: number;
}

export interface ThresholdsUsed {
  congestionStartDensity: number;
  criticalDensity: number;
  highRiskDensity: number;
  hysteresisMargin: number;
  source: "global" | "zone_override";
}

export interface AuxiliaryAlert {
  code: string;
  message: string;
  severity: "info" | "warning" | "critical";
}

export interface DensityAnalysisResult {
  zoneId: string;
  zoneName: string;
  zoneType: ZoneType;
  effectiveAreaSquareMeters: number;
  detectedPeople: number | null;
  rawDensity: number | null;
  adjustedDensity: number | null;
  riskLevel: RiskLevel;
  criticalDensityReached: boolean;
  approachingHighRisk: boolean;
  trend: DensityTrend;
  thresholds: ThresholdsUsed;
  reason: string;
  confidence: number | null;
  lowConfidence: boolean;
  requiresManagerReview: boolean;
  measuredAt: string;
  dataSource: DataSource;
  isTestData: boolean;
  recommendedActions: string[];
  touristSummary: TouristViewModel;
  adminSummary: AdminViewModel;
  auxiliaryAlerts: AuxiliaryAlert[];
  densityBasedSafeButOtherRisks: boolean;
  errors: string[];
  warnings: string[];
  actionsTriggered: string[];
}

export interface TouristViewModel {
  zoneId: string;
  zoneName: string;
  riskLevel: RiskLevel;
  riskLabel: string;
  congestionHint: string;
  recommendedAction: string;
  safeDirectionHint: string;
  colorCode: string;
  icon: string;
  updatedAt: string;
  disclaimer: string;
}

export interface AdminViewModel {
  zoneId: string;
  zoneName: string;
  effectiveAreaSquareMeters: number;
  detectedPeople: number | null;
  rawDensity: number | null;
  adjustedDensity: number | null;
  riskLevel: RiskLevel;
  thresholds: ThresholdsUsed;
  criticalDensityReached: boolean;
  approachingHighRisk: boolean;
  trend: DensityTrend;
  reason: string;
  confidence: number | null;
  requiresManagerReview: boolean;
  densityHistory: DensitySample[];
  auxiliaryAlerts: AuxiliaryAlert[];
  actionsTriggered: string[];
  errors: string[];
  warnings: string[];
  canManualOverride: true;
  canSendAlert: true;
  canRecordFalsePositive: true;
}

export interface AnalysisError {
  code: string;
  message: string;
}
