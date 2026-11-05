#!/bin/bash
# Бэкап данных CleanTrade: заказы, подписчики, журнал доказательств, лента событий.
# Простое копирование этих файлов ничего не стоит: один диск - одна точка отказа.
set -u
SRC=/root/CleanProd/data
DST=/root/CleanProd/backups
KEEP=14
STAMP=$(date +%Y%m%d_%H%M)
mkdir -p "$DST"
OUT="$DST/data_$STAMP.tar.gz"
cd "$SRC" || exit 1
tar -czf "$OUT" --ignore-failed-read \
  orders_api.json subscribers.json order_audit.jsonl events.jsonl \
  alerts.jsonl poller_heartbeat.json 2>/dev/null
if [ -s "$OUT" ]; then
  echo "OK $OUT $(stat -c%s "$OUT") bytes"
else
  echo "FAIL backup is empty"; exit 2
fi
# ротация: держим KEEP последних
ls -1t "$DST"/data_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
ls -1t "$DST"/data_*.tar.gz 2>/dev/null | wc -l | xargs -I{} echo "kept {} archives"
