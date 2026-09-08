#!/bin/sh
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PYTHON_BIN=""
for candidate in python3 python python3.12 python3.11 python3.10 python3.9; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[0] >= 3 else 1)
PY
    then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "没有找到可用的 Python 3。"
  echo "请先安装 Python 3，然后再双击这个启动文件。"
  if [ -t 0 ]; then
    printf "按回车退出..."
    read dummy
  fi
  exit 1
fi

exec "$PYTHON_BIN" quant_proxy_server.py
