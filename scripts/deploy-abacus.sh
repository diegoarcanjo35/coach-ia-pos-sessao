#!/usr/bin/env sh
set -eu
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
