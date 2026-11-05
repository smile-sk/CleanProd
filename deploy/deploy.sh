#!/usr/bin/env bash
# CleanProd - деплой на VPS: venv + systemd-юниты (ботферма + платёжное API).
set -euo pipefail

cd /root/CleanProd

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

cp deploy/cleanprod.service /etc/systemd/system/cleanprod.service
cp deploy/stablepay.service /etc/systemd/system/stablepay.service
systemctl daemon-reload
systemctl enable --now cleanprod stablepay

echo "OK. Logs: journalctl -u cleanprod -f"
