import { describe, expect, it } from "vitest";
import {
  toVisionBridgePayload,
  toVisionDensityInput,
} from "../src/adapters/visionAdapter.js";

describe("visionAdapter", () => {
  it("maps Python JSON to DensityInput for the density engine", () => {
    const raw = {
      ok: true,
      zoneId: "GWANGALLI-ZONE-CENTER",
      measuredAt: "2026-07-29T01:00:00.000Z",
      detectedPeople: 16,
      confidence: 0.42,
      dataSource: "vision_yolo_sahi",
      isTestData: true,
      cameras: [
        {
          cameraId: "vision-sahi-yolov8",
          detectedPeople: 16,
          confidence: 0.42,
          measuredAt: "2026-07-29T01:00:00.000Z",
        },
      ],
      vision: {
        imagePath: "x.png",
        imageStem: "01_wide_full_beach",
        rawDetections: 19,
        roiPersonCount: 16,
        meanConfidence: 0.42,
        inferenceSeconds: 1.2,
        upscale: 2,
        sliceSize: 256,
        overlapRatio: 0.25,
        roiAreaM2Homography: 1800,
        densityPerM2Homography: 0.008,
        homographyFieldVerified: false,
        calibrationPath: null,
        heatmapPath: "h.jpg",
        heatmapRelativePath: "vision/output/app_bridge/h.jpg",
        personCentersPixels: [[1, 2]],
        personCentersMeters: [[0.1, 0.2]],
        note: "test",
      },
    };

    const payload = toVisionBridgePayload(raw);
    const input = toVisionDensityInput(payload);

    expect(payload.zoneId).toBe("GWANGALLI-ZONE-CENTER");
    expect(input.dataSource).toBe("vision_yolo_sahi");
    expect(input.detectedPeople).toBe(16);
    expect(input.confidence).toBe(0.42);
    expect(input.auxiliaryFactors?.cctvAnalysisConfidence).toBe(0.42);
    expect(input.effectiveAreaSquareMeters).toBeUndefined();
  });

  it("passes homography area when provided", () => {
    const payload = toVisionBridgePayload({
      ok: true,
      zoneId: "GWANGALLI-ZONE-CENTER",
      measuredAt: "2026-07-29T01:00:00.000Z",
      detectedPeople: 10,
      confidence: 0.5,
      effectiveAreaSquareMeters: 2500,
      cameras: [
        {
          cameraId: "vision-sahi-yolov8",
          detectedPeople: 10,
          confidence: 0.5,
          measuredAt: "2026-07-29T01:00:00.000Z",
        },
      ],
    });
    const input = toVisionDensityInput(payload);
    expect(input.effectiveAreaSquareMeters).toBe(2500);
  });
});
