import type { AdminViewModel, DensityAnalysisResult } from "../types/index.js";

export function toAdminPayload(result: DensityAnalysisResult): AdminViewModel {
  return result.adminSummary;
}

export function toAdminList(results: DensityAnalysisResult[]): AdminViewModel[] {
  return results.map(toAdminPayload);
}

export interface AlertDispatchRequest {
  zoneId: string;
  message: string;
  sentBy: string;
  at?: string;
}

export interface FalsePositiveRecord {
  zoneId: string;
  reportedRiskLevel: string;
  actualNote: string;
  recordedBy: string;
  at?: string;
}

export function createAlertDispatch(req: AlertDispatchRequest) {
  return {
    ok: true,
    type: "manager_alert" as const,
    zoneId: req.zoneId,
    message: req.message,
    sentBy: req.sentBy,
    at: req.at ?? new Date().toISOString(),
    note: "실제 경보 채널(SMS/앱) 연동은 운영 환경에서 구성합니다.",
  };
}

export function createFalsePositiveRecord(req: FalsePositiveRecord) {
  return {
    ok: true,
    type: "false_positive" as const,
    zoneId: req.zoneId,
    reportedRiskLevel: req.reportedRiskLevel,
    actualNote: req.actualNote,
    recordedBy: req.recordedBy,
    at: req.at ?? new Date().toISOString(),
  };
}
