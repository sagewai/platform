# Quickstart

Get a working agent in under 2 minutes.

## Install

```bash
pip install sagewai
```

## Your first agent

```python
import asyncio
from sagewai import UniversalAgent

async def main():
    agent = UniversalAgent(name="assistant", model="gpt-4o-mini")
    response = await agent.chat("What is the capital of France?")
    print(response)

asyncio.run(main())
```

Set your API key first: `export OPENAI_API_KEY=sk-...`

Works with any model — swap `gpt-4o-mini` for `claude-haiku-4-5-20251001` (Anthropic) or `gemini/gemini-2.5-flash` (Google).

## Add tools

```python
from sagewai import UniversalAgent, tool

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"Sunny, 22C in {city}"

agent = UniversalAgent(
    name="weather-bot",
    model="gpt-4o-mini",
    tools=[get_weather],
)
```

The agent calls `get_weather` automatically when asked about weather.

## Add memory

```python
from sagewai import UniversalAgent
from sagewai.memory.graph import GraphMemory

agent = UniversalAgent(
    name="assistant",
    model="gpt-4o-mini",
    memory=GraphMemory(),
)
```

The agent remembers facts across conversations — entities and relations are stored in an in-memory knowledge graph.

## Add guardrails

```python
from sagewai import UniversalAgent
from sagewai.safety.pii import PIIGuard

agent = UniversalAgent(
    name="safe-bot",
    model="gpt-4o-mini",
    guardrails=[PIIGuard(action="redact")],
)
```

PII (emails, phone numbers, SSNs) is automatically detected and redacted.

## CLI

```bash
# Check your setup
sagewai doctor

# Scaffold a new project
sagewai init my-project
cd my-project

# Start the admin API
sagewai admin serve
```

## Start an MCP server

Expose your agent as an MCP server for Claude Code, Cursor, or any MCP client:

```python
from sagewai import UniversalAgent
from sagewai.mcp.server import McpServer

agent = UniversalAgent(name="my-agent", model="gpt-4o-mini")
server = McpServer.from_agent(agent)

# Stdio transport (for Claude Code / Cursor)
import asyncio
asyncio.run(server.run_stdio())
```

## Run the admin panel

```bash
# Install with FastAPI support
pip install 'sagewai[fastapi]'

# Start the admin API server
sagewai admin serve --port 8000

# Visit http://localhost:8000/docs for the interactive API
```

## Local models (no API key)

Run agents with zero cloud costs using Ollama:

```bash
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull llama3.1:8b
```

```python
from sagewai import UniversalAgent, providers

agent = UniversalAgent(
    name="local-bot",
    model="llama3.1:8b",
    **providers.ollama("llama3.1:8b"),
)
```

See the [Local Inference guide](https://docs.sagewai.ai/docs/guides/local-inference) for vLLM, LM Studio, llama.cpp, and Unsloth.

## Use from any language

Start the harness proxy, then connect from TypeScript, Go, Rust, or any of 17 supported languages:

```bash
sagewai harness start --port 8100
```

```typescript
// TypeScript
import { SagewaiClient } from "sagewai";

const client = new SagewaiClient({
  baseUrl: "http://localhost:8100",
  apiKey: "sk-harness-...",
});
const response = await client.chat({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Hello from TypeScript!" }],
});
```

See the [Client Wrappers guide](https://docs.sagewai.ai/docs/guides/client-wrappers) for all 17 languages.

## What's next

- [Tutorials](https://docs.sagewai.ai/docs/guides/tutorials) — 8 step-by-step tutorials from beginner to enterprise
- [Examples](sagewai/examples/) — 23 runnable examples covering every feature
- [API Reference](https://docs.sagewai.ai) — full documentation
- [GitHub](https://github.com/sagewai/platform) — source code and issues
