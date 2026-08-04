/**
 * 실시간 안전지도 스트림 런처
 * Python vision/realtime_safety_map.py 를 실행합니다.
 *
 *   npm run vision:realtime
 *   npm run vision:realtime -- --source https://www.youtube.com/watch?v=jmVmZlsQIL8
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const SCRIPT = join(ROOT, "vision", "realtime_safety_map.py");

function resolvePython(): string {
  if (process.env.VISION_PYTHON) return process.env.VISION_PYTHON;
  const win312 = join(
    process.env.LOCALAPPDATA ?? "",
    "Programs",
    "Python",
    "Python312",
    "python.exe",
  );
  if (win312 && existsSync(win312)) return win312;
  return process.platform === "win32" ? "python" : "python3";
}

const python = resolvePython();
const extraArgs = process.argv.slice(2);
/** 기본: hybrid FAST(both) + SAHI256 실시간 스트림. 사용자가 --detector 주면 그대로 사용 */
const hasDetectorFlag = extraArgs.some(
  (a, i) => a === "--detector" || (i > 0 && extraArgs[i - 1] === "--detector"),
);
const args = hasDetectorFlag
  ? [SCRIPT, ...extraArgs]
  : [SCRIPT, "--detector", "both", ...extraArgs];

console.log(`[vision:realtime] ${python} ${args.join(" ")}`);
console.log(`[vision:realtime] UI: http://127.0.0.1:8790/`);
console.log(`[vision:realtime] SAHI MJPEG: http://127.0.0.1:8790/stream/sahi256`);
console.log(`[vision:realtime] 안전지도: http://127.0.0.1:8790/stream`);

const child = spawn(python, args, {
  cwd: join(ROOT, "vision"),
  stdio: "inherit",
  env: {
    ...process.env,
    PYTHONIOENCODING: "utf-8",
    // 기본 SAHI-256 실시간 ON (명시적 0만 끔)
    VISION_SAHI256: process.env.VISION_SAHI256 ?? "1",
  },
  windowsHide: true,
});

child.on("exit", (code) => process.exit(code ?? 1));
