"""파인튜닝용 원본 프레임 수집기 (보조).

권장: realtime_safety_map 내장 수집(VISION_COLLECT_RAW=1)을 쓰세요.
이 스크립트는 서버의 /stream/raw 를 주기적으로 저장하는 보조 경로입니다.

사용법:
    python collect_finetune_frames.py                 # 60초마다 저장, 기본 서버
    python collect_finetune_frames.py --every 30 --max 500
    python collect_finetune_frames.py --url http://127.0.0.1:8790/stream/raw
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "finetune" / "raw"


def grab_one(url: str, timeout: float = 20.0) -> bytes | None:
    """MJPEG 스트림에서 JPEG 한 장을 추출."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = b""
        while len(buf) < 8_000_000:
            chunk = r.read(8192)
            if not chunk:
                break
            buf += chunk
            s = buf.find(b"\xff\xd8")
            e = buf.find(b"\xff\xd9", s + 2) if s >= 0 else -1
            if s >= 0 and e > s:
                return buf[s : e + 2]
    return None


def main():
    ap = argparse.ArgumentParser(description="Collect raw frames for fine-tuning")
    ap.add_argument("--url", default="http://127.0.0.1:8790/stream/raw")
    ap.add_argument("--every", type=float, default=60.0, help="저장 간격(초)")
    ap.add_argument("--max", type=int, default=0, help="최대 장수(0=무제한)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    # 파이프 리다이렉트에서도 로그가 바로 보이게
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(
        f"[collect] saving to {out}  every={args.every}s  from {args.url}",
        flush=True,
    )
    print(
        "[collect] 참고: 서버 내장 수집(VISION_COLLECT_RAW)이 켜져 있으면 "
        "중복 저장될 수 있습니다.",
        flush=True,
    )

    n = 0
    fail_streak = 0
    while True:
        try:
            jpg = grab_one(args.url)
            if jpg:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = out / f"gwangalli_{ts}.jpg"
                path.write_bytes(jpg)
                n += 1
                fail_streak = 0
                print(f"[collect] {n} saved {path.name} ({len(jpg)} bytes)", flush=True)
            else:
                fail_streak += 1
                print("[collect] no frame (스트림/서버 확인)", flush=True)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            fail_streak += 1
            # 서버 꺼짐(10061)일 때 60초마다 로그 폭주 방지
            wait = min(300.0, max(args.every, 15.0) * min(fail_streak, 5))
            print(f"[collect] error: {exc} → {wait:.0f}s 후 재시도", flush=True)
            if args.max and n >= args.max:
                break
            time.sleep(wait)
            continue
        except Exception as exc:  # noqa: BLE001
            fail_streak += 1
            print(f"[collect] error: {exc}", flush=True)
        if args.max and n >= args.max:
            print(f"[collect] done ({n} frames)", flush=True)
            break
        time.sleep(max(1.0, args.every))


if __name__ == "__main__":
    main()
