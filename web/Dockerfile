FROM node:22-alpine

WORKDIR /app

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/components.json web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
COPY contracts /contracts

ENV ATLAS_PRODUCTION_API_PROXY_TARGET=http://api:8002

RUN npm run build

EXPOSE 5174

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5174"]
