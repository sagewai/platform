# Changelog

All notable changes to the Sagewai SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added

- Core SDK with BaseAgent, UniversalAgent, GoogleNativeAgent
- Multi-model support via LiteLLM (GPT-4o, Claude, Gemini, Mistral, Ollama, and more)
- Tool system with @tool decorator and MCP protocol support
- Agentic strategies: ReAct, LATS, Tree of Thoughts, planning, self-correction
- Durable workflows with checkpoint-based persistence
- Memory module: Milvus vector store, NebulaGraph graph store, RAG pipeline
- Context Engine: document ingestion, multi-strategy retrieval (vector + BM25 + graph)
- Directive Engine: @context, @memory, @agent prompt preprocessing
- Intelligence Layer: embeddings, entity extraction, language detection, summarization
- Safety: guardrails engine, PII detection, content filtering, permission policies
- LLM Harness: smart proxy with complexity routing, policy engine, budget enforcement
- Fleet management: distributed workers, load balancing, zero-trust security
- Gateway: OpenAI-compatible endpoint, access tokens, webhooks, triggers
- Admin module: health monitoring, analytics, budget management, run control
- Observability: OpenTelemetry tracing, Prometheus metrics, cost tracking, audit logging
- Notifications: SMTP email, Slack webhook, in-app SSE channels
- 40 built-in connectors (Slack, GitHub, Jira, Salesforce, SAP, and more)
- CLI: init, doctor, agent run, workflow run, harness start
- VS Code extension: syntax highlighting, snippets, scaffolding commands
- 21 progressive examples covering all 5 pillars
- AGPL-3.0 licensing with commercial dual-license option
- Trademark policy and derivative work protection
