# 多阶段：Node 构建静态产物 → nginx 提供 SPA
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
ARG CACHE_BUST=0
RUN echo "cache-bust=${CACHE_BUST}"
COPY . .
# 生产默认走同源 nginx 代理，以便把 Easy Auth 身份安全转发给后端。
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build -- --logLevel silent

FROM nginx:1.27-alpine
ENV DF_BACKEND_UPSTREAM=https://ca-dataforge-backend.thankfultree-c0fc8321.eastus2.azurecontainerapps.io
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
