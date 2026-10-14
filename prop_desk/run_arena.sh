#!/usr/bin/env bash
# Prop Desk Arena launcher for Mac/Linux. Run: ./prop_desk/run_arena.sh
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "Python environment not found. Follow prop_desk/SETUP.md step 3 first."; exit 1
fi
echo "Starting Prop Desk Arena - open http://localhost:8000 in your browser. Ctrl+C to stop."
while true; do
  .venv/bin/python -m prop_desk.server >> prop_desk/arena.log 2>&1
  echo "The Arena stopped unexpectedly. Restarting in 30 seconds... (see prop_desk/arena.log)"
  sleep 30
done
