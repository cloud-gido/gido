

ARG NODE_IMAGE=docker.m.daocloud.io/library/node:18-alpine
ARG NGINX_IMAGE=docker.m.daocloud.io/library/nginx:alpine

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

FROM ${NGINX_IMAGE}
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
