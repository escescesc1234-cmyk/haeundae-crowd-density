# 위험 격자 경고 배너 데모 (합성 밀도 격자)
# 실제 탐지 없이도 빨강 칸 + 관광객/관리자 메시지가 이미지에 표시되는지 확인합니다.

from pathlib import Path
import json
import sys

import cv2
import numpy as np

from safety_map import (
    CELL_PX,
    OVERLAY_ALPHA,
    build_warning_messages,
    count_danger_cells,
    draw_legend,
    draw_warning_banners,
    render_safety_map,
)

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "input" / "screenshots" / "10_new_zone_right.png"
OUT_DIR = ROOT / "output" / "safety_map"
OUT = OUT_DIR / "10_new_zone_right_safety_map_danger_demo.jpg"


def main():
    image = cv2.imread(str(SRC))
    if image is None:
        print(f"이미지 없음: {SRC}", file=sys.stderr)
        sys.exit(1)

    h, w = image.shape[:2]
    rows = int(np.ceil(h / CELL_PX))
    cols = int(np.ceil(w / CELL_PX))
    density = np.full((rows, cols), 0.5, dtype=np.float32)  # 기본 안전(초록)
    # 해안가 쪽에 위험(빨강) 칸 2개 강제 삽입
    density[rows - 2, cols // 2] = 6.5
    density[rows - 2, cols // 2 + 1] = 7.0
    # 주의(노랑) 칸 1개
    density[rows - 3, cols // 2] = 4.5

    safety = render_safety_map(image, density, CELL_PX, OVERLAY_ALPHA)
    safety = draw_legend(safety)
    danger = count_danger_cells(density)
    alerts = build_warning_messages(danger)
    print(f"[경고][관광객] {alerts['touristMessage']}")
    print(f"[경고][관리자] {alerts['managerMessage']}")
    safety = draw_warning_banners(safety, alerts)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT), safety)
    (OUT_DIR / "10_new_zone_right_safety_map_danger_demo.json").write_text(
        json.dumps({"output": str(OUT), "alerts": alerts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
