#!/usr/bin/env bash
# e2e-test.sh — Run E2E tests against the local SDK.
# Usage:
#   bash scripts/e2e-test.sh --offline    # No API keys needed
#   bash scripts/e2e-test.sh --live       # Needs OPENAI_API_KEY etc.
#   bash scripts/e2e-test.sh --cli        # CLI subcommand smoke tests
#   bash scripts/e2e-test.sh --all        # Everything
set -euo pipefail

# Ensure uv is in PATH
if ! command -v uv &>/dev/null; then
    export PATH="$HOME/.local/bin:$PATH"
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0; SKIP=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }
skip() { echo -e "  ${YELLOW}SKIP${NC} $1"; SKIP=$((SKIP + 1)); }
section() { echo -e "\n${BLUE}── $1 ──${NC}"; }

run_python() {
    if uv run python -c "$1" 2>&1; then
        pass "$2"
    else
        fail "$2"
    fi
}

MODE="${1:---all}"

# ══════════════════════════════════════════════════════════════════════════════
# OFFLINE TESTS (no API keys, no external services)
# ══════════════════════════════════════════════════════════════════════════════

run_offline() {
    section "Memory (GraphMemory)"
    run_python "
import asyncio
from sagewai.memory.graph import GraphMemory
async def main():
    m = GraphMemory()
    await m.store('Alice works at Acme Corp.')
    await m.add_relation('Alice', 'works_at', 'Acme Corp')
    assert len(await m.list_entities()) > 0
    assert len(await m.get_neighbors('Alice')) > 0
asyncio.run(main())
" "Memory store + relations + neighbors"

    section "PII Detection"
    run_python "
import asyncio
from sagewai.safety.pii import PIIGuard
async def main():
    g = PIIGuard(action='redact')
    r = await g.check_input('Email me at alice@example.com', context={})
    assert not r.passed and 'EMAIL' in r.violation.upper()
asyncio.run(main())
" "PIIGuard detects email"

    section "Harness Key + Classification"
    run_python "
import asyncio
from sagewai.harness.models import HarnessKey
from sagewai.harness.store import InMemoryHarnessStore
from sagewai.harness.classifier import RequestClassifier
async def main():
    store = InMemoryHarnessStore()
    key = HarnessKey(name='test', user_id='alice', org_id='acme', max_budget_daily_usd=5.0)
    pt = await store.create_key(key)
    assert pt.startswith('sk-harness-')
    identity = await store.validate_key(pt)
    assert identity.user_id == 'alice'
    c = RequestClassifier()
    assert c.classify(messages=[{'role':'user','content':'fix typo'}]).tier.value == 'simple'
asyncio.run(main())
" "Harness key lifecycle + classifier"

    section "Agent Registry"
    run_python "
from sagewai import UniversalAgent
from sagewai.core.registry import AgentRegistry
r = AgentRegistry()
a = UniversalAgent(name='test', model='gpt-4o-mini')
r.register(a, capabilities=['test'])
assert r.get('test') is not None
r.unregister('test')
assert r.get('test') is None
r.clear()
" "Register + discover + unregister"

    section "Observatory (Audit + Cost)"
    run_python "
import asyncio
from sagewai.observability.audit import AuditEvent, AuditLogger, InMemoryAuditBackend
from sagewai.observability.costs import CostTracker
async def main():
    b = InMemoryAuditBackend()
    l = AuditLogger(backends=[b])
    l.log(AuditEvent(action='agent.chat', agent_name='t', model='gpt-4o', tokens_used=150))
    await l.flush()
    assert len(b.events) == 1
    t = CostTracker(); t.start_run('t'); t.record_call(model='gpt-4o', input_tokens=100, output_tokens=50); t.end_run()
    assert len(t.runs) == 1
asyncio.run(main())
" "Audit events + cost tracking"

    section "Security (SSRF + Permissions + Trust + JWT)"
    run_python "
from sagewai.context.url_parser import _is_private_ip, _validate_url
from sagewai.safety.permissions import PermissionPolicy, PermissionLevel
from sagewai.core.trust import TrustLevel, DeferredInit
from sagewai.auth.jwt import JWTAuth
assert _is_private_ip('127.0.0.1')
assert _is_private_ip('169.254.169.254')
assert not _is_private_ip('8.8.8.8')
try: _validate_url('http://127.0.0.1/admin'); assert False
except ValueError: pass
p = PermissionPolicy(default_level=PermissionLevel.READ, deny_prefixes=['delete_'])
assert p.check('get_users').allowed
assert not p.check('delete_db').allowed
d = DeferredInit(); assert d.trust_level == TrustLevel.UNTRUSTED
d.elevate(TrustLevel.TRUSTED); assert d.trust_level == TrustLevel.TRUSTED
a = JWTAuth(secret='test-secret-32-characters-long!!')
t = a.create_token({'sub':'alice'}); assert a.verify_token(t)['sub'] == 'alice'
" "SSRF + permissions + trust + JWT"

    section "Smoke Tests"
    local smoke_out
    smoke_out=$(uv run pytest tests/test_smoke.py -v -o "addopts=" 2>&1) && local smoke_rc=0 || local smoke_rc=$?
    echo "$smoke_out" | tail -5
    if [ $smoke_rc -eq 0 ]; then pass "29 smoke tests"; else fail "Smoke tests (exit $smoke_rc)"; fi
}

# ══════════════════════════════════════════════════════════════════════════════
# CLI TESTS
# ══════════════════════════════════════════════════════════════════════════════

run_cli() {
    section "CLI Commands"

    for cmd in "version" "doctor" "status" "--help" \
               "agent --help" "token --help" "budget --help" \
               "workflow --help" "eval --help" "model --help" \
               "strategy --help" "prompt --help" "session --help" \
               "mcp --help" "fleet --help" "memory --help" \
               "admin --help" "db --help" "safety --help" \
               "init --help" "run --help" "worker --help"; do
        if uv run sagewai $cmd >/dev/null 2>&1; then
            pass "sagewai $cmd"
        else
            fail "sagewai $cmd"
        fi
    done

    # Test init scaffold
    TMPDIR=$(mktemp -d)
    if uv run sagewai init "$TMPDIR/test-project" >/dev/null 2>&1 && [ -f "$TMPDIR/test-project/main.py" ]; then
        pass "sagewai init (scaffold created)"
    else
        fail "sagewai init"
    fi
    rm -rf "$TMPDIR"
}

# ══════════════════════════════════════════════════════════════════════════════
# LIVE LLM TESTS (need API keys)
# ══════════════════════════════════════════════════════════════════════════════

run_live() {
    section "Agent Creation + Chat"
    if [ -z "${OPENAI_API_KEY:-}" ]; then
        skip "OPENAI_API_KEY not set"
    else
        run_python "
import asyncio
from sagewai import UniversalAgent
async def main():
    a = UniversalAgent(name='test', model='gpt-4o-mini')
    r = await a.chat('What is 2+2? Reply with just the number.')
    assert '4' in r, f'Expected 4, got: {r}'
asyncio.run(main())
" "Agent chat (gpt-4o-mini)"
    fi

    section "Custom Tools"
    if [ -z "${OPENAI_API_KEY:-}" ]; then
        skip "OPENAI_API_KEY not set"
    else
        run_python "
import asyncio
from sagewai import UniversalAgent, tool
@tool
def get_weather(city: str) -> str:
    '''Get weather for a city.'''
    return f'Sunny, 22C in {city}'
async def main():
    a = UniversalAgent(name='w', model='gpt-4o-mini', tools=[get_weather])
    r = await a.chat('What is the weather in Berlin?')
    assert 'Berlin' in r or '22' in r, f'Tool not called: {r}'
asyncio.run(main())
" "Tool calling (@tool decorator)"
    fi

    section "Multi-Model"
    for model_env in "gpt-4o-mini:OPENAI_API_KEY" "claude-haiku-4-5-20251001:ANTHROPIC_API_KEY" "gemini/gemini-2.5-flash:GOOGLE_API_KEY"; do
        model="${model_env%%:*}"
        env_var="${model_env##*:}"
        if [ -z "${!env_var:-}" ]; then
            skip "$model ($env_var not set)"
        else
            run_python "
import asyncio
from sagewai import UniversalAgent
async def main():
    a = UniversalAgent(name='multi', model='$model')
    r = await a.chat('Say hello in one word.')
    assert len(r) > 0
asyncio.run(main())
" "$model"
        fi
    done

    section "Full Integration (tools + memory + guardrails)"
    if [ -z "${OPENAI_API_KEY:-}" ]; then
        skip "OPENAI_API_KEY not set"
    else
        run_python "
import asyncio
from sagewai import UniversalAgent, tool
from sagewai.memory.graph import GraphMemory
from sagewai.safety.pii import PIIGuard
@tool
def lookup_customer(name: str) -> str:
    '''Look up a customer by name.'''
    return f'{name} is a premium customer since 2023.'
async def main():
    agent = UniversalAgent(
        name='support', model='gpt-4o-mini',
        tools=[lookup_customer], memory=GraphMemory(),
        guardrails=[PIIGuard(action='redact')],
        system_prompt='You are a customer support agent.',
    )
    r1 = await agent.chat('Look up customer Alice.')
    r2 = await agent.chat('What do you know about Alice?')
    assert 'Alice' in r1 or 'premium' in r1
asyncio.run(main())
" "Full integration (tools + memory + guardrails)"
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

echo -e "${BLUE}Sagewai E2E Test Suite${NC}"
echo "Mode: $MODE"

case "$MODE" in
    --offline) run_offline ;;
    --live)    run_live ;;
    --cli)     run_cli ;;
    --all)     run_offline; run_cli; run_live ;;
    *)         echo "Usage: $0 [--offline|--live|--cli|--all]"; exit 1 ;;
esac

echo -e "\n${BLUE}── Results ──${NC}"
echo -e "  ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC}"
[ $FAIL -eq 0 ] || exit 1
