"""군중 카운팅 보정계수(VISION_CROWD_CALIB) 산출.

서버 ROI(output/crowd_input.jpg) 또는 지정 이미지에 대해
DM-Count/QNRF · Bay/QNRF 를 돌리고, (선택) 수동 정답과 비교해
평균 보정계수 mean(actual/raw) 를 출력한다.

사용:
  # 현재 ROI만 모델값 확인
  python calibrate_crowd.py

  # 수동 정답 있는 샘플로 보정계수 산출
  # calib_samples.csv: path,actual
  python calibrate_crowd.py --gt calib_samples.csv

  # 라이브 프레임 N장 저장 후 수동 카운트 유도
  python calibrate_crowd.py --capture 3 --port 8790
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_IMG = ROOT / "output" / "crowd_input.jpg"
OUT_DIR = ROOT / "output" / "calib_frames"

MODELS = (("DM-Count", "QNRF"), ("Bay", "QNRF"))


def run_models(img: Path) -> dict:
    from lwcc import LWCC

    out = {}
    for name, weights in MODELS:
        m = LWCC.load_model(model_name=name, model_weights=weights)
        t0 = time.perf_counter()
        c = float(LWCC.get_count(str(img), model=m))
        ms = (time.perf_counter() - t0) * 1000.0
        out[f"{name}/{weights}"] = {"count": round(c, 2), "inferMs": round(ms)}
    vals = [v["count"] for v in out.values()]
    out["ensemble_mean"] = round(sum(vals) / len(vals), 2)
    return out


def _grab_mjpeg_frame(url: str, timeout: float = 15.0) -> bytes | None:
    """multipart MJPEG 스트림에서 JPEG 1장만 추출."""
    req = urllib.request.Request(url, headers={"Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = r.read(4096)
            if not chunk:
                break
            buf += chunk
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9")
            if a >= 0 and b > a:
                return buf[a : b + 2]
            if len(buf) > 8_000_000:
                buf = buf[-2_000_000:]
    return None


def capture_frames(port: int, n: int) -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for i in range(n):
        data = _grab_mjpeg_frame(f"http://localhost:{port}/stream/raw")
        if not data:
            # 폴백: 서버가 쓰는 crowd ROI
            src = ROOT / "output" / "crowd_input.jpg"
            if src.exists():
                data = src.read_bytes()
            else:
                print("[calib] capture fail")
                break
        fp = OUT_DIR / f"calib_{int(time.time())}_{i}.jpg"
        fp.write_bytes(data)
        saved.append(fp)
        print(f"[calib] saved {fp} ({len(data)} bytes)")
        if i + 1 < n:
            time.sleep(8.0)
    return saved


def load_gt(path: Path) -> list[tuple[Path, float]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            p = Path(row["path"])
            if not p.is_absolute():
                p = (ROOT / p).resolve()
            rows.append((p, float(row["actual"])))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=str(DEFAULT_IMG))
    ap.add_argument("--gt", default=None, help="CSV: path,actual")
    ap.add_argument("--capture", type=int, default=0)
    ap.add_argument("--port", type=int, default=8790)
    args = ap.parse_args()

    if args.capture > 0:
        capture_frames(args.port, args.capture)
        print(
            f"[calib] {OUT_DIR} 의 프레임을 수동으로 센 뒤 "
            "calib_samples.csv(path,actual)를 만들고 --gt 로 재실행하세요."
        )
        return

    if args.gt:
        pairs = load_gt(Path(args.gt))
        ratios = []
        report = []
        for img, actual in pairs:
            if not img.exists():
                print(f"[calib] missing {img}")
                continue
            m = run_models(img)
            raw = m["ensemble_mean"]
            ratio = (actual / raw) if raw > 1e-3 else None
            if ratio is not None:
                ratios.append(ratio)
            report.append({"img": str(img), "actual": actual, **m, "ratio": ratio})
            ratio_s = f"{ratio:.3f}" if ratio is not None else "n/a"
            print(
                f"  {img.name}: actual={actual:.0f} "
                f"ens={raw:.1f} ratio={ratio_s}"
            )
        if ratios:
            calib = sum(ratios) / len(ratios)
            print(f"\n[calib] 추천 VISION_CROWD_CALIB = {calib:.3f}  (n={len(ratios)})")
            print(f"[calib] 코드 기본값 또는 환경변수로 적용하세요.")
        (ROOT / "output" / "calib_report.json").write_text(
            json.dumps({"samples": report, "calib": calib if ratios else None}, indent=2),
            encoding="utf-8",
        )
        return

    img = Path(args.img)
    if not img.exists():
        print(f"[calib] 입력 없음: {img}")
        return
    print(f"[calib] {img}")
    m = run_models(img)
    for k, v in m.items():
        if isinstance(v, dict):
            print(f"  {k:16s}  count={v['count']:7.1f}  infer={v['inferMs']:.0f}ms")
        else:
            print(f"  {k:16s}  count={v:7.1f}")
    print(
        "\n[calib] 수동 정답이 있으면 CSV(path,actual)로 --gt 실행. "
        "또는 --capture N 으로 프레임을 저장하세요."
    )


if __name__ == "__main__":
    main()
