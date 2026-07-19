#!/usr/bin/env sh
set -eu
curl --fail --silent http://localhost:8000/health
curl --fail --silent http://localhost:3000/api/health
