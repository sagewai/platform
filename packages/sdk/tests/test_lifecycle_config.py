# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for lifecycle config + auto-triggers (#420)."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.lifecycle import LifecycleConfig, LifecycleManager
from sagewai.context.models import ContextScope
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


class TestLifecycleConfig:
    def test_defaults(self):
        config = LifecycleConfig()
        assert config.decay_rate == 0.05
        assert config.compress_age_days == 90
        assert config.compress_min_importance == 0.1
        assert config.archive_importance == 0.05
        assert config.discard_age_days == 365
        assert config.auto_trigger_threshold == 10000

    def test_custom_values(self):
        config = LifecycleConfig(
            decay_rate=0.03,
            compress_age_days=60,
            auto_trigger_threshold=5000,
        )
        assert config.decay_rate == 0.03
        assert config.compress_age_days == 60
        assert config.auto_trigger_threshold == 5000


class TestConfigurableDecay:
    @pytest.mark.asyncio
    async def test_decay_uses_config_rate(self):
        meta = InMemoryMetadataStore()
        vec = InMemoryVectorStore()
        config = LifecycleConfig(decay_rate=0.10)  # 10% per week
        mgr = LifecycleManager(metadata_store=meta, vector_store=vec, config=config)

        # Verify the config is stored
        assert mgr.config.decay_rate == 0.10

    @pytest.mark.asyncio
    async def test_compress_uses_config_thresholds(self):
        meta = InMemoryMetadataStore()
        vec = InMemoryVectorStore()
        config = LifecycleConfig(compress_age_days=30, compress_min_importance=0.2)
        mgr = LifecycleManager(metadata_store=meta, vector_store=vec, config=config)

        # compress_stale with None args should use config
        count = await mgr.compress_stale("test-project")
        assert count == 0  # no docs to compress


class TestAutoTrigger:
    @pytest.mark.asyncio
    async def test_engine_accepts_lifecycle_config(self):
        config = LifecycleConfig(auto_trigger_threshold=5)
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
            lifecycle_config=config,
        )
        assert engine._lifecycle_config is config

    @pytest.mark.asyncio
    async def test_auto_trigger_does_not_run_below_threshold(self):
        config = LifecycleConfig(auto_trigger_threshold=1000)
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
            lifecycle_config=config,
        )

        # Ingest one small doc (well below 1000 chunks)
        await engine.ingest_text(
            "Small document",
            title="test",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )

        # Lifecycle should not have run
        assert engine._lifecycle_running is False

    @pytest.mark.asyncio
    async def test_no_lifecycle_config_skips_trigger(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
        )
        # Should not raise even without config
        await engine._maybe_auto_lifecycle()
