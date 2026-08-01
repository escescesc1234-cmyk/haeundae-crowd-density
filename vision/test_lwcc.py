"""lwcc(CSRNet/DM-Count) 병행 가능성 검증: 가중치 다운로드 + CPU 추론 시간/카운트."""
from __future__ import annotations

import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRAME = ROOT / "output" / "bench_frame.jpg"


def main():
    from lwcc import LWCC

    trials = [
        ("DM-Count", "SHA"),
        ("CSRNet", "SHA"),
    ]
    for name, w in trials:
        try:
            t0 = time.perf_counter()
            model = LWCC.load_model(model_name=name, model_weights=w)
            load_ms = (time.perf_counter() - t0) * 1000.0
            t1 = time.perf_counter()
            count = LWCC.get_count(str(FRAME), model=model)
            infer_ms = (time.perf_counter() - t1) * 1000.0
            print(
                f"[ok] {name}/{w}: count={float(count):.1f} "
                f"load={load_ms:.0f}ms infer={infer_ms:.0f}ms"
            )
        except Exception as exc:
            import traceback

            print(f"[FAIL] {name}/{w}: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
