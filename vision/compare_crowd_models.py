"""광안리 해변 ROI에서 밀도추정 모델/가중치를 실측 비교.

서버가 쓰는 output/crowd_input.jpg(ROI 크롭)에 대해 여러 (model, weights)
조합의 카운트와 추론시간을 재서, 이 장면에 가장 맞는 조합을 고른다.

- SHA: ShanghaiTech-A (실내외 초고밀도)
- SHB: ShanghaiTech-B (거리, 저~중밀도)
- QNRF: UCF-QNRF (야외 고밀도, 다양한 장면) → 해변에 가장 근접 도메인
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMG = ROOT / "output" / "crowd_input.jpg"

# (model_name, weights) — lwcc 지원 조합만
COMBOS = [
    ("DM-Count", "SHA"),
    ("DM-Count", "SHB"),
    ("DM-Count", "QNRF"),
    ("CSRNet", "SHA"),
    ("CSRNet", "SHB"),
    ("Bay", "SHA"),
    ("Bay", "QNRF"),
    ("SFANet", "SHB"),
]


def main():
    img = sys.argv[1] if len(sys.argv) > 1 else str(IMG)
    if not Path(img).exists():
        print(f"[compare] 입력 없음: {img}")
        return
    print(f"[compare] 입력: {img}")
    from lwcc import LWCC

    for model_name, weights in COMBOS:
        try:
            t0 = time.perf_counter()
            m = LWCC.load_model(model_name=model_name, model_weights=weights)
            load_ms = (time.perf_counter() - t0) * 1000.0
            t1 = time.perf_counter()
            c = float(LWCC.get_count(img, model=m))
            infer_ms = (time.perf_counter() - t1) * 1000.0
            print(
                f"  {model_name:9s}/{weights:5s}  count={c:7.1f}  "
                f"load={load_ms:6.0f}ms  infer={infer_ms:6.0f}ms"
            )
        except Exception as exc:
            print(f"  {model_name:9s}/{weights:5s}  FAIL: {exc}")


if __name__ == "__main__":
    main()
