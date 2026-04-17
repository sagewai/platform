# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Unsloth fine-tuning executor for the autopilot curator pipeline.

:class:`FineTuneExecutor` runs a fine-tuning job using Unsloth when it is
available. When Unsloth is not installed (the common case in CPU / CI
environments), the executor returns a ``"skipped"`` result rather than
raising an error.

Typical usage::

    executor = FineTuneExecutor(config=FineTuneConfig(output_dir="/tmp/models"))
    result = executor.execute(job, dataset)
    if result.status == "completed":
        print(f"Model saved to {result.model_path}")
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .types import FineTuneJob, TrainingDataset

logger = logging.getLogger(__name__)


class FineTuneConfig(BaseModel):
    """Immutable configuration for :class:`FineTuneExecutor`.

    Attributes:
        output_dir: Directory where fine-tuned models are saved.
            Sub-directories are created per job using ``job_id``.
        lora_r: LoRA rank. Higher values = more parameters.
        lora_alpha: LoRA scaling factor. Typically 2× ``lora_r``.
        epochs: Number of training epochs.
        batch_size: Per-device training batch size.
        learning_rate: AdamW learning rate.
    """

    model_config = ConfigDict(frozen=True)

    output_dir: str = Field(default="/tmp/sagewai-finetune")
    lora_r: int = Field(default=16, gt=0)
    lora_alpha: int = Field(default=32, gt=0)
    epochs: int = Field(default=1, gt=0)
    batch_size: int = Field(default=4, gt=0)
    learning_rate: float = Field(default=2e-4, gt=0)


class FineTuneResult(BaseModel):
    """Frozen outcome from :meth:`FineTuneExecutor.execute`.

    Attributes:
        status: One of ``"completed"``, ``"skipped"``, or ``"failed"``.
        reason: Human-readable explanation for ``"skipped"`` or ``"failed"``
            statuses.  ``None`` when ``status == "completed"``.
        model_path: Absolute path to the saved model directory.
            ``None`` unless ``status == "completed"``.
        metrics: Training metrics (loss, steps, etc.) reported by Unsloth.
            Empty dict when not completed.
    """

    model_config = ConfigDict(frozen=True)

    status: str
    reason: str | None = None
    model_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


def _export_dataset_to_jsonl(dataset: TrainingDataset, path: Path) -> int:
    """Write *dataset* samples to *path* in JSONL (one JSON object per line).

    Only samples in ``"alpaca"`` format are written; other formats are
    written as-is (raw JSON object per line).

    Returns:
        Number of samples written.
    """
    written = 0
    with path.open("w", encoding="utf-8") as fh:
        for sample in dataset.samples:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
            written += 1
    return written


class FineTuneExecutor:
    """Executes fine-tuning jobs via Unsloth.

    The executor is designed to be injected into a :class:`Curator` instance.
    It handles three execution paths:

    * **Unsloth not installed** → returns ``FineTuneResult(status="skipped")``
    * **Unsloth installed, job succeeds** → returns
      ``FineTuneResult(status="completed", model_path=..., metrics={...})``
    * **Unsloth installed, job fails** → returns
      ``FineTuneResult(status="failed", reason=str(error))``

    Args:
        config: Fine-tuning hyperparameters and output directory.
            Defaults to :class:`FineTuneConfig` with stock values.
    """

    def __init__(self, config: FineTuneConfig | None = None) -> None:
        self.config: FineTuneConfig = config or FineTuneConfig()

    def execute(self, job: FineTuneJob, dataset: TrainingDataset) -> FineTuneResult:
        """Run a fine-tuning job.

        Args:
            job: The :class:`FineTuneJob` specification (base model, project,
                method, etc.).
            dataset: The :class:`TrainingDataset` whose samples are used for
                training.

        Returns:
            A :class:`FineTuneResult` describing the outcome.
        """
        # Graceful degradation: check for Unsloth without importing at module
        # level so that import of this module never fails in CPU environments.
        try:
            import unsloth  # noqa: F401 — existence check only
            from unsloth import FastLanguageModel  # type: ignore[import]
        except ImportError:
            logger.info(
                "unsloth is not installed — fine-tune job %s skipped", job.job_id
            )
            return FineTuneResult(
                status="skipped",
                reason="unsloth not installed",
            )

        try:
            return self._run_unsloth(job, dataset, FastLanguageModel)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fine-tune job %s failed: %s", job.job_id, exc)
            return FineTuneResult(
                status="failed",
                reason=str(exc),
            )

    # ── Internal ───────────────────────────────────────────────────

    def _run_unsloth(
        self,
        job: FineTuneJob,
        dataset: TrainingDataset,
        FastLanguageModel: Any,
    ) -> FineTuneResult:
        """Execute the Unsloth fine-tuning pipeline.

        This method is called only when Unsloth is confirmed to be importable.
        It is a separate method so that tests can monkeypatch it in isolation.
        """
        cfg = self.config
        output_path = Path(cfg.output_dir) / job.job_id
        output_path.mkdir(parents=True, exist_ok=True)

        # Export dataset to a temporary JSONL file.
        with tempfile.NamedTemporaryFile(
            suffix=".jsonl", mode="w", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)

        sample_count = _export_dataset_to_jsonl(dataset, tmp_path)
        logger.info(
            "Exported %d samples to %s for job %s",
            sample_count,
            tmp_path,
            job.job_id,
        )

        # Load base model + tokeniser via Unsloth.
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=job.base_model,
            max_seq_length=2048,
            dtype=None,
            load_in_4bit=True,
        )

        # Apply LoRA adapters.
        model = FastLanguageModel.get_peft_model(
            model,
            r=cfg.lora_r,
            lora_alpha=cfg.lora_alpha,
            target_modules=[
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
            ],
            lora_dropout=0,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=42,
        )

        # Build HuggingFace dataset from the JSONL we exported.
        from datasets import load_dataset  # type: ignore[import]
        from trl import SFTTrainer  # type: ignore[import]
        from transformers import TrainingArguments  # type: ignore[import]

        hf_dataset = load_dataset("json", data_files=str(tmp_path), split="train")

        def _format_alpaca(examples: dict[str, Any]) -> dict[str, Any]:
            instructions = examples.get("instruction", [""] * len(examples["input"]))
            inputs = examples.get("input", [""] * len(instructions))
            outputs = examples.get("output", [""] * len(instructions))
            texts = []
            for inst, inp, out in zip(instructions, inputs, outputs):
                prompt = f"### Instruction:\n{inst}\n"
                if inp:
                    prompt += f"### Input:\n{inp}\n"
                prompt += f"### Response:\n{out}"
                texts.append(prompt)
            return {"text": texts}

        hf_dataset = hf_dataset.map(_format_alpaca, batched=True)

        training_args = TrainingArguments(
            output_dir=str(output_path),
            num_train_epochs=cfg.epochs,
            per_device_train_batch_size=cfg.batch_size,
            learning_rate=cfg.learning_rate,
            logging_steps=1,
            save_strategy="no",
            report_to="none",
        )

        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=hf_dataset,
            dataset_text_field="text",
            max_seq_length=2048,
            args=training_args,
        )

        train_output = trainer.train()
        metrics: dict[str, Any] = dict(train_output.metrics)
        metrics["sample_count"] = sample_count

        # Save LoRA-adapted model.
        model.save_pretrained(str(output_path))
        tokenizer.save_pretrained(str(output_path))

        # Clean up temp file.
        try:
            tmp_path.unlink()
        except OSError:
            pass

        logger.info("Fine-tune job %s completed, model at %s", job.job_id, output_path)
        return FineTuneResult(
            status="completed",
            model_path=str(output_path),
            metrics=metrics,
        )
