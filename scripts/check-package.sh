#!/usr/bin/env bash
# check-package.sh — Inspect the built package without uploading.
# Usage: bash scripts/check-package.sh
set -euo pipefail

# Ensure uv is in PATH
if ! command -v uv &>/dev/null; then
    export PATH="$HOME/.local/bin:$PATH"
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "  ${YELLOW}WARN${NC} $1"; }
section() { echo -e "\n${YELLOW}── $1 ──${NC}"; }

PKG_VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null \
    || python3 -c "import re; print(re.search(r'version\s*=\s*\"(.+?)\"', open('pyproject.toml').read()).group(1))")
VENV_DIR=$(mktemp -d)
trap 'rm -rf "$VENV_DIR"' EXIT

# ── Build ────────────────────────────────────────────────────────────────────

section "Build"
rm -rf dist/
uv build 2>&1 | tail -2

WHL=$(ls dist/*.whl 2>/dev/null | head -1)
SDIST=$(ls dist/*.tar.gz 2>/dev/null | head -1)

if [ -n "$WHL" ]; then pass "Wheel built: $(basename $WHL) ($(du -h $WHL | cut -f1))"; else fail "No wheel produced"; fi
if [ -n "$SDIST" ]; then pass "Sdist built: $(basename $SDIST) ($(du -h $SDIST | cut -f1))"; else fail "No sdist produced"; fi

# ── Wheel contents ───────────────────────────────────────────────────────────

section "Wheel contents"
TOTAL_FILES=$(python3 -c "import zipfile; z=zipfile.ZipFile('$WHL'); print(len(z.namelist()))")
PY_FILES=$(python3 -c "import zipfile; z=zipfile.ZipFile('$WHL'); print(len([n for n in z.namelist() if n.endswith('.py')]))")
echo "  Files: $TOTAL_FILES total, $PY_FILES Python"

# Check key files exist
for key in "sagewai/__init__.py" "sagewai/cli/__init__.py" "sagewai/core/base.py" "sagewai/engines/universal.py"; do
    if python3 -c "import zipfile; z=zipfile.ZipFile('$WHL'); assert '$key' in z.namelist()" 2>/dev/null; then
        pass "$key present"
    else
        fail "$key missing"
    fi
done

# Check no dev files leaked
for bad in "tests/" "scripts/" "Makefile" ".env" ".github/" "conftest" "__pycache__"; do
    if python3 -c "import zipfile; z=zipfile.ZipFile('$WHL'); assert not any('$bad' in n for n in z.namelist())" 2>/dev/null; then
        pass "No '$bad' in wheel"
    else
        fail "'$bad' found in wheel"
    fi
done

# ── Metadata ─────────────────────────────────────────────────────────────────

section "Metadata"
META=$(python3 -c "
import zipfile
with zipfile.ZipFile('$WHL') as z:
    for name in z.namelist():
        if name.endswith('METADATA'):
            print(z.read(name).decode())
            break
")

check_meta() {
    if echo "$META" | grep -q "$1"; then pass "$2"; else fail "$2"; fi
}

check_meta "Name: sagewai"                         "Package name"
check_meta "Version: $PKG_VERSION"                  "Version $PKG_VERSION"
check_meta "License: AGPL-3.0-or-later"            "AGPL license"
check_meta "https://sagewai.ai"                    "Homepage URL"
check_meta "https://github.com/sagewai/sagewai"    "Repository URL"
check_meta "Ali Arda Diri"                         "Author name"
check_meta "Requires-Python: >=3.10"               "Python >=3.10"

# ── Clean install ────────────────────────────────────────────────────────────

section "Clean install"
uv venv --python 3.13 --seed "$VENV_DIR" 2>/dev/null
"$VENV_DIR/bin/pip" install --quiet "$WHL" 2>&1 | tail -1

VERSION=$("$VENV_DIR/bin/python" -c "import sagewai; print(sagewai.__version__)" 2>&1)
INSTALLED_VER=$("$VENV_DIR/bin/python" -c "import sagewai; print(sagewai.__version__)" 2>&1)
if [ "$INSTALLED_VER" = "$PKG_VERSION" ]; then pass "Import: sagewai $INSTALLED_VER"; else fail "Import failed: expected $PKG_VERSION, got $INSTALLED_VER"; fi

CLI_VERSION=$("$VENV_DIR/bin/sagewai" version 2>&1)
if echo "$CLI_VERSION" | grep -q "$PKG_VERSION"; then pass "CLI: $CLI_VERSION"; else fail "CLI failed: $CLI_VERSION"; fi

# Check core imports work
"$VENV_DIR/bin/python" -c "
from sagewai import UniversalAgent, tool, AgentConfig, ChatMessage
from sagewai.memory.graph import GraphMemory
from sagewai.safety.pii import PIIGuard
from sagewai.core.registry import AgentRegistry
from sagewai.auth.jwt import JWTAuth
from sagewai.harness.store import InMemoryHarnessStore
from sagewai.observability.costs import CostTracker
" 2>&1 && pass "Core imports" || fail "Core imports"

# ── Summary ──────────────────────────────────────────────────────────────────

section "Summary"
echo -e "  ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"
[ $FAIL -eq 0 ] && echo -e "  ${GREEN}Package is ready for publishing.${NC}" || echo -e "  ${RED}Fix failures before publishing.${NC}"
exit $FAIL
