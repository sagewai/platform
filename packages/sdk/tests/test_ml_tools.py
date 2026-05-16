# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.tools.ml — ML model tool wrappers."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from sagewai.tools.ml import onnx_tool, sklearn_tool, tabular_tool


class MockClassifier:
    """Minimal mock sklearn-compatible classifier."""

    def predict_proba(self, features):
        return [[0.2, 0.8]]  # 80% positive probability

    def predict(self, features):
        return [1]


class MockRegressor:
    """Mock model with only predict (no predict_proba)."""

    def predict(self, features):
        return [42.5]


pytest_plugins = ("pytest_asyncio",)


def test_sklearn_tool_spec_structure():
    tool = sklearn_tool(
        name="predict_churn",
        description="Predict customer churn probability",
        model=MockClassifier(),
        feature_names=["tenure", "usage", "plan"],
    )
    assert tool.name == "predict_churn"
    assert tool.description == "Predict customer churn probability"
    assert "tenure" in tool.parameters["properties"]
    assert "usage" in tool.parameters["properties"]
    assert "plan" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["tenure", "usage", "plan"]


@pytest.mark.asyncio
async def test_sklearn_tool_predict_proba():
    pytest.importorskip("pandas")
    tool = sklearn_tool(
        name="detect_fraud",
        description="Fraud score",
        model=MockClassifier(),
        feature_names=["amount", "merchant"],
        output_label="fraud_probability",
    )
    result = await tool.handler(amount=100.0, merchant=3.0)
    data = json.loads(result)
    assert "fraud_probability" in data
    assert abs(data["fraud_probability"] - 0.8) < 0.001


@pytest.mark.asyncio
async def test_sklearn_tool_predict_only():
    pytest.importorskip("pandas")
    tool = sklearn_tool(
        name="forecast",
        description="Demand forecast",
        model=MockRegressor(),
        feature_names=["day", "season"],
        output_label="demand",
    )
    result = await tool.handler(day=1.0, season=2.0)
    data = json.loads(result)
    assert "demand" in data
    assert abs(data["demand"] - 42.5) < 0.001


@pytest.mark.asyncio
async def test_sklearn_tool_missing_features_defaults_to_zero():
    pytest.importorskip("pandas")
    tool = sklearn_tool(
        name="score",
        description="Score",
        model=MockClassifier(),
        feature_names=["a", "b", "c"],
    )
    # Only pass one feature — others should default to 0.0 without error
    result = await tool.handler(a=5.0)
    data = json.loads(result)
    assert "prediction" in data


def test_tabular_tool_spec_structure():
    def my_fn(**kwargs):
        return {"result": sum(kwargs.values())}

    tool = tabular_tool(
        name="sum_features",
        description="Sum all features",
        predict_fn=my_fn,
        feature_names=["x", "y"],
    )
    assert tool.name == "sum_features"
    assert "x" in tool.parameters["properties"]
    assert "y" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["x", "y"]


@pytest.mark.asyncio
async def test_tabular_tool_calls_fn():
    def my_fn(x, y):
        return {"sum": x + y}

    tool = tabular_tool("add", "Add x and y", my_fn, ["x", "y"])
    result = await tool.handler(x=3.0, y=4.0)
    data = json.loads(result)
    assert data["sum"] == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_tabular_tool_fn_returns_string():
    def my_fn(x):
        return "already a string"

    tool = tabular_tool("str_fn", "Returns string", my_fn, ["x"])
    result = await tool.handler(x=1.0)
    assert result == "already a string"


def test_onnx_tool_spec_structure():
    mock_session = MagicMock()
    tool = onnx_tool(
        name="onnx_predict",
        description="ONNX model prediction",
        session=mock_session,
        input_names=["feature_0", "feature_1"],
        output_name="score",
    )
    assert tool.name == "onnx_predict"
    assert "feature_0" in tool.parameters["properties"]
    assert "feature_1" in tool.parameters["properties"]


@pytest.mark.asyncio
async def test_onnx_tool_calls_session():
    numpy = pytest.importorskip("numpy")

    mock_session = MagicMock()
    mock_session.run.return_value = [numpy.array([[0.95]])]

    tool = onnx_tool(
        name="onnx_score",
        description="ONNX score",
        session=mock_session,
        input_names=["inp"],
        output_name="probability",
    )
    result = await tool.handler(inp=1.0)
    data = json.loads(result)
    assert abs(data["probability"] - 0.95) < 0.001
    assert mock_session.run.called
