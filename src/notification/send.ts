/**
 * 알림 발송 및 재시도
 */

import type { MockNotificationChannel } from "./channels/mockChannel.js";
import type {
  GeneratedMessage,
  NotificationChannelType,
  NotificationDeliveryRecord,
  NotificationPolicy,
  RecipientType,
} from "./types.js";
import { nextDeliveryId } from "./storage.js";

export async function sendNotification(
  channels: Map<NotificationChannelType, MockNotificationChannel>,
  params: {
    eventId: string;
    recipientId: string;
    recipientType: RecipientType;
    message: GeneratedMessage;
    pushEnabled?: boolean;
  },
): Promise<NotificationDeliveryRecord[]> {
  const records: NotificationDeliveryRecord[] = [];
  const { eventId, recipientId, recipientType, message } = params;
  const pushEnabled = params.pushEnabled ?? true;

  for (const channelType of message.channels) {
    if (channelType === "push" && !pushEnabled) {
      records.push({
        deliveryId: nextDeliveryId(),
        eventId,
        recipientType,
        recipientId,
        messageTitle: message.title,
        messageBody: message.body,
        channel: channelType,
        sentAt: new Date().toISOString(),
        success: false,
        failureReason: "푸시 알림 권한이 꺼져 있습니다.",
        read: false,
        managerAcknowledged: false,
        resendCount: 0,
      });
      continue;
    }

    const channel = channels.get(channelType);
    if (!channel) continue;

    const result = await channel.send({
      recipientId,
      recipientType,
      title: message.title,
      body: message.body,
      priority: message.priority,
      eventId,
      soundAndVibration: message.soundAndVibration,
    });

    records.push({
      deliveryId: nextDeliveryId(),
      eventId,
      recipientType,
      recipientId,
      messageTitle: message.title,
      messageBody: message.body,
      channel: channelType,
      sentAt: new Date().toISOString(),
      success: result.success,
      failureReason: result.failureReason,
      read: false,
      managerAcknowledged: false,
      resendCount: 0,
    });
  }

  return records;
}

export async function retryFailedNotification(
  channels: Map<NotificationChannelType, MockNotificationChannel>,
  failed: NotificationDeliveryRecord,
  message: GeneratedMessage,
  policy: NotificationPolicy,
  attempt: number,
): Promise<NotificationDeliveryRecord | null> {
  if (attempt > policy.maxRetryAttempts) return null;

  const channel = channels.get(failed.channel);
  if (!channel) return null;

  const result = await channel.send({
    recipientId: failed.recipientId,
    recipientType: failed.recipientType,
    title: message.title,
    body: message.body,
    priority: message.priority,
    eventId: failed.eventId,
    soundAndVibration: message.soundAndVibration,
  });

  return {
    ...failed,
    deliveryId: nextDeliveryId(),
    sentAt: new Date().toISOString(),
    success: result.success,
    failureReason: result.failureReason,
    resendCount: attempt,
  };
}

export function recordNotificationLog(
  store: { recordNotificationLog: (r: NotificationDeliveryRecord) => void },
  records: NotificationDeliveryRecord[],
) {
  for (const r of records) {
    store.recordNotificationLog(r);
  }
}
