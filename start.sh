#!/usr/bin/env sh
set -u
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
python3 refresh_database.py --if-needed || echo "Полная база не обновилась; запускаю интерфейс с доступными данными."
exec python3 server.py
