#!/bin/sh
# One image, several services — pick with UNIT8_SERVICE.
set -e
case "${UNIT8_SERVICE:-worker}" in
  chart)
    exec uvicorn api.server:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
    ;;
  worker)
    exec python main.py "$@"
    ;;
  *)
    echo "Unknown UNIT8_SERVICE='${UNIT8_SERVICE}' (worker | chart)" >&2
    exit 64
    ;;
esac
