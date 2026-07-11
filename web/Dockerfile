# 多阶段：Node 构建静态产物 → nginx 提供 SPA
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
ARG CACHE_BUST=0
RUN echo "cache-bust=${CACHE_BUST}"
COPY . .
# 构建期注入后端地址（默认指向云端编排器后端）
ARG VITE_API_BASE="https://ca-dataforge-backend.thankfultree-c0fc8321.eastus2.azurecontainerapps.io"
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build -- --logLevel silent

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
