# Safe Flow — 실시간 AI 비전 (YOLO + Flask)
# 가중치는 이미지에 넣지 않음 → 볼륨으로 vision/models/yolo26s_beach_ft.pt 마운트
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps lwcc gdown || true

COPY . ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV CORS_ORIGINS=*
ENV HOST=0.0.0.0
ENV PORT=8790

EXPOSE 8790

# --detector both : 알람용 hybrid. 모델 없으면 ultralytics 기본 가중치로 기동될 수 있음.
CMD ["python", "realtime_safety_map.py", "--host", "0.0.0.0", "--port", "8790", "--detector", "both"]
