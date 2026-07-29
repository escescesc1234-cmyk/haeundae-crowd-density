/**
 * CLI: 비전 파이프라인 → 밀도 엔진 연동
 *
 * 사용 예:
 *   npm run vision:analyze -- vision/input/screenshots/01_wide_full_beach.png
 *   npm run vision:analyze -- 01_wide_full_beach.png GWANGALLI-ZONE-CENTER
 */

import { sharedService } from "./service/crowdDensityService.js";
import { defaultVisionScreenshot } from "./adapters/visionAdapter.js";

async function main() {
  const imageArg = process.argv[2] ?? defaultVisionScreenshot();
  const zoneId = process.argv[3] ?? "GWANGALLI-ZONE-CENTER";

  console.log(`[vision:analyze] image=${imageArg}`);
  console.log(`[vision:analyze] zoneId=${zoneId}`);

  const result = await sharedService.analyzeVision(
    {
      imagePath: imageArg,
      zoneId,
    },
    { skipHysteresis: true },
  );

  const a = result.analysis;
  console.log("--- density engine ---");
  console.log(`zoneId          : ${a.zoneId}`);
  console.log(`detectedPeople  : ${a.detectedPeople}`);
  console.log(`rawDensity      : ${a.rawDensity}`);
  console.log(`adjustedDensity : ${a.adjustedDensity}`);
  console.log(`riskLevel       : ${a.riskLevel}`);
  console.log(`dataSource      : ${a.dataSource}`);
  console.log("--- alerts ---");
  console.log(JSON.stringify(result.vision.payload.alerts ?? null, null, 2));
  console.log("--- vision ---");
  console.log(JSON.stringify(result.vision.payload.vision, null, 2));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
