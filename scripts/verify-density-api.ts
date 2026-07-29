/**
 * docs/INTEGRATION-PROMPT 최소 연동 예시와 동일 계약 검증
 */
import {
  DensityApiClient,
  visionOutputUrl,
} from "../src/client/densityApiClient.js";

const BASE = process.env.DENSITY_API_BASE_URL ?? "http://localhost:3780";

async function main() {
  const client = new DensityApiClient({ baseUrl: BASE });

  const health = await client.health();
  console.log("1) health:", JSON.stringify(health));

  const manual = await client.analyzeManual({
    zoneId: "GWANGALLI-ZONE-CENTER",
    detectedPeople: 800,
    measuredAt: new Date().toISOString(),
    notify: false,
  });
  console.log(
    "2) manual:",
    JSON.stringify(
      {
        zoneId: manual.zoneId,
        zoneName: manual.zoneName,
        riskLevel: manual.riskLevel,
        detectedPeople: manual.detectedPeople,
        rawDensity: manual.rawDensity,
        adjustedDensity: manual.adjustedDensity,
      },
      null,
      2,
    ),
  );

  try {
    const vision = await client.analyzeVision({
      imagePath: "vision/input/screenshots/01_wide_full_beach.png",
      zoneId: "GWANGALLI-ZONE-CENTER",
      skipHysteresis: true,
      notify: false,
    });

    const safetyMapUrl = vision.vision?.safetyMapRelativePath
      ? `${BASE}/vision-output/${String(vision.vision.safetyMapRelativePath).replace(/^vision\/output\//, "")}`
      : null;

    const viaHelper = visionOutputUrl(
      BASE,
      vision.vision?.safetyMapRelativePath as string | undefined,
    );

    console.log("3) vision.ok:", vision.ok);
    console.log(
      "   analysis:",
      JSON.stringify({
        riskLevel: vision.analysis?.riskLevel,
        detectedPeople: vision.analysis?.detectedPeople,
        adjustedDensity: vision.analysis?.adjustedDensity,
        dataSource: vision.analysis?.dataSource,
      }),
    );
    console.log("   alerts:", JSON.stringify(vision.alerts));
    console.log(
      "   safetyMapRelativePath:",
      vision.vision?.safetyMapRelativePath ?? null,
    );
    console.log("   safetyMapUrl (snippet rule):", safetyMapUrl);
    console.log("   safetyMapUrl (visionOutputUrl):", viaHelper);
    console.log(
      "   heatmapUrl:",
      visionOutputUrl(
        BASE,
        vision.vision?.heatmapRelativePath as string | undefined,
      ),
    );
  } catch (e) {
    console.log(
      "3) vision_error:",
      e instanceof Error ? e.message : String(e),
    );
    console.log(
      "   → vision/requirements.txt pip 설치 + VISION_PYTHON 확인 후 재시도",
    );
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
