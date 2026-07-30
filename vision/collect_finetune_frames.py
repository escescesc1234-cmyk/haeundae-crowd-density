"""파인튜닝용 원본 프레임 수집기 (4번: 이 카메라 전용 파인튜닝의 첫 단계).

실행 중인 실시간 서버의 /stream/raw(오버레이 없는 원본)에서 주기적으로
프레임을 저장한다. 시간대·조도·혼잡도가 다양하게 섞이도록 오래 켜 두면 된다.

수집 → 라벨링(예: Roboflow/LabelImg에서 person 박스) → yolo26s 파인튜닝
    → 결과물을 vision/models/yolo26s_beach_ft.pt 로 저장하면
    realtime_safety_map.py 가 자동으로 최우선 로드한다.

사용법:
    python collect_finetune_frames.py                 # 60초마다 저장, 기본 서버
    python collect_finetune_frames.py --every 30 --max 500
    python collect_finetune_frames.py --url http://127.0.0.1:8790/stream/raw
"""
from __future__ import annotations

import argparse
import time
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
                return buf[s:e + 2]
    return None


def main():
    ap = argparse.ArgumentParser(description="Collect raw frames for fine-tuning")
    ap.add_argument("--url", default="http://127.0.0.1:8790/stream/raw")
    ap.add_argument("--every", type=float, default=60.0, help="저장 간격(초)")
    ap.add_argument("--max", type=int, default=0, help="최대 장수(0=무제한)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"[collect] saving to {out}  every={args.every}s  from {args.url}")

    n = 0
    while True:
        try:
            jpg = grab_one(args.url)
            if jpg:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = out / f"gwangalli_{ts}.jpg"
                path.write_bytes(jpg)
                n += 1
                print(f"[collect] {n} saved {path.name} ({len(jpg)} bytes)")
            else:
                print("[collect] no frame (스트림/서버 확인)")
        except Exception as exc:
            print(f"[collect] error: {exc}")
        if args.max and n >= args.max:
            print(f"[collect] done ({n} frames)")
            break
        time.sleep(max(1.0, args.every))


if __name__ == "__main__":
    main()
