from __future__ import annotations

from pathlib import Path
import math
import platform

from .common import digest, read_json, read_jsonl, write_json, write_text
from .dataset import validate
from .models import REVISION, UPSTREAM, load_nemo, package_versions

DEFAULTS = {"checkpoint": UPSTREAM, "revision": REVISION, "batch_size": 1,
            "accumulate_grad_batches": 8, "max_epochs": 30, "learning_rate": 0.00001,
            "precision": "auto", "accelerator": "auto", "num_workers": 0,
            "patience": 5, "seed": 42, "freeze_encoder": False, "gradient_clip_val": 1.0}


def training_config(path: Path | None) -> dict:
    supplied = read_json(path) if path else {}
    if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
        raise ValueError("Configuração de treino contém opções desconhecidas")
    result = {**DEFAULTS, **supplied}
    for key in ("batch_size", "accumulate_grad_batches", "max_epochs", "patience"):
        if not isinstance(result[key], int) or result[key] < 1:
            raise ValueError(f"{key} deve ser inteiro positivo")
    if not math.isfinite(result["learning_rate"]) or result["learning_rate"] <= 0 or result["num_workers"] < 0:
        raise ValueError("learning_rate/num_workers inválidos")
    if not math.isfinite(result["gradient_clip_val"]) or result["gradient_clip_val"] < 0:
        raise ValueError("gradient_clip_val deve ser finito e não negativo")
    return result


def train(dataset: Path, output: Path, config_path: Path | None = None, resume: Path | None = None,
          ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    config = training_config(config_path)
    report = validate(dataset, ffmpeg=ffmpeg, ffprobe=ffprobe)
    if not report["ready"]:
        raise ValueError("Dataset reprovado. Consulte reports/dataset_validation.json.")
    stats = read_json(dataset / "reports" / "statistics.json")
    if stats["sample_rate"] != 16000:
        raise ValueError("Este checkpoint e runtime exigem 16 kHz")
    for split in ("train", "validation"):
        if not read_jsonl(dataset / "manifests" / f"{split}.jsonl"):
            raise ValueError(f"Conjunto {split} vazio")
    if resume and (not resume.is_file() or resume.suffix != ".ckpt"):
        raise ValueError("--resume exige checkpoint Lightning .ckpt")
    output = output.resolve()
    if output.exists() and any(output.iterdir()) and not resume:
        raise ValueError("Diretório de treino já contém dados. Use outro --output ou --resume.")
    import torch
    import lightning.pytorch as pl
    from lightning.pytorch.callbacks import EarlyStopping, ModelCheckpoint
    from lightning.pytorch.loggers import CSVLogger
    from omegaconf import OmegaConf

    pl.seed_everything(config["seed"], workers=True)
    accelerator = config["accelerator"]
    if accelerator == "auto":
        accelerator = "gpu" if torch.cuda.is_available() else "cpu"
    if accelerator not in ("gpu", "cpu"):
        raise ValueError("accelerator deve ser auto, gpu ou cpu")
    if accelerator == "gpu" and not torch.cuda.is_available():
        raise ValueError("GPU CUDA solicitada, mas indisponível")
    precision = config["precision"]
    if precision == "auto":
        precision = ("bf16-mixed" if torch.cuda.is_bf16_supported() else "16-mixed") if accelerator == "gpu" else "32-true"
    if accelerator == "cpu" and precision != "32-true":
        raise ValueError("Use precision=32-true em CPU")
    model, original_path = load_nemo(config["checkpoint"], config["revision"])
    if not 0 < model.ctc_loss_weight <= 1:
        raise ValueError("Checkpoint não possui perda CTC ativa")
    # Preserve the native hybrid training objective and original tokenizer.
    if config["freeze_encoder"]:
        for parameter in model.encoder.parameters():
            parameter.requires_grad = False
    monitor = "val_wer_ctc"
    callback = ModelCheckpoint(dirpath=output / "checkpoints", monitor=monitor, mode="min",
                               save_top_k=2, save_last=True, filename="epoch{epoch:02d}-ctc{val_wer_ctc:.4f}",
                               auto_insert_metric_name=False)
    trainer = pl.Trainer(accelerator=accelerator, devices=1, precision=precision,
                         max_epochs=config["max_epochs"], accumulate_grad_batches=config["accumulate_grad_batches"],
                         gradient_clip_val=config["gradient_clip_val"], deterministic=True,
                         num_sanity_val_steps=1, log_every_n_steps=1,
                         callbacks=[callback, EarlyStopping(monitor=monitor, mode="min", patience=config["patience"], strict=True)],
                         logger=CSVLogger(str(output), name="metrics"), default_root_dir=str(output))
    model.set_trainer(trainer)
    for split in ("train", "validation"):
        data_config = OmegaConf.create({"manifest_filepath": str((dataset / "manifests" / f"{split}.jsonl").resolve()),
                                       "sample_rate": 16000, "batch_size": config["batch_size"], "shuffle": split == "train",
                                       "num_workers": config["num_workers"], "pin_memory": accelerator == "gpu",
                                       "min_duration": stats["min_duration"], "max_duration": stats["max_duration"],
                                       "is_tarred": False, "use_start_end_token": False, "drop_last": False})
        if split == "train":
            model.setup_training_data(data_config)
        else:
            model.setup_validation_data(data_config)
    model.setup_optimization(OmegaConf.create({"name": "adamw", "lr": config["learning_rate"],
                                             "betas": [0.9, 0.98], "weight_decay": 0.001}))
    # Dither is useful in training; validation/export must be deterministic.
    # NeMo handles eval dither. Runtime export has a separate compatibility check.
    run_info = {"configuration": {**config, "resolved_accelerator": accelerator, "resolved_precision": precision},
                "initial_checkpoint": str(original_path), "initial_checkpoint_sha256": digest(original_path),
                "manifests": report["manifest_sha256"], "versions": package_versions(), "platform": platform.platform(),
                "model_class": type(model).__name__, "tokenizer_vocab_size": model.tokenizer.vocab_size,
                "monitor": monitor, "ctc_loss_weight": model.ctc_loss_weight}
    if resume:
        previous = read_json(output / "run.json") if (output / "run.json").exists() else None
        if previous is None or previous["manifests"] != run_info["manifests"] or previous["configuration"] != run_info["configuration"]:
            raise ValueError("Retomada exige run.json, mesmos manifests e mesma configuração")
        run_info["resume_checkpoint_sha256"] = digest(resume)
    write_json(output / "run.json", run_info)
    write_text(output / "model_config.yaml", OmegaConf.to_yaml(model.cfg))
    trainer.fit(model, ckpt_path=str(resume.resolve()) if resume else None)
    if not callback.best_model_path:
        raise ValueError("Treino não produziu checkpoint validado")
    best = torch.load(callback.best_model_path, map_location="cpu", weights_only=False)
    model.load_state_dict(best["state_dict"])
    model.to("cpu").eval()
    model.save_to(str(output / "best.nemo"))
    run_info.update(best_checkpoint=callback.best_model_path, best_nemo_sha256=digest(output / "best.nemo"),
                    best_validation_wer_ctc=float(callback.best_model_score), status="completed")
    write_json(output / "run.json", run_info)
    return run_info
