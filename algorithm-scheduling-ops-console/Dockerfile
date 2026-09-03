FROM node:22-alpine AS build

WORKDIR /app
ARG VITE_CONTROL_BASE_URL=/control
ARG VITE_GATEWAY_BASE_URL=/gateway
ARG VITE_GPU_BASE_URL=http://192.168.29.11:9400
ENV VITE_CONTROL_BASE_URL=${VITE_CONTROL_BASE_URL} \
    VITE_GATEWAY_BASE_URL=${VITE_GATEWAY_BASE_URL} \
    VITE_GPU_BASE_URL=${VITE_GPU_BASE_URL}
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine

WORKDIR /app
COPY --from=build /app/dist ./dist
COPY server.mjs ./server.mjs

EXPOSE 80
CMD ["node", "server.mjs"]
