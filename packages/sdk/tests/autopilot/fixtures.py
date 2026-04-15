"""Synthetic test fixtures for the autopilot framework.

These are NOT production blueprints. Production blueprints live only on
the hosted Sagewai LLM service. The synthetic fixtures in this file
mirror the structural shapes of the three reference blueprints
(scheduled, event_driven, batch) so that the framework can be tested
against every mode without shipping real templates in the OSS repo.

Everything in this file is prefixed with ``SYNTHETIC_`` to make that
unmistakable.
"""

from __future__ import annotations

from sagewai.autopilot._types import AgentKind, Mode
from sagewai.autopilot.agent_graph import Agent, AgentGraph, Branch
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.models import (
    EvalRef,
    LearningLoopConfig,
    Metric,
    ProviderRequirement,
    TrainingHook,
)
from sagewai.autopilot.slots import SlotSpec


def make_synthetic_scheduled_blueprint() -> Blueprint:
    """Shape: scheduled · research loop · linear graph · rating-gated hook."""
    return Blueprint(
        id="SYNTHETIC_scheduled_research",
        version="0.0.1",
        title="SYNTHETIC scheduled research fixture",
        description="Test fixture — not a production blueprint.",
        category="research",
        mode=Mode.SCHEDULED,
        example_goals=(
            "SYNTHETIC: run daily research on N vendors",
            "SYNTHETIC: track what vendors shipped each day",
        ),
        required_slots={
            "vendors": SlotSpec(
                type_="list[str]",
                description="URL list",
                validator_name="url_list",
            ),
            "schedule": SlotSpec(
                type_="cron",
                description="when to run",
                validator_name="cron",
                required=False,
                default="0 9 * * 1-5",
            ),
        },
        optional_slots={},
        tools_required=("web_search", "web_fetch"),
        providers_required=(
            ProviderRequirement(role="summarizer", capability="reasoning", tier="medium"),
        ),
        agent_graph=AgentGraph(
            nodes=(
                Agent(id="scout", kind=AgentKind.LLM, prompt_ref="p/s.md"),
                Agent(id="summarizer", kind=AgentKind.LLM, prompt_ref="p/sm.md"),
            ),
            edges=(("scout", "summarizer"),),
            entry="scout",
        ),
        success_criteria=EvalRef(
            dataset_id="SYNTHETIC_scheduled_research_eval",
            metrics=(Metric(name="quality", op=">=", value=4.0),),
        ),
        training_data_hooks=(
            TrainingHook(
                event="summarizer.completed",
                dataset="SYNTHETIC_scheduled_research_ds",
                format="alpaca",
                quality_filter="user_rating >= 4",
            ),
        ),
    )


def make_synthetic_event_driven_blueprint() -> Blueprint:
    """Shape: event_driven · classify + route · override-gated hook · Layer 5 target."""
    return Blueprint(
        id="SYNTHETIC_event_triage",
        version="0.0.1",
        title="SYNTHETIC event-driven triage fixture",
        description="Test fixture — not a production blueprint.",
        category="support",
        mode=Mode.EVENT_DRIVEN,
        example_goals=(
            "SYNTHETIC: triage incoming support events",
            "SYNTHETIC: classify and route tickets",
        ),
        required_slots={
            "taxonomy": SlotSpec(
                type_="list[str]",
                description="category labels",
                required=False,
                default=["billing", "bug", "other"],
            ),
        },
        optional_slots={},
        tools_required=("ticket_create", "ticket_assign"),
        providers_required=(
            ProviderRequirement(
                role="classifier",
                capability="classification",
                tier="small",
                fine_tune_target=True,
            ),
        ),
        agent_graph=AgentGraph(
            nodes=(
                Agent(
                    id="classifier",
                    kind=AgentKind.LLM,
                    prompt_ref="p/cls.md",
                    output_schema_ref="ClassificationSchema",
                ),
                Agent(
                    id="router",
                    kind=AgentKind.DETERMINISTIC,
                    deterministic_fallback=True,
                ),
                Agent(id="review", kind=AgentKind.DETERMINISTIC),
            ),
            edges=(("classifier", "router"),),
            branches={
                "router": (
                    Branch(condition="confidence >= 0.7", target="review"),
                    Branch(condition="confidence <  0.7", target="classifier"),
                ),
            },
            entry="classifier",
        ),
        success_criteria=EvalRef(
            dataset_id="SYNTHETIC_event_triage_eval",
            metrics=(Metric(name="accuracy", op=">=", value=0.9),),
        ),
        training_data_hooks=(
            TrainingHook(
                event="router.completed",
                dataset="SYNTHETIC_event_triage_ds",
                format="classification",
                quality_filter="human_override is None",
            ),
        ),
        learning_loop_target=LearningLoopConfig(
            trigger_after_labeled_samples=500,
            base_model="llama-3.1-8b-instruct",
            eval_gate_dataset_id="SYNTHETIC_event_triage_eval",
            promotion_criteria="accuracy >= 0.92",
        ),
    )


def make_synthetic_batch_blueprint() -> Blueprint:
    """Shape: batch · deterministic validator in graph · dynamic extraction schema."""
    return Blueprint(
        id="SYNTHETIC_batch_extract",
        version="0.0.1",
        title="SYNTHETIC batch extraction fixture",
        description="Test fixture — not a production blueprint.",
        category="operations",
        mode=Mode.BATCH,
        example_goals=("SYNTHETIC: extract structured fields from documents",),
        required_slots={
            "extraction_schema": SlotSpec(
                type_="JsonSchema",
                description="fields to extract",
                validator_name="json_schema",
            ),
        },
        optional_slots={
            "confidence_threshold": SlotSpec(
                type_="float",
                description="below this routes to review",
                required=False,
                default=0.85,
            ),
        },
        tools_required=("pdf_parse", "ocr", "table_write"),
        providers_required=(
            ProviderRequirement(
                role="extractor",
                capability="structured_output",
                tier="medium",
                fine_tune_target=True,
            ),
        ),
        agent_graph=AgentGraph(
            nodes=(
                Agent(id="ingestor", kind=AgentKind.LLM, prompt_ref="p/ing.md"),
                Agent(id="extractor", kind=AgentKind.LLM, prompt_ref="p/ext.md"),
                Agent(id="validator", kind=AgentKind.DETERMINISTIC),
                Agent(id="router", kind=AgentKind.DETERMINISTIC),
                Agent(id="out_table", kind=AgentKind.DETERMINISTIC),
                Agent(id="out_review", kind=AgentKind.DETERMINISTIC),
            ),
            edges=(
                ("ingestor", "extractor"),
                ("extractor", "validator"),
                ("validator", "router"),
            ),
            branches={
                "router": (
                    Branch(
                        condition="confidence >= threshold",
                        target="out_table",
                    ),
                    Branch(
                        condition="confidence <  threshold",
                        target="out_review",
                    ),
                ),
            },
            entry="ingestor",
        ),
        success_criteria=EvalRef(
            dataset_id="SYNTHETIC_batch_extract_eval",
            metrics=(Metric(name="f1", op=">=", value=0.9),),
        ),
        training_data_hooks=(
            TrainingHook(
                event="reviewer.completed",
                dataset="SYNTHETIC_batch_extract_ds",
                format="alpaca",
                quality_filter="reviewer_accepted is True",
            ),
        ),
    )
