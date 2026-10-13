# Сборка фронта и nginx в одном образе. vite build — production: .env.development не читается, моки в сборку не попадают.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_FACULTY_NAME=
ARG VITE_STATS_URL=
ARG VITE_BUILD=
RUN npm run build

FROM nginx:stable
# Стандартный access_log образа писал бы каждый запрос второй, не-JSON строкой рядом с нашим JSON-логом из шаблона
RUN sed -i '/access_log/d' /etc/nginx/nginx.conf
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
