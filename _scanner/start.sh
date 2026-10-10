#!/bin/sh
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
exec python3 app.py
