#!/usr/bin/env bash
set -e

# Colors
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
warn() { echo -e "  ${YELLOW}!${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; exit 1; }
step() { echo -e "\n${BOLD}$1${NC}"; }

echo ""
echo -e "${BOLD}  Telos Setup${NC}"
echo -e "${DIM}  ─────────────────────────────${NC}"

# ── 1. Python ──────────────────────────────────────────────────────────────
step "1. Checking Python"
if ! command -v python3 &>/dev/null; then
  fail "Python 3 not found. Install from https://python.org"
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  fail "Python $PY_VER found, but 3.11+ is required."
fi
ok "Python $PY_VER"

# ── 2. Virtual environment ─────────────────────────────────────────────────
step "2. Python environment"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  ok "Created .venv"
else
  ok ".venv already exists"
fi
source .venv/bin/activate
echo -n "  Installing dependencies (may take a minute)..."
pip install -e . -q --disable-pip-version-check
echo -e " ${GREEN}done${NC}"

# ── 3. Docker ──────────────────────────────────────────────────────────────
step "3. Checking Docker"
if ! command -v docker &>/dev/null; then
  fail "Docker not found. Install from https://docker.com and re-run setup."
fi
if ! docker info &>/dev/null 2>&1; then
  fail "Docker is installed but not running. Start Docker Desktop and re-run setup."
fi
ok "Docker is running"

# ── 4. Build sandbox image ─────────────────────────────────────────────────
step "4. Building sandbox image"
echo -n "  Building telos-sandbox:latest (first time ~2 min)..."
docker build -t telos-sandbox:latest . -q
echo -e " ${GREEN}done${NC}"

# ── 5. Start Qdrant ────────────────────────────────────────────────────────
step "5. Starting Qdrant"
if docker compose version &>/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
  COMPOSE_CMD="docker-compose"
else
  fail "docker compose not found. Update Docker Desktop to a recent version."
fi
$COMPOSE_CMD up -d --quiet-pull 2>/dev/null
ok "Qdrant started (http://localhost:6333)"

# ── 6. API key ─────────────────────────────────────────────────────────────
step "6. API key"
if [ -f ".env" ] && grep -q "OPENROUTER_API_KEY=sk-or" .env 2>/dev/null; then
  ok "OPENROUTER_API_KEY already set in .env"
else
  echo -e "  ${DIM}Get a free key at: https://openrouter.ai/keys${NC}"
  echo ""
  read -rsp "  Paste your OpenRouter API key: " API_KEY
  echo ""
  if [ -z "$API_KEY" ]; then
    warn "Skipped. Add OPENROUTER_API_KEY=<key> to .env before running telos."
  else
    # Preserve any existing .env content, replace or add the key
    if [ -f ".env" ]; then
      grep -v "^OPENROUTER_API_KEY=" .env > .env.tmp && mv .env.tmp .env || true
    fi
    echo "OPENROUTER_API_KEY=$API_KEY" >> .env
    ok "Saved to .env"
  fi
fi

# ── 7. Embedding model ─────────────────────────────────────────────────────
step "7. Embedding model"
echo -n "  Downloading all-MiniLM-L6-v2 (~90MB, first time only)..."
python3 -c "
import os, warnings, logging
os.environ.update({'HF_HUB_VERBOSITY':'error','TOKENIZERS_PARALLELISM':'false','HF_HUB_DISABLE_PROGRESS_BARS':'1'})
warnings.filterwarnings('ignore')
logging.getLogger('huggingface_hub').setLevel(logging.ERROR)
logging.getLogger('sentence_transformers').setLevel(logging.ERROR)
from sentence_transformers import SentenceTransformer
SentenceTransformer('all-MiniLM-L6-v2')
" 2>/dev/null
echo -e " ${GREEN}done${NC}"

# ── 8. Project directory ───────────────────────────────────────────────────
step "8. Project directory"
mkdir -p projects/default/workspace/persistent
ok "projects/default/ ready"

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${DIM}  ─────────────────────────────${NC}"
echo -e "${GREEN}${BOLD}  Setup complete.${NC}"
echo ""
echo -e "  ${BOLD}Activate venv and run:${NC}"
echo ""
echo -e "    source .venv/bin/activate"
echo -e "    telos start"
echo ""
