/**
 * 관리자 확인·에스컬레이션·이벤트 종료
 */

import { parseIsoMs } from "../density/smoothing.js";
import type {
  AlertEvent,
  AlertEventStatus,
  ManagerNotificationMessage,
  ManagerUserProfile,
  NotificationPolicy,
} from "./types.js";
import { generateManagerMessage } from "./managerMessage.js";
import { selectManagerRecipients } from "./recipients.js";
import type { NotificationStore } from "./storage.js";
import type { MockNotificationChannel } from "./channels/mockChannel.js";
import type { NotificationChannelType } from "./types.js";
import { sendNotification } from "./send.js";
import { nextEventId } from "./storage.js";
import type { NotificationAnalysisInput } from "./types.js";

export function acknowledgeManagerAlert(
  store: NotificationStore,
  eventId: string,
  managerId: string,
): AlertEvent | undefined {
  return store.updateEvent(eventId, {
    status: "관리자 확인",
    acknowledgedBy: managerId,
    acknowledgedAt: new Date().toISOString(),
  });
}

export function startManagerResponse(
  store: NotificationStore,
  eventId: string,
  managerId: string,
): AlertEvent | undefined {
  return store.updateEvent(eventId, {
    status: "대응 중",
    acknowledgedBy: managerId,
    acknowledgedAt: new Date().toISOString(),
  });
}

export function resolveAlertEvent(
  store: NotificationStore,
  eventId: string,
  status: Extract<AlertEventStatus, "해결됨" | "오경보" | "자동 종료"> = "해결됨",
): AlertEvent | undefined {
  return store.updateEvent(eventId, {
    status,
    resolvedAt: new Date().toISOString(),
  });
}

export async function escalateUnacknowledgedAlert(
  store: NotificationStore,
  channels: Map<NotificationChannelType, MockNotificationChannel>,
  managers: ManagerUserProfile[],
  policy: NotificationPolicy,
  input: NotificationAnalysisInput,
  parentEvent: AlertEvent,
): Promise<AlertEvent | null> {
  const now = Date.now();
  const created = parseIsoMs(parentEvent.createdAt);
  const ackAt = parentEvent.acknowledgedAt
    ? parseIsoMs(parentEvent.acknowledgedAt)
    : null;

  const waitBase = ackAt ?? created;
  const elapsed = (now - waitBase) / 1000;

  if (
    parentEvent.status !== "발송됨" &&
    parentEvent.status !== "생성됨"
  ) {
    return null;
  }

  if (elapsed < policy.managerAckTimeoutSeconds) {
    return null;
  }

  const nextLevel = parentEvent.escalationLevel + 1;
  const recipients = selectManagerRecipients(
    managers,
    input,
    parentEvent.priority,
    nextLevel,
  );

  if (recipients.length === 0) return null;

  const escalatedEvent: AlertEvent = {
    ...parentEvent,
    eventId: nextEventId(),
    parentEventId: parentEvent.eventId,
    escalationLevel: nextLevel,
    status: "발송됨",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    triggerReasons: [
      {
        code: "ESCALATION",
        message: `관리자 미응답 ${Math.floor(elapsed)}초 — 상위 관리자로 확대`,
      },
    ],
  };

  store.saveEvent(escalatedEvent);

  const msg = generateManagerMessage(
    input,
    escalatedEvent,
    parentEvent.priority,
    parentEvent.touristNotificationRequired,
  );
  msg.title = `[경보 확대 L${nextLevel}] ${msg.title}`;
  msg.body = `${msg.body}\n\n이전 경보(${parentEvent.eventId})에 대한 확인이 지연되어 상위 관리자에게 전달됩니다.`;

  for (const manager of recipients) {
    await sendNotification(channels, {
      eventId: escalatedEvent.eventId,
      recipientId: manager.managerId,
      recipientType: "manager",
      message: msg,
      pushEnabled: manager.pushEnabled,
    });
  }

  store.updateEvent(parentEvent.eventId, { status: "자동 종료" });
  return escalatedEvent;
}
