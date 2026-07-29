/**
 * 모듈 public API — AI LAB 안전 관리 앱에서 import하여 사용
 */

export { calculateRawDensity } from "./density/calculator.js";
export {
  analyzeZoneDensity,
  createInitialZoneState,
} from "./density/engine.js";
export {
  applyHysteresis,
  classifyByDensityOnly,
} from "./density/hysteresis.js";
export { computeAdjustedDensity, computeTrend } from "./density/smoothing.js";
export { deduplicateCameraDetections } from "./density/deduplication.js";
export {
  loadThresholds,
  saveThresholds,
  validateThresholdOrder,
  resolveZoneThresholds,
  DEFAULT_THRESHOLDS,
} from "./config/thresholds.js";
export {
  loadZoneCatalog,
  getZoneById,
  computeEffectiveArea,
} from "./zone/zoneService.js";
export { CrowdDensityService, sharedService } from "./service/crowdDensityService.js";
export { toManualDensityInput } from "./adapters/manualAdapter.js";
export { toCctvDensityInput } from "./adapters/cctvAdapter.js";
export {
  runVisionAnalyze,
  toVisionDensityInput,
  toVisionBridgePayload,
  defaultVisionScreenshot,
} from "./adapters/visionAdapter.js";
export type {
  VisionAnalyzeRequest,
  VisionAnalyzeResult,
  VisionBridgePayload,
} from "./adapters/visionAdapter.js";
export {
  VISION_TOURIST_DANGER_MESSAGE,
  VISION_MANAGER_DANGER_MESSAGE,
  normalizeVisionAlerts,
  alertsFromDensityGrid,
} from "./adapters/visionSafetyAlerts.js";
export type { VisionSafetyAlerts } from "./adapters/visionSafetyAlerts.js";
export { createApp } from "./api/server.js";
export {
  DensityApiClient,
  createDensityApiClient,
} from "./client/densityApiClient.js";
export {
  DensityApiError,
  visionOutputUrl,
} from "./client/densityApiTypes.js";
export type {
  AdminZoneView,
  DensityAnalysisResult as DensityApiAnalysisResult,
  DensityClientOptions,
  DensityHealthResponse,
  ManualAnalyzeRequest,
  TouristZoneView,
  VisionAnalyzeMeta,
  VisionAnalyzeRequest,
  VisionAnalyzeResponse,
  VisionSafetyAlerts as DensityApiVisionAlerts,
} from "./client/densityApiTypes.js";
export {
  NotificationService,
  sharedNotificationService,
  evaluateNotificationEvent,
  determineNotificationPriority,
  generateTouristMessage,
  generateManagerMessage,
  selectTouristRecipients,
  selectManagerRecipients,
  sendNotification,
  acknowledgeManagerAlert,
  escalateUnacknowledgedAlert,
  resolveAlertEvent,
} from "./notification/index.js";
export {
  ForecastService,
  sharedForecastService,
  KmaVilageForecastProvider,
  MockWeatherProvider,
  WeightedVisitorForecastModel,
  ForecastDataStore,
  loadForecastPolicy,
  validateIsoDate,
  compareDateByYear,
} from "./forecast/index.js";
export type * from "./types/index.js";
export type * from "./notification/types.js";
export type * from "./forecast/types.js";
