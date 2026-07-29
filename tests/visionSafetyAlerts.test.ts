import { describe, expect, it } from "vitest";
import {
  alertsFromDensityGrid,
  normalizeVisionAlerts,
  VISION_MANAGER_DANGER_MESSAGE,
  VISION_TOURIST_DANGER_MESSAGE,
} from "../src/adapters/visionSafetyAlerts.js";
import { toVisionBridgePayload } from "../src/adapters/visionAdapter.js";

describe("visionSafetyAlerts", () => {
  it("returns tourist/manager messages when a danger cell exists", () => {
    const alerts = alertsFromDensityGrid([
      [0.1, 1.2, null],
      [3.9, 6.0, 4.5],
    ]);
    expect(alerts.hasDanger).toBe(true);
    expect(alerts.dangerCellCount).toBe(1);
    expect(alerts.touristMessage).toBe(VISION_TOURIST_DANGER_MESSAGE);
    expect(alerts.managerMessage).toBe(VISION_MANAGER_DANGER_MESSAGE);
  });

  it("returns no messages when all cells are below danger", () => {
    const alerts = alertsFromDensityGrid([
      [0, 1, 2],
      [3.9, 5.9, null],
    ]);
    expect(alerts.hasDanger).toBe(false);
    expect(alerts.touristMessage).toBeNull();
    expect(alerts.managerMessage).toBeNull();
  });

  it("maps Python alerts into vision bridge payload", () => {
    const payload = toVisionBridgePayload({
      ok: true,
      zoneId: "GWANGALLI-ZONE-CENTER",
      measuredAt: "2026-07-29T01:00:00.000Z",
      detectedPeople: 10,
      confidence: 0.5,
      cameras: [
        {
          cameraId: "vision-sahi-yolov8",
          detectedPeople: 10,
          confidence: 0.5,
          measuredAt: "2026-07-29T01:00:00.000Z",
        },
      ],
      alerts: {
        hasDanger: true,
        dangerCellCount: 2,
        touristMessage: VISION_TOURIST_DANGER_MESSAGE,
        managerMessage: VISION_MANAGER_DANGER_MESSAGE,
      },
      vision: {
        imagePath: "x.png",
        imageStem: "x",
        rawDetections: 10,
        roiPersonCount: 10,
        meanConfidence: 0.5,
        inferenceSeconds: 1,
        upscale: 2,
        sliceSize: 256,
        overlapRatio: 0.25,
        roiAreaM2Homography: 1000,
        densityPerM2Homography: 0.01,
        homographyFieldVerified: false,
        calibrationPath: null,
        heatmapPath: "h.jpg",
        heatmapRelativePath: "h.jpg",
        personCentersPixels: [],
        personCentersMeters: [],
        note: "t",
      },
    });
    expect(payload.alerts?.hasDanger).toBe(true);
    expect(payload.alerts?.touristMessage).toBe(VISION_TOURIST_DANGER_MESSAGE);
    expect(normalizeVisionAlerts(payload.alerts).managerMessage).toBe(
      VISION_MANAGER_DANGER_MESSAGE,
    );
  });
});
