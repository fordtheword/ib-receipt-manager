#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

source venv/bin/activate

# Run in background to mimic "start /min"
python -m uvicorn app:app --port 8000 &
echo "Server started in background. PID: $!"
