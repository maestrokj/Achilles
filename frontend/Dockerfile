# syntax=docker/dockerfile:1
FROM node:22-alpine

RUN mkdir -p /app && chown node:node /app
USER node
WORKDIR /app

COPY --chown=node:node package.json package-lock.json ./
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 \
    npm ci

COPY --chown=node:node . .

EXPOSE 3000
CMD ["npm", "run", "dev"]
