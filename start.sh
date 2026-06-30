#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# finderX — Cosmic Discovery Engine  ·  start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv
source .venv/bin/activate

echo ""
echo "  ██████╗ ██╗███╗   ██╗██████╗ ███████╗██████╗ ██╗  ██╗"
echo "  ██╔════╝ ╚═╝████╗  ██║██╔══██╗██╔════╝██╔══██╗╚██╗██╔╝"
echo "  █████╗   ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝ ╚███╔╝"
echo "  ██╔══╝   ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗ ██╔██╗"
echo "  ██║      ██║██║ ╚████║██████╔╝███████╗██║  ██║██╔╝ ██╗"
echo "  ╚═╝      ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝"
echo ""
echo "  Cosmic Discovery Engine"
echo "  Data: Gaia DR3 · SIMBAD · MPC/SkyBoT · NED · JPL SBDB · SDSS DR16"
echo ""
echo "  → http://localhost:5050"
echo "  → Ctrl+C to stop"
echo ""

python3 app.py
