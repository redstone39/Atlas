FROM node:22-alpine AS deps

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder

WORKDIR /app

COPY --from=deps /app/web/node_modules ./web/node_modules
COPY web ./web
COPY contracts ./contracts
RUN cd web && npm run build

FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=5174

COPY --from=builder /app/web/.next/standalone ./
COPY --from=builder /app/web/.next/static ./web/.next/static

WORKDIR /app/web

EXPOSE 5174

CMD ["node", "server.js"]
