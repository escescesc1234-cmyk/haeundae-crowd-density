# Safe Flow — 밀도 API (Express)
FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
COPY config ./config
COPY public ./public
RUN npm run build && npm prune --omit=dev

FROM node:20-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=3780
COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/config ./config
COPY --from=build /app/public ./public
# POST /api/analyze/vision 이 vision/ 스크립트를 호출할 때 사용 (선택 이미지)
COPY vision ./vision
EXPOSE 3780
CMD ["node", "dist/api/start.js"]
