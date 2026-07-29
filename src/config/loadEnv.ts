/**
 * 프로젝트 루트 `.env`를 process.env에 로드
 * 로컬 개발에서는 `.env` 값을 우선해 키 갱신이 바로 반영되게 한다.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export function loadEnvFile(fileName = ".env"): void {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
  const path = join(root, fileName);
  if (!existsSync(path)) return;

  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}
