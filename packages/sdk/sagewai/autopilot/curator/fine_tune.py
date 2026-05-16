# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fine-tuning executor for the autopilot curator pipeline.

:class:`FineTuneExecutor` runs a LoRA fine-tuning job, dispatching to one
of two API-compatible backends:

* **unsloth** — for CUDA / NVIDIA GPUs. The standard production path.
* **mlx_tune** — for Apple Silicon via Apple's MLX framework. Drop-in
  Unsloth-compatible API maintained at https://github.com/ARahim3/mlx-tune.

When neither backend is installed (the common case on a CPU / CI runner),
the executor returns a ``"skipped"`` result rather than raising.

The default backend is ``"auto"``: try Unsloth first, then mlx-tune.
Override via :attr:`FineTuneConfig.backend` when you want a specific path.

Typical usage::

    executor = FineTuneExecutor(config=FineTuneConfig(output_dir="/tmp/models"))
    result = executor.execute(job, dataset)
    if result.status == "completed":
        print(f"Model saved to {result.model_path}")
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .types import FineTuneJob, TrainingDataset

logger = logging.getLogger(__name__)

Backend = Literal["auto", "unsloth", "mlx_tune"]


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
        backend: Which fine-tuning backend to use. ``"auto"`` (default)
            tries ``unsloth`` first, then ``mlx_tune``. Set to a specific
            value to force a path or to fail fast in CI.
        produce_gguf: When ``True``, attempt to export the fused model to
            GGUF immediately after training so it can be loaded by Ollama
            / llama.cpp / LM Studio without an extra conversion step.
            Currently honoured on the ``mlx_tune`` backend (uses
            ``save_pretrained_gguf(..., dequantize=True)``); the Unsloth
            branch only logs a warning since Unsloth users typically run
            ``llama.cpp/convert_hf_to_gguf.py`` themselves. The resulting
            path lands in ``FineTuneResult.metrics['gguf_path']``.
    """

    model_config = ConfigDict(frozen=True)

    output_dir: str = Field(default="/tmp/sagewai-finetune")
    lora_r: int = Field(default=16, gt=0)
    lora_alpha: int = Field(default=32, gt=0)
    epochs: int = Field(default=1, gt=0)
    batch_size: int = Field(default=4, gt=0)
    learning_rate: float = Field(default=2e-4, gt=0)
    backend: Backend = "auto"
    produce_gguf: bool = False


class FineTuneResult(BaseModel):
    """Frozen outcome from :meth:`FineTuneExecutor.execute`.

    Attributes:
        status: One of ``"completed"``, ``"skipped"``, or ``"failed"``.
        reason: Human-readable explanation for ``"skipped"`` or ``"failed"``
            statuses.  ``None`` when ``status == "completed"``.
        model_path: Absolute path to the saved model directory.
            ``None`` unless ``status == "completed"``.
        metrics: Training metrics (loss, steps, etc.) reported by the
            chosen backend (Unsloth / mlx-tune). Empty dict when not
            completed. ``backend`` key always present on success.
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
    """Executes fine-tuning jobs via Unsloth (CUDA) or mlx-tune (Apple Silicon).

    The executor is designed to be injected into a :class:`Curator` instance.
    It handles three execution paths:

    * **No backend installed** → ``FineTuneResult(status="skipped")``
    * **Backend installed, job succeeds** →
      ``FineTuneResult(status="completed", model_path=..., metrics={...})``
    * **Backend installed, job fails** →
      ``FineTuneResult(status="failed", reason=str(error))``

    The backend is selected via :attr:`FineTuneConfig.backend`. The default
    ``"auto"`` tries Unsloth first (the production path on CUDA boxes),
    then mlx-tune (the Apple Silicon path), then returns ``"skipped"``.

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
        backend, fast_lm = self._select_backend()
        if backend is None:
            logger.info(
                "No fine-tune backend available — job %s skipped", job.job_id,
            )
            return FineTuneResult(
                status="skipped",
                reason="unsloth not installed",
            )

        try:
            if backend == "unsloth":
                return self._run_unsloth(job, dataset, fast_lm)
            return self._run_with_backend(backend, job, dataset, fast_lm)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fine-tune job %s failed: %s", job.job_id, exc)
            return FineTuneResult(
                status="failed",
                reason=str(exc),
            )

    # ── Internal ───────────────────────────────────────────────────

    def _select_backend(self) -> tuple[Backend | None, Any]:
        """Resolve which backend to use and import its FastLanguageModel.

        Returns ``(backend_name, FastLanguageModel)`` on success, or
        ``(None, None)`` when no backend is available. Honours
        ``FineTuneConfig.backend`` strictly: an explicit ``"unsloth"`` or
        ``"mlx_tune"`` choice that is not installed returns ``(None, None)``
        rather than silently switching to the other backend.
        """
        choice = self.config.backend

        if choice in ("auto", "unsloth"):
            try:
                import unsloth  # type: ignore[import]

                return ("unsloth", unsloth.FastLanguageModel)
            except ImportError:
                if choice == "unsloth":
                    return (None, None)

        if choice in ("auto", "mlx_tune"):
            try:
                import mlx_tune  # type: ignore[import]

                return ("mlx_tune", mlx_tune.FastLanguageModel)
            except ImportError:
                pass

        return (None, None)

    def _run_unsloth(
        self,
        job: FineTuneJob,
        dataset: TrainingDataset,
        fast_lm: Any,
    ) -> FineTuneResult:
        """Execute the Unsloth (CUDA) fine-tuning pipeline.

        Kept as a named method so existing tests that monkeypatch
        ``_run_unsloth`` continue to work.
        """
        return self._run_with_backend("unsloth", job, dataset, fast_lm)

    def _run_with_backend(
        self,
        backend: Backend,
        job: FineTuneJob,
        dataset: TrainingDataset,
        fast_lm: Any,
    ) -> FineTuneResult:
        """Execute the LoRA pipeline against a chosen backend.

        Both backends share the FastLanguageModel + LoRA + SFT training
        surface; the differences live in which TrainingArguments / SFTTrainer
        we import and which kwargs they accept.
        """
        cfg = self.config
        output_path = Path(cfg.output_dir) / job.job_id
        output_path.mkdir(parents=True, exist_ok=True)

        # Snapshot the dataset to JSONL alongside the saved adapter for
        # provenance + manual re-run convenience. Training itself reads
        # from the in-memory list (avoids the HF datasets / dill pickle
        # path, which is brittle on newer Python versions).
        snapshot_path = output_path / "dataset.jsonl"
        sample_count = _export_dataset_to_jsonl(dataset, snapshot_path)
        logger.info(
            "Exported %d samples to %s for job %s (backend=%s)",
            sample_count, snapshot_path, job.job_id, backend,
        )

        model, tokenizer = fast_lm.from_pretrained(
            model_name=job.base_model,
            max_seq_length=2048,
            dtype=None,
            load_in_4bit=True,
        )

        model = fast_lm.get_peft_model(
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

        # Build a plain list of formatted Alpaca rows. We avoid going through
        # ``datasets.load_dataset`` / ``Dataset.from_list``: those touch HF's
        # ``dill``-based fingerprint pickler, which is broken on Python 3.14+
        # (CPython 3.14 changed ``Pickler._batch_setitems`` and ``dill``
        # 0.4.x has not caught up). For ``unsloth`` we wrap into a HF Dataset
        # because ``trl.SFTTrainer`` requires it; for ``mlx_tune`` the rows
        # go in directly — its trainer iterates and indexes, no Arrow needed.
        formatted_rows: list[dict[str, str]] = []
        for sample in dataset.samples:
            instruction = str(sample.get("instruction", ""))
            input_text = str(sample.get("input", ""))
            output_text = str(sample.get("output", ""))
            prompt = f"### Instruction:\n{instruction}\n"
            if input_text:
                prompt += f"### Input:\n{input_text}\n"
            prompt += f"### Response:\n{output_text}"
            formatted_rows.append({"text": prompt})

        if backend == "unsloth":
            from datasets import Dataset  # type: ignore[import]
            from transformers import TrainingArguments  # type: ignore[import]
            from trl import SFTTrainer  # type: ignore[import]

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
                train_dataset=Dataset.from_list(formatted_rows),
                dataset_text_field="text",
                max_seq_length=2048,
                args=training_args,
            )
        else:  # mlx_tune
            from mlx_tune import SFTTrainer, TrainingArguments  # type: ignore[import]

            training_args = TrainingArguments(
                output_dir=str(output_path),
                num_train_epochs=cfg.epochs,
                per_device_train_batch_size=cfg.batch_size,
                learning_rate=cfg.learning_rate,
                logging_steps=1,
            )
            trainer = SFTTrainer(
                model=model,
                tokenizer=tokenizer,
                train_dataset=formatted_rows,
                dataset_text_field="text",
                max_seq_length=2048,
                args=training_args,
            )

        train_output = trainer.train()
        metrics: dict[str, Any] = dict(getattr(train_output, "metrics", {}) or {})
        metrics["sample_count"] = sample_count
        metrics["backend"] = backend

        model.save_pretrained(str(output_path))
        tokenizer.save_pretrained(str(output_path))

        if cfg.produce_gguf:
            gguf_path = output_path / "model.gguf"
            try:
                if backend == "mlx_tune":
                    self._mlx_export_gguf_via_bridge(
                        model=model,
                        tokenizer=tokenizer,
                        output_path=output_path,
                        gguf_path=gguf_path,
                    )
                    metrics["gguf_path"] = str(gguf_path)
                    logger.info("GGUF export written to %s", gguf_path)
                else:
                    logger.warning(
                        "produce_gguf=True requested on backend=%s — not yet "
                        "wired. Run llama.cpp/convert_hf_to_gguf.py against "
                        "the fused HF model manually.",
                        backend,
                    )
                    metrics["gguf_path"] = None
            except Exception as exc:  # noqa: BLE001
                # Non-fatal: training succeeded; deploy is the user's call.
                logger.warning(
                    "GGUF export for job %s failed: %s — adapter still saved at %s",
                    job.job_id, exc, output_path,
                )
                metrics["gguf_path"] = None
                metrics["gguf_export_error"] = str(exc)

        logger.info(
            "Fine-tune job %s completed via %s, model at %s",
            job.job_id, backend, output_path,
        )
        return FineTuneResult(
            status="completed",
            model_path=str(output_path),
            metrics=metrics,
        )

    def _mlx_export_gguf_via_bridge(
        self,
        *,
        model: Any,
        tokenizer: Any,
        output_path: Path,
        gguf_path: Path,
    ) -> None:
        """Double-hop GGUF export: save Safetensors, then bridge via llama.cpp.

        Why this is the safe path:

        1. ``mx.save_gguf`` in mlx-lm 0.31.x rejects non-row-major arrays
           produced by ``permute_weights`` for ``q_proj``/``k_proj``
           (``ValueError: [save_gguf] can only serialize row-major arrays``).
        2. Even with a contiguity patch applied, mlx-lm's GGUF writer
           omits tokenizer metadata keys (e.g. ``tokenizer.ggml.tokens``)
           that Ollama requires; the runner crashes on first
           ``/api/generate`` with
           ``libc++abi: terminating due to uncaught exception of type
           std::out_of_range``.

        The double-hop sidesteps both upstream issues by going through a
        Safetensors checkpoint and letting ``llama.cpp/convert_hf_to_gguf.py``
        do the GGUF packing — its tokenizer-metadata writer is complete
        and its tensor handling is layout-agnostic.

        Steps:

        1. ``model.save_pretrained_merged(merged_dir, tokenizer)`` — fuses
           LoRA into the base in-process and writes a row-major HF
           Safetensors checkpoint. mlx-tune's wrapper handles this.
        2. Drop in-memory references and clear MLX's Metal cache. Without
           this the parent process holds ~6 GB of fp16 tensors that the
           subsequent llama.cpp subprocess has no way to free, starving
           any later in-process inference (e.g. the held-out eval in
           Example 38).
        3. Subprocess to ``LLAMA_CPP_DIR/convert_hf_to_gguf.py`` with
           ``--outtype $SAGEWAI_FT_GGUF_QUANT`` (default ``q8_0``).

        For ``q4_k_m``-class quantisation, run ``llama-quantize`` from a
        built llama.cpp on the produced GGUF — out of scope for this
        in-process helper because building llama.cpp is a separate dance.

        Args:
            model: In-memory mlx-tune model wrapper (still has LoRA
                attached at call time).
            tokenizer: The tokenizer ``model.save_pretrained_merged``
                serialises alongside the fused weights.
            output_path: Per-job working dir, e.g.
                ``/tmp/sagewai-ft/<job-id>``.
            gguf_path: Where the produced GGUF should land.

        Raises:
            RuntimeError: When llama.cpp is not available, or its
                ``convert_hf_to_gguf.py`` exits non-zero. Caller maps
                this to ``FineTuneResult.metrics["gguf_export_error"]``.
        """
        convert_script = self._locate_llama_cpp_convert_script()
        if convert_script is None:
            raise RuntimeError(
                "llama.cpp not found. Set LLAMA_CPP_DIR to a checkout of "
                "github.com/ggml-org/llama.cpp (the repo's "
                "convert_hf_to_gguf.py is what we shell out to), or put "
                "convert_hf_to_gguf.py on PATH. The double-hop bridge needs "
                "it because mlx-lm 0.31.x's GGUF writer drops tokenizer "
                "metadata Ollama requires.",
            )

        import gc

        import mlx.core as mx

        # ── 1. Save the fused HF checkpoint (in-process, no subprocess) ──
        merged_dir = output_path / "merged"
        logger.info(
            "Saving fused HF checkpoint to %s for double-hop GGUF export",
            merged_dir,
        )
        model.save_pretrained_merged(
            str(merged_dir), tokenizer, save_method="merged_16bit",
        )

        # ── 2. Memory cleanup before the shell call ──
        # The fused HF model is now on disk; the in-memory copy is no
        # longer needed for this export. Drop it, GC, and clear Metal
        # cache so the subprocess + any later in-process inference both
        # have headroom.
        gc.collect()
        try:
            # mlx renamed mx.metal.clear_cache → mx.clear_cache; prefer the
            # new name when present, fall back to the old one for older
            # mlx releases.
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
        except Exception:  # noqa: BLE001
            pass

        # ── 3. Subprocess to llama.cpp/convert_hf_to_gguf.py ──
        # Default to q8_0 — the convert script doesn't ship k-quants.
        # For q4_k_m, the user runs llama-quantize on the produced GGUF
        # (build llama.cpp first; out of scope here).
        quant = os.environ.get("SAGEWAI_FT_GGUF_QUANT", "q8_0")
        cmd = [
            sys.executable,
            str(convert_script),
            str(merged_dir),
            "--outfile", str(gguf_path),
            "--outtype", quant,
        ]
        logger.info("Running llama.cpp bridge: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-800:]
            raise RuntimeError(
                f"convert_hf_to_gguf.py exited {proc.returncode}: {tail}",
            )

    @staticmethod
    def _locate_llama_cpp_convert_script() -> Path | None:
        """Find ``convert_hf_to_gguf.py`` via ``LLAMA_CPP_DIR`` or ``$PATH``.

        Returns ``None`` when neither is set / found.
        """
        env_dir = os.environ.get("LLAMA_CPP_DIR")
        if env_dir:
            candidate = Path(env_dir) / "convert_hf_to_gguf.py"
            if candidate.exists():
                return candidate
        which = shutil.which("convert_hf_to_gguf.py")
        if which:
            return Path(which)
        return None
