# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for InferencePreset and InferenceParams."""

import pytest

from sagewai.models.agent import AgentConfig
from sagewai.models.inference import InferenceParams, InferencePreset


class TestInferencePreset:
    def test_preset_values(self):
        assert InferencePreset.PRECISE == "precise"
        assert InferencePreset.BALANCED == "balanced"
        assert InferencePreset.CREATIVE == "creative"
        assert InferencePreset.EXPERIMENTAL == "experimental"

    def test_from_string(self):
        assert InferencePreset("precise") == InferencePreset.PRECISE


class TestInferenceParams:
    def test_defaults(self):
        params = InferenceParams()
        assert params.temperature == 0.7
        assert params.top_p is None
        assert params.top_k is None
        assert params.max_tokens is None
        assert params.frequency_penalty is None
        assert params.presence_penalty is None
        assert params.stop_sequences is None

    def test_custom_params(self):
        params = InferenceParams(
            temperature=0.3,
            top_p=0.8,
            top_k=40,
            max_tokens=4096,
            frequency_penalty=0.5,
            presence_penalty=-0.2,
            stop_sequences=["END", "STOP"],
        )
        assert params.temperature == 0.3
        assert params.top_p == 0.8
        assert params.top_k == 40
        assert params.max_tokens == 4096
        assert params.frequency_penalty == 0.5
        assert params.presence_penalty == -0.2
        assert params.stop_sequences == ["END", "STOP"]

    def test_from_preset_precise(self):
        params = InferenceParams.from_preset(InferencePreset.PRECISE)
        assert params.temperature == 0.1
        assert params.top_p == 0.9

    def test_from_preset_balanced(self):
        params = InferenceParams.from_preset(InferencePreset.BALANCED)
        assert params.temperature == 0.5
        assert params.top_p == 0.95

    def test_from_preset_creative(self):
        params = InferenceParams.from_preset(InferencePreset.CREATIVE)
        assert params.temperature == 0.9
        assert params.top_p == 1.0

    def test_from_preset_experimental(self):
        params = InferenceParams.from_preset(InferencePreset.EXPERIMENTAL)
        assert params.temperature == 1.5
        assert params.top_p == 1.0

    def test_from_preset_with_overrides(self):
        params = InferenceParams.from_preset(InferencePreset.CREATIVE, max_tokens=2000)
        assert params.temperature == 0.9
        assert params.top_p == 1.0
        assert params.max_tokens == 2000

    def test_from_preset_override_preset_value(self):
        params = InferenceParams.from_preset(InferencePreset.PRECISE, temperature=0.2)
        assert params.temperature == 0.2
        assert params.top_p == 0.9


class TestInferenceValidation:
    def test_temperature_too_low(self):
        with pytest.raises(ValueError, match="temperature must be between"):
            InferenceParams(temperature=-0.1)

    def test_temperature_too_high(self):
        with pytest.raises(ValueError, match="temperature must be between"):
            InferenceParams(temperature=2.1)

    def test_temperature_boundary_zero(self):
        params = InferenceParams(temperature=0.0)
        assert params.temperature == 0.0

    def test_temperature_boundary_two(self):
        params = InferenceParams(temperature=2.0)
        assert params.temperature == 2.0

    def test_top_p_too_low(self):
        with pytest.raises(ValueError, match="top_p must be between"):
            InferenceParams(top_p=-0.1)

    def test_top_p_too_high(self):
        with pytest.raises(ValueError, match="top_p must be between"):
            InferenceParams(top_p=1.5)

    def test_top_p_boundary(self):
        params = InferenceParams(top_p=0.0)
        assert params.top_p == 0.0
        params = InferenceParams(top_p=1.0)
        assert params.top_p == 1.0

    def test_frequency_penalty_too_low(self):
        with pytest.raises(ValueError, match="frequency_penalty must be between"):
            InferenceParams(frequency_penalty=-2.1)

    def test_frequency_penalty_too_high(self):
        with pytest.raises(ValueError, match="frequency_penalty must be between"):
            InferenceParams(frequency_penalty=2.1)

    def test_presence_penalty_too_low(self):
        with pytest.raises(ValueError, match="presence_penalty must be between"):
            InferenceParams(presence_penalty=-2.1)

    def test_presence_penalty_too_high(self):
        with pytest.raises(ValueError, match="presence_penalty must be between"):
            InferenceParams(presence_penalty=2.1)


class TestAgentConfigBackwardCompatibility:
    def test_top_level_temperature(self):
        config = AgentConfig(name="test", temperature=0.5)
        assert config.inference.temperature == 0.5

    def test_top_level_max_tokens(self):
        config = AgentConfig(name="test", max_tokens=2000)
        assert config.inference.max_tokens == 2000

    def test_top_level_both(self):
        config = AgentConfig(name="test", temperature=0.3, max_tokens=1000)
        assert config.inference.temperature == 0.3
        assert config.inference.max_tokens == 1000

    def test_defaults_without_explicit(self):
        config = AgentConfig(name="test")
        assert config.inference.temperature == 0.7
        assert config.inference.max_tokens is None

    def test_inference_preset(self):
        config = AgentConfig(name="test", inference=InferencePreset.CREATIVE)
        assert config.inference.temperature == 0.9
        assert config.inference.top_p == 1.0

    def test_inference_preset_string(self):
        config = AgentConfig(name="test", inference="balanced")
        assert config.inference.temperature == 0.5
        assert config.inference.top_p == 0.95

    def test_inference_params_object(self):
        params = InferenceParams(temperature=0.4, top_p=0.85)
        config = AgentConfig(name="test", inference=params)
        assert config.inference.temperature == 0.4
        assert config.inference.top_p == 0.85

    def test_inference_dict(self):
        config = AgentConfig(name="test", inference={"temperature": 0.2, "top_k": 50})
        assert config.inference.temperature == 0.2
        assert config.inference.top_k == 50
