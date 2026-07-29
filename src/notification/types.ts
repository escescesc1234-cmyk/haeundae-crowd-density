/**
 * 실시간 안전 알림 시스템 — 타입 정의
 */

import type {
  DensityAnalysisResult,
  DensityTrend,
  RiskLevel,
  ZoneType,
} from "../types/index.js";

export type StableRiskLevel = Extract<RiskLevel, "안전" | "혼잡" | "위험">;

/** 0=정보, 1=혼잡, 2=임계, 3=위험, 4=즉각복합, system=시스템경고 */
export type NotificationPriority =
  | 0
  | 1
  | 2
  | 3
  | 4
  | "system";

export type AlertEventStatus =
  | "생성됨"
  | "발송됨"
  | "관리자 확인"
  | "현장 확인 중"
  | "대응 중"
  | "해결됨"
  | "오경보"
  | "자동 종료";

export type NotificationChannelType =
  | "in_app_banner"
  | "push"
  | "map_overlay"
  | "admin_dashboard"
  | "admin_sound"
  | "broadcast"
  | "sms"
  | "system";

export type RecipientType = "tourist" | "manager";

export type ManagerRole =
  | "zone_manager"
  | "field_supervisor"
  | "operations_admin"
  | "safety_officer"
  | "rescue_staff"
  | "system_admin"
  | "traffic_controller";

export type TouristLocationConsent = "granted" | "denied" | "unknown";

export type DataFreshnessStatus = "fresh" | "stale" | "missing";

export interface NotificationPolicy {
  sameLevelCooldownSeconds: number;
  minLevelDurationSeconds: number;
  dangerRepeatIntervalSeconds: number;
  managerAckTimeoutSeconds: number;
  downgradeConfirmSeconds: number;
  minDensityChangeForResend: number;
  maxRetryAttempts: number;
  retryIntervalSeconds: number;
  touristProximityRadiusMeters: number;
  adjacentZoneRadiusMeters: number;
  rapidRisePerSecond: number;
  emergencyDensityMultiplier: number;
  staleDataSeconds: number;
  minimumConfidence: number;
  disclaimer?: string;
}

export interface NotificationAnalysisInput {
  zoneId: string;
  zoneName: string;
  zoneType: ZoneType;
  detectedPeople: number | null;
  effectiveAreaSquareMeters: number;
  rawDensity: number | null;
  adjustedDensity: number | null;
  currentRiskLevel: RiskLevel;
  previousRiskLevel: RiskLevel | null;
  thresholds: {
    congestionStartDensity: number;
    criticalDensity: number;
    highRiskDensity: number;
  };
  criticalDensityReached: boolean;
  approachingHighRisk: boolean;
  densityTrend: DensityTrend;
  densityIncreaseRatePerSecond: number;
  currentLevelDurationSeconds: number;
  confidence: number | null;
  measuredAt: string;
  dataFreshness: DataFreshnessStatus;
  lowConfidence: boolean;
  additionalRiskFactors: string[];
  managerConfirmStatus: string;
  nearbyTouristCount: number;
  reason: string;
  errors: string[];
  warnings: string[];
  densityBasedSafeButOtherRisks: boolean;
  suddenScatterOrCollapse: boolean;
  isTestData: boolean;
  manualCongestionBroadcast?: boolean;
}

export interface TouristUserProfile {
  userId: string;
  locationConsent: TouristLocationConsent;
  pushEnabled: boolean;
  currentZoneId?: string | null;
  latitude?: number;
  longitude?: number;
  headingTowardZoneId?: string | null;
  favoriteZoneIds?: string[];
  plannedVisit?: boolean;
  insideRiskZone?: boolean;
}

export interface ManagerUserProfile {
  managerId: string;
  name: string;
  role: ManagerRole;
  assignedZoneIds: string[];
  pushEnabled: boolean;
  onDuty: boolean;
}

export interface ZoneGeoHint {
  zoneId: string;
  latitude: number;
  longitude: number;
  adjacentZoneIds: string[];
}

export interface NotificationTriggerReason {
  code: string;
  message: string;
}

export interface AlertEvent {
  eventId: string;
  zoneId: string;
  zoneName: string;
  previousRiskLevel: RiskLevel | null;
  currentRiskLevel: RiskLevel;
  adjustedDensity: number | null;
  thresholds: NotificationAnalysisInput["thresholds"];
  criticalDensityReached: boolean;
  densityTrend: DensityTrend | string;
  densityIncreaseRatePerSecond: number;
  confidence: number | null;
  additionalRiskFactors: string[];
  touristNotificationRequired: boolean;
  managerNotificationRequired: boolean;
  priority: NotificationPriority;
  status: AlertEventStatus;
  triggerReasons: NotificationTriggerReason[];
  isSystemWarning: boolean;
  isTestData: boolean;
  parentEventId?: string;
  escalationLevel: number;
  createdAt: string;
  updatedAt: string;
  resolvedAt?: string;
  acknowledgedBy?: string;
  acknowledgedAt?: string;
}

export interface GeneratedMessage {
  title: string;
  body: string;
  actionHint?: string;
  safeAlternative?: string;
  channels: NotificationChannelType[];
  soundAndVibration?: boolean;
  priority: NotificationPriority;
}

export interface TouristNotificationMessage extends GeneratedMessage {
  audience: "tourist";
  zoneId: string;
  zoneName: string;
  riskLevel: RiskLevel;
  updatedAt: string;
  showDensity?: number | null;
}

export interface ManagerNotificationMessage extends GeneratedMessage {
  audience: "manager";
  zoneId: string;
  zoneName: string;
  riskLevel: RiskLevel;
  detectedPeople: number | null;
  effectiveAreaSquareMeters: number;
  rawDensity: number | null;
  adjustedDensity: number | null;
  thresholds: NotificationAnalysisInput["thresholds"];
  criticalDensityReached: boolean;
  trend: DensityTrend | string;
  densityIncreaseRatePerSecond: number;
  confidence: number | null;
  additionalRiskFactors: string[];
  aiReason: string;
  recommendedActions: string[];
  cctvLink?: string;
  touristAlertSent: boolean;
  actions: {
    acknowledge: true;
    startResponse: true;
    resolve: true;
    falseAlarm: true;
  };
}

export interface NotificationDeliveryRecord {
  deliveryId: string;
  eventId: string;
  recipientType: RecipientType;
  recipientId: string;
  messageTitle: string;
  messageBody: string;
  channel: NotificationChannelType;
  sentAt: string;
  success: boolean;
  failureReason?: string;
  read: boolean;
  managerAcknowledged: boolean;
  resendCount: number;
}

export interface NotificationEvaluationResult {
  shouldNotify: boolean;
  isDuplicate: boolean;
  isSystemWarning: boolean;
  priority: NotificationPriority;
  touristRequired: boolean;
  managerRequired: boolean;
  triggerReasons: NotificationTriggerReason[];
  updateExistingEventId?: string;
  suppressReason?: string;
}

export interface ProcessNotificationResult {
  analysisInput: NotificationAnalysisInput;
  evaluation: NotificationEvaluationResult;
  event?: AlertEvent;
  touristMessages: TouristNotificationMessage[];
  managerMessages: ManagerNotificationMessage[];
  touristDeliveries: NotificationDeliveryRecord[];
  managerDeliveries: NotificationDeliveryRecord[];
  escalations: AlertEvent[];
}

export interface NotificationChannel {
  type: NotificationChannelType;
  send(payload: {
    recipientId: string;
    recipientType: RecipientType;
    title: string;
    body: string;
    priority: NotificationPriority;
    eventId: string;
    soundAndVibration?: boolean;
  }): Promise<{ success: boolean; failureReason?: string }>;
}

export type DensityResultWithContext = DensityAnalysisResult & {
  previousRiskLevel?: RiskLevel | null;
  currentLevelDurationSeconds?: number;
  densityIncreaseRatePerSecond?: number;
  dataFreshness?: DataFreshnessStatus;
  managerConfirmStatus?: string;
  nearbyTouristCount?: number;
  suddenScatterOrCollapse?: boolean;
  manualCongestionBroadcast?: boolean;
};
