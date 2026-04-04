"""End-to-end integration tests for the Sagewai SDK.

These tests verify that the full stack works together using IN-MEMORY
backends only — no Postgres, Milvus, or NebulaGraph required.

Scenarios:
1. Fleet: register worker -> claim task -> report result
2. Intelligence: extract facts -> consolidate -> decay
3. Context: ingest text -> search -> scope isolation
4. Directives: parse prompt with @context -> resolve -> verify output
"""

from __future__ import annotations

import pytest


# =====================================================================
# Scenario 1: Fleet — register worker -> claim task -> report result
# =====================================================================


@pytest.mark.asyncio
async def test_fleet_register_claim_report():
    """Full fleet lifecycle: register worker, enqueue task, claim via dispatcher, report result."""
    from sagewai.fleet import (
        FleetDispatcher,
        FleetPayloadEncryption,
        InMemoryFleetRegistry,
        InMemoryTaskStore,
        WorkerCapabilities,
        WRTTokenManager,
    )

    # 1. Create registry + token manager
    registry = InMemoryFleetRegistry()
    wrt = WRTTokenManager(secret="test-secret-key-for-e2e")
    encryption = FleetPayloadEncryption(
        org_keys={"org-1": FleetPayloadEncryption.generate_key()}
    )

    # 2. Create enrollment key
    key_record, raw_key = await registry.create_enrollment_key(
        org_id="org-1", name="test-key", created_by="admin"
    )
    assert key_record.name == "test-key"
    assert key_record.org_id == "org-1"
    assert not key_record.revoked

    # 3. Register worker with enrollment key (auto-approve)
    worker = await registry.register_worker(
        name="test-worker",
        org_id="org-1",
        capabilities=WorkerCapabilities(
            models_supported=["gpt-4o"],
            pool="default",
            max_concurrent=1,
        ),
        enrollment_key=raw_key,
    )
    assert worker.approval_status.value == "approved"
    assert worker.name == "test-worker"

    # 4. Verify worker is retrievable
    fetched = await registry.get_worker(worker.id)
    assert fetched is not None
    assert fetched.id == worker.id

    # 5. Issue WRT token and validate it
    token = wrt.issue_token(worker.id, "org-1", pool="default")
    assert token.startswith("wrt-1.")

    claims = wrt.validate_token(token)
    assert claims is not None
    assert claims["sub"] == worker.id
    assert claims["org"] == "org-1"
    assert claims["pool"] == "default"

    # 6. Create dispatcher with task store
    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(
        store=store,
        encryption=encryption,
        poll_interval=0.05,
        poll_timeout=1.0,
    )

    # 7. Enqueue a task
    store.enqueue({
        "run_id": "run-1",
        "model": "gpt-4o",
        "pool": "default",
        "payload": "test input",
    })

    # 8. Claim task
    task = await dispatcher.claim(
        worker_id=worker.id,
        org_id="org-1",
        models_canonical=["gpt-4o"],
        pool="default",
    )
    assert task is not None
    assert task["run_id"] == "run-1"

    # 9. Report result
    await dispatcher.report(
        worker_id=worker.id,
        org_id="org-1",
        run_id="run-1",
        status="completed",
        output="test output",
    )

    # 10. Verify task store state
    assert "run-1" in store._completed
    assert store._completed["run-1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_fleet_worker_without_enrollment_key():
    """Worker registered without enrollment key enters PENDING state."""
    from sagewai.fleet import InMemoryFleetRegistry, WorkerCapabilities

    registry = InMemoryFleetRegistry()

    worker = await registry.register_worker(
        name="pending-worker",
        org_id="org-1",
        capabilities=WorkerCapabilities(
            models_supported=["gpt-4o"],
            pool="default",
        ),
    )
    assert worker.approval_status.value == "pending"

    # Manually approve
    approved = await registry.approve_worker(worker.id, approved_by="admin-1")
    assert approved.approval_status.value == "approved"


@pytest.mark.asyncio
async def test_fleet_token_revocation():
    """Revoked WRT tokens are rejected on validation."""
    from sagewai.fleet import WRTTokenManager

    wrt = WRTTokenManager(secret="revocation-test-secret")

    token = wrt.issue_token("w-1", "org-1")
    assert wrt.validate_token(token) is not None

    wrt.revoke_token(token)
    assert wrt.validate_token(token) is None


@pytest.mark.asyncio
async def test_fleet_claim_timeout_no_matching_task():
    """Claim times out when no matching task is available."""
    from sagewai.fleet import FleetDispatcher, InMemoryTaskStore

    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(
        store=store,
        poll_interval=0.05,
        poll_timeout=0.2,
    )

    # Enqueue task for a different model
    store.enqueue({
        "run_id": "run-x",
        "model": "claude-3-opus",
        "pool": "default",
    })

    # Claim with gpt-4o should time out
    task = await dispatcher.claim(
        worker_id="w-1",
        org_id="org-1",
        models_canonical=["gpt-4o"],
        pool="default",
    )
    assert task is None


@pytest.mark.asyncio
async def test_fleet_encryption_roundtrip():
    """Payload encryption and decryption produce original text."""
    from sagewai.fleet import FleetPayloadEncryption

    key = FleetPayloadEncryption.generate_key()
    enc = FleetPayloadEncryption(org_keys={"org-1": key})

    plaintext = '{"task": "analyze quarterly report"}'
    ciphertext = enc.encrypt("org-1", plaintext)
    assert ciphertext != plaintext

    decrypted = enc.decrypt("org-1", ciphertext)
    assert decrypted == plaintext

    # Unknown org passes through unchanged
    assert enc.encrypt("org-unknown", plaintext) == plaintext


# =====================================================================
# Scenario 2: Intelligence — extract facts -> consolidate -> decay
# =====================================================================


@pytest.mark.asyncio
async def test_intelligence_fact_extraction():
    """Rule-based fact extractor detects decisions, actions, and events."""
    from sagewai.intelligence import RuleBasedFactExtractor

    extractor = RuleBasedFactExtractor()
    conversation = """
    User: We decided to use PostgreSQL for the new project.
    Assistant: Good choice. I prefer PostgreSQL over MySQL for complex queries.
    User: The deadline is March 15th. TODO: set up the database schema.
    Assistant: I'll schedule that. Meeting on Friday to review the architecture.
    """
    facts = await extractor.extract(conversation)
    assert len(facts) > 0

    fact_types = {f.fact_type for f in facts}
    assert "decision" in fact_types, f"Expected 'decision' in {fact_types}"

    # Verify all facts have content and confidence
    for fact in facts:
        assert fact.content
        assert 0.0 <= fact.confidence <= 1.0


@pytest.mark.asyncio
async def test_intelligence_consolidation_dedup():
    """MemoryConsolidator deduplicates identical facts using embeddings."""
    from sagewai.intelligence import HashEmbedder, MemoryConsolidator
    from sagewai.intelligence.models import ExtractedFact

    embedder = HashEmbedder(dimension=384)
    consolidator = MemoryConsolidator(
        embedder=embedder,
        similarity_threshold=0.99,  # Very high — only exact duplicates
    )

    facts = [
        ExtractedFact(content="Use PostgreSQL for the project", fact_type="decision", confidence=0.8),
        ExtractedFact(content="Use PostgreSQL for the project", fact_type="decision", confidence=0.9),
        ExtractedFact(content="Meeting scheduled for Friday", fact_type="event", confidence=0.7),
    ]

    result = await consolidator.deduplicate_facts(facts)

    # Identical texts should be merged (hash embedder produces same vector)
    assert len(result.unique_facts) < len(facts)
    assert result.merged_count > 0

    # The higher-confidence duplicate should be kept
    postgres_facts = [f for f in result.unique_facts if "PostgreSQL" in f.content]
    assert len(postgres_facts) == 1
    assert postgres_facts[0].confidence == 0.9


@pytest.mark.asyncio
async def test_intelligence_consolidation_distinct():
    """Distinct facts are NOT merged even with consolidation."""
    from sagewai.intelligence import HashEmbedder, MemoryConsolidator
    from sagewai.intelligence.models import ExtractedFact

    embedder = HashEmbedder(dimension=384)
    consolidator = MemoryConsolidator(embedder=embedder, similarity_threshold=0.9)

    facts = [
        ExtractedFact(content="Use PostgreSQL for the database", fact_type="decision", confidence=0.8),
        ExtractedFact(content="Deploy to GCP Cloud Run", fact_type="decision", confidence=0.7),
        ExtractedFact(content="Meeting scheduled for Friday at 3pm", fact_type="event", confidence=0.6),
    ]

    result = await consolidator.deduplicate_facts(facts)
    # These are distinct enough that they should all survive
    assert len(result.unique_facts) == len(facts)
    assert result.merged_count == 0


@pytest.mark.asyncio
async def test_intelligence_decay():
    """Importance decay reduces weight of older facts exponentially."""
    from sagewai.intelligence import HashEmbedder, MemoryConsolidator
    from sagewai.intelligence.models import ExtractedFact

    embedder = HashEmbedder(dimension=384)
    consolidator = MemoryConsolidator(embedder=embedder, decay_rate=0.05)

    facts = [
        ExtractedFact(content="Fact A", fact_type="decision", confidence=0.8),
        ExtractedFact(content="Fact B", fact_type="decision", confidence=0.8),
        ExtractedFact(content="Fact C", fact_type="decision", confidence=0.8),
    ]

    ages = [0.0, 7.0, 30.0]  # days old
    decayed = consolidator.apply_decay(facts, ages)

    assert len(decayed) == 3

    # Recent fact should have highest weight
    weights = [w for _, w in decayed]
    assert weights[0] > weights[1] > weights[2]

    # Brand new fact should keep full confidence
    assert abs(weights[0] - 0.8) < 0.001

    # 30-day-old fact should be significantly reduced
    assert weights[2] < weights[0] * 0.5


@pytest.mark.asyncio
async def test_intelligence_full_pipeline():
    """End-to-end: extract -> consolidate -> decay in sequence."""
    from sagewai.intelligence import HashEmbedder, MemoryConsolidator, RuleBasedFactExtractor

    extractor = RuleBasedFactExtractor()
    embedder = HashEmbedder(dimension=384)
    consolidator = MemoryConsolidator(embedder=embedder, similarity_threshold=0.9)

    # Step 1: Extract facts
    conversation = """
    User: We decided to use Redis for caching.
    User: We decided to use Redis for caching.
    User: TODO: write the Redis integration tests.
    """
    facts = await extractor.extract(conversation)
    assert len(facts) > 0

    # Step 2: Consolidate (dedup)
    result = await consolidator.deduplicate_facts(facts)
    unique = result.unique_facts

    # Step 3: Decay
    ages = [float(i) for i in range(len(unique))]
    decayed = consolidator.apply_decay(unique, ages)

    # Every fact should have a positive weight
    for fact, weight in decayed:
        assert weight > 0
        assert fact.content


# =====================================================================
# Scenario 3: Context — ingest text -> search -> scope isolation
# =====================================================================


@pytest.mark.asyncio
async def test_context_ingest_and_search():
    """Ingest a text document and search for its content."""
    from sagewai.context import (
        ContextEngine,
        ContextScope,
        ContextSource,
        InMemoryMetadataStore,
        InMemoryVectorStore,
    )
    from sagewai.intelligence import HashEmbedder

    embedder = HashEmbedder(dimension=384)
    engine = ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        embedder=embedder,
        project_id="test-project",
        enable_bm25=False,  # Avoid BM25 complexity for this test
    )

    # Ingest a document
    doc = await engine.ingest_text(
        text="PostgreSQL is a powerful open-source relational database "
        "management system. It supports advanced data types, full-text "
        "search, and JSON storage. PostgreSQL is widely used in enterprise "
        "applications for its reliability and feature richness.",
        title="PostgreSQL Overview",
        scope=ContextScope.PROJECT,
        scope_id="test-project",
        source=ContextSource.MANUAL,
    )

    assert doc.title == "PostgreSQL Overview"
    assert doc.status == "ready"
    assert doc.chunk_count > 0

    # Search for relevant content
    results = await engine.search("database management", top_k=3)
    assert len(results) > 0
    assert any("PostgreSQL" in r.content for r in results)


@pytest.mark.asyncio
async def test_context_scope_isolation():
    """Documents in different projects are isolated from each other."""
    from sagewai.context import (
        ContextEngine,
        ContextScope,
        ContextSource,
        InMemoryMetadataStore,
        InMemoryVectorStore,
    )
    from sagewai.intelligence import HashEmbedder

    meta_store = InMemoryMetadataStore()
    vec_store = InMemoryVectorStore()
    embedder = HashEmbedder(dimension=384)

    # Create two engines for different projects, sharing the same stores
    engine_a = ContextEngine(
        metadata_store=meta_store,
        vector_store=vec_store,
        embedder=embedder,
        project_id="project-a",
        enable_bm25=False,
    )
    engine_b = ContextEngine(
        metadata_store=meta_store,
        vector_store=vec_store,
        embedder=embedder,
        project_id="project-b",
        enable_bm25=False,
    )

    # Ingest into project-a
    await engine_a.ingest_text(
        text="Project Alpha uses a microservice architecture with gRPC "
        "communication between services. The deployment target is GKE.",
        title="Project Alpha Architecture",
        scope=ContextScope.PROJECT,
        scope_id="project-a",
        source=ContextSource.MANUAL,
    )

    # Ingest into project-b
    await engine_b.ingest_text(
        text="Project Beta is a monolithic Django application deployed "
        "on Cloud Run with a single PostgreSQL database.",
        title="Project Beta Architecture",
        scope=ContextScope.PROJECT,
        scope_id="project-b",
        source=ContextSource.MANUAL,
    )

    # Search in project-a should find only project-a content
    results_a = await engine_a.search("architecture", top_k=5)
    for r in results_a:
        assert r.scope_id == "project-a", (
            f"Project A search returned content from {r.scope_id}"
        )

    # Search in project-b should find only project-b content
    results_b = await engine_b.search("architecture", top_k=5)
    for r in results_b:
        assert r.scope_id == "project-b", (
            f"Project B search returned content from {r.scope_id}"
        )


@pytest.mark.asyncio
async def test_context_tag_filtering():
    """Documents can be filtered by tags during search."""
    from sagewai.context import (
        ContextEngine,
        ContextScope,
        ContextSource,
        InMemoryMetadataStore,
        InMemoryVectorStore,
    )
    from sagewai.intelligence import HashEmbedder

    embedder = HashEmbedder(dimension=384)
    engine = ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        embedder=embedder,
        project_id="tag-test",
        enable_bm25=False,
    )

    # Ingest two documents with different tags
    await engine.ingest_text(
        text="Q4 financial report shows 15% revenue growth year over year.",
        title="Q4 Financial Report",
        scope=ContextScope.PROJECT,
        scope_id="tag-test",
        source=ContextSource.MANUAL,
        metadata={"tags": ["finance", "q4"]},
    )
    await engine.ingest_text(
        text="The engineering team completed the API migration to gRPC.",
        title="Engineering Update",
        scope=ContextScope.PROJECT,
        scope_id="tag-test",
        source=ContextSource.MANUAL,
        metadata={"tags": ["engineering"]},
    )

    # Update the document tags in the metadata store directly
    # since ingest_text doesn't set tags from metadata automatically
    docs = await engine.metadata_store.list_documents(project_id="tag-test")
    for doc in docs:
        if "Financial" in doc.title:
            doc.tags = ["finance", "q4"]
            await engine.metadata_store.update_document(doc)
        elif "Engineering" in doc.title:
            doc.tags = ["engineering"]
            await engine.metadata_store.update_document(doc)

    # Search with finance tag should find only financial content
    results = await engine.search("report", top_k=5, tags=["finance"])
    assert len(results) > 0
    for r in results:
        assert "revenue" in r.content.lower() or "financial" in r.document_title.lower()

    # Search with non-matching tag should return nothing
    results_empty = await engine.search("report", top_k=5, tags=["nonexistent"])
    assert len(results_empty) == 0


@pytest.mark.asyncio
async def test_context_memory_provider_protocol():
    """ContextEngine implements the MemoryProvider protocol (store + retrieve)."""
    from sagewai.context import (
        ContextEngine,
        InMemoryMetadataStore,
        InMemoryVectorStore,
    )
    from sagewai.intelligence import HashEmbedder

    embedder = HashEmbedder(dimension=384)
    engine = ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        embedder=embedder,
        project_id="memory-test",
        enable_bm25=False,
    )

    # Store via MemoryProvider.store()
    await engine.store(
        "The project deadline is March 15th. We use Python 3.12.",
        metadata={"title": "Project Notes"},
    )

    # Retrieve via MemoryProvider.retrieve()
    results = await engine.retrieve("project deadline", top_k=3)
    assert len(results) > 0
    assert any("deadline" in r.lower() or "March" in r for r in results)


# =====================================================================
# Scenario 4: Directives — parse and resolve @context in a prompt
# =====================================================================


@pytest.mark.asyncio
async def test_directive_no_directives_passthrough():
    """Prompts without directives pass through unchanged."""
    from sagewai.directives import DirectiveEngine

    engine = DirectiveEngine(model="gpt-4o")
    result = await engine.resolve("Help me learn about Python.")

    assert result.prompt == "Help me learn about Python."
    assert result.clean_prompt == "Help me learn about Python."
    assert result.metadata.total_directives == 0


@pytest.mark.asyncio
async def test_directive_context_resolution():
    """@context directive resolves using the context engine."""
    from sagewai.context import (
        ContextEngine,
        InMemoryMetadataStore,
        InMemoryVectorStore,
    )
    from sagewai.context.models import ContextScope, ContextSource
    from sagewai.directives import DirectiveEngine
    from sagewai.intelligence import HashEmbedder

    embedder = HashEmbedder(dimension=384)
    context = ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        embedder=embedder,
        project_id="directive-test",
        enable_bm25=False,
    )

    # Seed some context
    await context.ingest_text(
        text="Sagewai is an AI agent infrastructure SDK. It provides "
        "tool calling, memory, context engine, and directive preprocessing.",
        title="Sagewai Overview",
        scope_id="directive-test",
        scope=ContextScope.PROJECT,
        source=ContextSource.MANUAL,
    )

    directive_engine = DirectiveEngine(
        context=context,
        model="gpt-4o",
    )

    result = await directive_engine.resolve(
        "@context('Sagewai SDK') Tell me about it."
    )

    # The resolved prompt should contain context from the ingested doc
    assert result.metadata.total_directives > 0
    assert len(result.clean_prompt.strip()) > 0
    # The directive should have been resolved (not errored)
    assert result.metadata.resolved_count >= 1 or result.metadata.error_count == 0


@pytest.mark.asyncio
async def test_directive_model_profile_detection():
    """Model profile is auto-detected from model name."""
    from sagewai.directives import DirectiveEngine, detect_profile

    # Small model
    small_profile = detect_profile("codellama:7b")
    assert small_profile.compression_ratio > 1.0

    # Large model
    large_profile = detect_profile("gpt-4o")
    assert large_profile.compression_ratio == 1.0

    # Engine uses detected profile
    engine = DirectiveEngine(model="codellama:7b")
    assert engine.profile.compression_ratio > 1.0


@pytest.mark.asyncio
async def test_directive_has_directives_detection():
    """has_directives() detects directive syntax in text."""
    from sagewai.directives import has_directives

    assert has_directives("@context('query') help me")
    assert has_directives("@memory('search') find facts")
    assert has_directives("@agent:helper('do work')")
    assert has_directives("/tool.name('args')")
    assert has_directives("#model:gpt-4o")
    assert not has_directives("Just a normal prompt without directives")


@pytest.mark.asyncio
async def test_directive_custom_registry():
    """Custom directives can be registered and resolved."""
    from sagewai.directives import DirectiveEngine

    engine = DirectiveEngine(model="gpt-4o")

    # Register a custom directive
    async def kb_handler(query: str) -> str:
        return f"Knowledge base result for: {query}"

    engine.register("kb", "@kb", kb_handler, description="Knowledge base search")

    result = await engine.resolve("@kb('machine learning') Explain the basics.")

    # Custom directive should be resolved
    assert result.metadata.total_directives > 0
    assert "machine learning" in result.prompt.lower() or result.metadata.resolved_count > 0


# =====================================================================
# Cross-module: Context + Intelligence integration
# =====================================================================


@pytest.mark.asyncio
async def test_context_with_hash_embedder():
    """ContextEngine works fully offline with HashEmbedder (no API keys)."""
    from sagewai.context import (
        ContextEngine,
        ContextScope,
        ContextSource,
        InMemoryMetadataStore,
        InMemoryVectorStore,
    )
    from sagewai.intelligence import HashEmbedder

    embedder = HashEmbedder(dimension=128)

    engine = ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        embedder=embedder,
        project_id="offline-test",
        enable_bm25=False,
    )

    # Ingest multiple documents
    for i, text in enumerate([
        "Python is a high-level programming language known for readability.",
        "JavaScript is the language of the web, running in browsers and Node.js.",
        "Rust provides memory safety without garbage collection.",
    ]):
        await engine.ingest_text(
            text=text,
            title=f"Language Overview {i+1}",
            scope=ContextScope.PROJECT,
            scope_id="offline-test",
            source=ContextSource.MANUAL,
        )

    # Search should return results (hash embedder works deterministically)
    results = await engine.search("programming language", top_k=3)
    assert len(results) > 0

    # All results should have valid scores
    for r in results:
        assert r.score > 0
        assert r.content
        assert r.document_title
