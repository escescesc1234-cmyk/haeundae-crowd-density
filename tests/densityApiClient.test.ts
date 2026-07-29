import { describe, expect, it, vi } from "vitest";
import {
  DensityApiClient,
  DensityApiError,
} from "../src/client/densityApiClient.js";
import { visionOutputUrl } from "../src/client/densityApiTypes.js";

describe("visionOutputUrl", () => {
  it("strips vision/output prefix", () => {
    expect(
      visionOutputUrl(
        "http://localhost:3780",
        "vision/output/safety_map/foo.jpg",
      ),
    ).toBe("http://localhost:3780/vision-output/safety_map/foo.jpg");
  });

  it("handles empty base (same origin)", () => {
    expect(visionOutputUrl("", "vision/output/app_bridge/bar.jpg")).toBe(
      "/vision-output/app_bridge/bar.jpg",
    );
  });
});

describe("DensityApiClient", () => {
  it("health() hits /api/health", async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({ ok: true, service: "haeundae-crowd-density" }),
    }));
    const client = new DensityApiClient({
      baseUrl: "http://localhost:3780",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const h = await client.health();
    expect(h.ok).toBe(true);
    expect(h.service).toBe("haeundae-crowd-density");
    expect(fetchImpl.mock.calls[0][0]).toBe(
      "http://localhost:3780/api/health",
    );
  });

  it("analyzeManual posts contract body", async () => {
    const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      expect(body.zoneId).toBe("GWANGALLI-ZONE-CENTER");
      expect(body.detectedPeople).toBe(800);
      expect(body.notify).toBe(false);
      return {
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            zoneId: "GWANGALLI-ZONE-CENTER",
            zoneName: "중앙 구역",
            riskLevel: "혼잡",
            detectedPeople: 800,
            rawDensity: 0.26,
            adjustedDensity: 0.26,
            measuredAt: body.measuredAt,
          }),
      };
    });
    const client = new DensityApiClient({
      baseUrl: "http://localhost:3780",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const r = await client.analyzeManual({
      zoneId: "GWANGALLI-ZONE-CENTER",
      detectedPeople: 800,
      measuredAt: "2026-07-29T00:00:00.000Z",
      notify: false,
    });
    expect(r.riskLevel).toBe("혼잡");
    expect(fetchImpl.mock.calls[0][0]).toBe(
      "http://localhost:3780/api/analyze/manual",
    );
  });

  it("maps connection failure message", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const client = new DensityApiClient({
      baseUrl: "http://localhost:3780",
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    await expect(client.health()).rejects.toBeInstanceOf(DensityApiError);
    await expect(client.health()).rejects.toThrow(/밀도 분석 서비스 연결 실패/);
  });
});
