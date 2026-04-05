#!/usr/bin/env bash
# test-publish.sh — Full TestPyPI round-trip: build → upload → install → verify.
# Usage: TESTPYPI_TOKEN=pypi-... bash scripts/test-publish.sh
set -euo pipefail

# Ensure uv is in PATH
if ! command -v uv &>/dev/null; then
    export PATH="$HOME/.local/bin:$PATH"
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }
section() { echo -e "\n${YELLOW}── $1 ──${NC}"; }

PKG_VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null \
    || python3 -c "import re; print(re.search(r'version\s*=\s*\"(.+?)\"', open('pyproject.toml').read()).group(1))")
VENV_DIR=$(mktemp -d)
trap 'rm -rf "$VENV_DIR"' EXIT

# ── Preflight ────────────────────────────────────────────────────────────────

if [ -z "${TESTPYPI_TOKEN:-}" ]; then
    echo -e "${RED}Error: TESTPYPI_TOKEN is not set.${NC}"
    echo "Get a token from https://test.pypi.org/manage/account/#api-tokens"
    echo "Usage: TESTPYPI_TOKEN=pypi-... bash scripts/test-publish.sh"
    exit 1
fi

# ── 1. Build ─────────────────────────────────────────────────────────────────

section "1. Build"
rm -rf dist/
uv build 2>&1 | tail -2
WHL=$(ls dist/*.whl | head -1)
pass "Built $(basename $WHL)"

# ── 2. Check package ────────────────────────────────────────────────────────

section "2. Package inspection"
bash scripts/check-package.sh 2>&1 | grep -E "PASS|FAIL" | head -10
pass "Package inspection complete"

# ── 3. Upload to TestPyPI ───────────────────────────────────────────────────

section "3. Upload to TestPyPI"
uv publish \
    --publish-url https://test.pypi.org/legacy/ \
    --token "$TESTPYPI_TOKEN" \
    dist/* 2>&1 | tail -3
pass "Uploaded to TestPyPI"

# ── 4. Install from TestPyPI ────────────────────────────────────────────────

section "4. Install from TestPyPI"
uv venv --python 3.13 --seed "$VENV_DIR" 2>/dev/null

echo "  Waiting for TestPyPI index to update..."
INSTALLED=false
for attempt in 1 2 3 4 5; do
    if "$VENV_DIR/bin/pip" install \
        --index-url https://test.pypi.org/simple/ \
        --extra-index-url https://pypi.org/simple/ \
        "sagewai==$PKG_VERSION" 2>&1 | tail -3; then
        INSTALLED=true
        break
    fi
    echo "  Retry $attempt/5 (waiting $((attempt * 5))s)..."
    sleep $((attempt * 5))
done
if $INSTALLED; then pass "Installed from TestPyPI"; else fail "Install from TestPyPI"; fi

# ── 5. Verify install ───────────────────────────────────────────────────────

section "5. Verify installed package"
PY="$VENV_DIR/bin/python"
CLI="$VENV_DIR/bin/sagewai"

VERSION=$($CLI version 2>&1)
if echo "$VERSION" | grep -q "$PKG_VERSION"; then pass "sagewai version: $VERSION"; else fail "version: $VERSION"; fi

$CLI doctor 2>&1 | head -3
pass "sagewai doctor runs"

# Core imports
$PY -c "
from sagewai import UniversalAgent, tool, AgentConfig, ChatMessage
from sagewai.memory.graph import GraphMemory
from sagewai.safety.pii import PIIGuard
from sagewai.core.registry import AgentRegistry
from sagewai.auth.jwt import JWTAuth
from sagewai.harness.store import InMemoryHarnessStore
from sagewai.observability.costs import CostTracker
" 2>&1 && pass "Core imports" || fail "Core imports"

# ── 6. Functional smoke test ────────────────────────────────────────────────

section "6. Functional smoke test"
$PY -c "
import asyncio
from sagewai.memory.graph import GraphMemory
from sagewai.safety.pii import PIIGuard
from sagewai.auth.jwt import JWTAuth
from sagewai.core.registry import AgentRegistry
from sagewai import UniversalAgent

async def main():
    # Memory
    mem = GraphMemory()
    await mem.store('TestPyPI round-trip test')
    entities = await mem.list_entities()
    assert len(entities) > 0, 'Memory: no entities'

    # PII
    guard = PIIGuard(action='redact')
    r = await guard.check_input('test@example.com', context={})
    assert not r.passed, 'PII: not detected'

    # JWT
    auth = JWTAuth(secret='test-secret-32-characters-long!!')
    tok = auth.create_token({'sub': 'test'})
    assert auth.verify_token(tok)['sub'] == 'test', 'JWT: verify failed'

    # Registry
    reg = AgentRegistry()
    agent = UniversalAgent(name='smoke', model='gpt-4o-mini')
    reg.register(agent, capabilities=['test'])
    assert reg.get('smoke') is not None, 'Registry: not found'
    reg.clear()

    print('ALL FUNCTIONAL TESTS PASSED')

asyncio.run(main())
" 2>&1 && pass "Functional smoke test" || fail "Functional smoke test"

# ── Summary ──────────────────────────────────────────────────────────────────

section "Summary"
echo -e "  ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"
if [ $FAIL -eq 0 ]; then
    echo -e "\n  ${GREEN}TestPyPI round-trip succeeded.${NC}"
    echo "  View: https://test.pypi.org/project/sagewai/$PKG_VERSION/"
else
    echo -e "\n  ${RED}TestPyPI round-trip had failures. Fix before real publish.${NC}"
fi
exit $FAIL
