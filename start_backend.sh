#!/bin/bash
source /opt/yecai/yecai-new/venv/bin/activate
cd /opt/yecai/yecai-new
exec gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000 --pid /var/run/yecai-backend.pid --access-logfile /var/log/yecai-access.log --error-logfile /var/log/yecai-error.log

