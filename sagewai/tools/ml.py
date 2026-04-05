# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ML model tool wrappers for Sagewai agents.

Wraps classical ML models (sklearn, XGBoost, CatBoost, ONNX) as agent-callable
``ToolSpec`` objects. The pattern: LLMs reason and orchestrate; ML models predict.

Usage::

    from sagewai.tools.ml import sklearn_tool, tabular_tool
    from sagewai.engines.universal import UniversalAgent
    import xgboost as xgb

    model = xgb.XGBClassifier()
    model.load_model("fraud_model.json")

    fraud_tool = sklearn_tool(
        name="detect_fraud",
        description="Score transaction fraud risk (0.0 = safe, 1.0 = fraud)",
        model=model,
        feature_names=["amount", "merchant_cat", "country_code", "hour_of_day"],
        output_label="fraud_probability",
        explain=True,   # include SHAP top-3 feature impacts if shap is installed
    )

    agent = UniversalAgent("pay-agent", model="gpt-4o", tools=[fraud_tool])
    result = await agent.chat("Is transaction #123 fraudulent? amount=5000, merchant_cat=7, ...")
"""

from __future__ import annotations

import json
from typing import Any

from sagewai.models.tool import ToolSpec


def sklearn_tool(
    name: str,
    description: str,
    model: Any,
    feature_names: list[str],
    output_label: str = "prediction",
    explain: bool = False,
) -> ToolSpec:
    """Wrap a sklearn/XGBoost/CatBoost model as an agent-callable tool.

    The tool accepts feature values as keyword arguments and returns a JSON
    string with the prediction. If the model has ``predict_proba``, the
    positive-class probability is returned. Otherwise ``predict`` is used.

    Args:
        name: Tool name in snake_case (shown to the LLM for tool selection).
        description: What the tool predicts (shown to the LLM).
        model: Trained model with ``.predict()`` or ``.predict_proba()``.
        feature_names: Input feature names — each becomes a required JSON schema property.
        output_label: Key name for the primary prediction in the output dict.
        explain: If ``True`` and ``shap`` is installed, include top-3 feature
            SHAP impacts in the output.

    Returns:
        A :class:`~sagewai.models.tool.ToolSpec` the agent can call.
    """
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            feat: {"type": "number", "description": f"Feature value for '{feat}'"}
            for feat in feature_names
        },
        "required": feature_names,
    }

    async def handler(**kwargs: Any) -> str:
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "pandas is required for sklearn_tool. Install with: pip install pandas"
            ) from exc

        row = {feat: float(kwargs.get(feat, 0.0)) for feat in feature_names}
        df = pd.DataFrame([row])

        result: dict[str, Any] = {}

        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(df)[0]
            if len(proba) == 2:
                result[output_label] = float(proba[1])
            else:
                result[output_label] = [float(p) for p in proba]
        else:
            pred = model.predict(df)[0]
            try:
                result[output_label] = float(pred)
            except (TypeError, ValueError):
                result[output_label] = str(pred)

        if explain:
            try:
                import shap

                explainer = shap.TreeExplainer(model)
                shap_values = explainer.shap_values(df)
                if isinstance(shap_values, list):
                    shap_values = shap_values[1]
                impacts = sorted(
                    zip(feature_names, shap_values[0]),
                    key=lambda x: abs(x[1]),
                    reverse=True,
                )[:3]
                result["top_factors"] = [
                    {"feature": f, "impact": round(float(v), 4)} for f, v in impacts
                ]
            except ImportError:
                pass  # shap not installed — skip explanation silently

        return json.dumps(result)

    return ToolSpec(name=name, description=description, parameters=parameters, handler=handler)


def tabular_tool(
    name: str,
    description: str,
    predict_fn: Any,
    feature_names: list[str],
) -> ToolSpec:
    """Wrap any callable ``(feature_name=value, ...) -> dict`` as an agent tool.

    Useful for wrapping custom prediction functions, ensemble models, or any
    callable that doesn't follow the sklearn API.

    Args:
        name: Tool name in snake_case.
        description: What the tool does (shown to the LLM).
        predict_fn: Callable that accepts feature values as keyword arguments
            and returns a dict (or any JSON-serializable value).
        feature_names: Input feature names — each becomes a required JSON schema property.

    Returns:
        A :class:`~sagewai.models.tool.ToolSpec` the agent can call.
    """
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            feat: {"type": "number", "description": f"Value for '{feat}'"}
            for feat in feature_names
        },
        "required": feature_names,
    }

    async def handler(**kwargs: Any) -> str:
        inputs = {feat: float(kwargs.get(feat, 0.0)) for feat in feature_names}
        result = predict_fn(**inputs)
        if isinstance(result, str):
            return result
        return json.dumps(result)

    return ToolSpec(name=name, description=description, parameters=parameters, handler=handler)


def onnx_tool(
    name: str,
    description: str,
    session: Any,
    input_names: list[str],
    output_name: str = "output",
) -> ToolSpec:
    """Wrap an ONNX Runtime ``InferenceSession`` as an agent-callable tool.

    Args:
        name: Tool name in snake_case.
        description: What the model predicts (shown to the LLM).
        session: An ``onnxruntime.InferenceSession`` instance.
        input_names: ONNX model input tensor names.
        output_name: Key for the scalar output value in the result dict.

    Returns:
        A :class:`~sagewai.models.tool.ToolSpec` the agent can call.
    """
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            inp: {"type": "number", "description": f"Input tensor value for '{inp}'"}
            for inp in input_names
        },
        "required": input_names,
    }

    async def handler(**kwargs: Any) -> str:
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError(
                "numpy is required for onnx_tool. Install with: pip install numpy"
            ) from exc

        feeds = {
            inp: np.array([[float(kwargs.get(inp, 0.0))]], dtype=np.float32)
            for inp in input_names
        }
        outputs = session.run(None, feeds)
        result = {output_name: float(outputs[0].flatten()[0])}
        return json.dumps(result)

    return ToolSpec(name=name, description=description, parameters=parameters, handler=handler)
