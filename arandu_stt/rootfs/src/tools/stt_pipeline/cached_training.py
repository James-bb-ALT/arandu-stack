"""Fine-tune the original NeMo CTC head on CUDA with a frozen CPU encoder.

The encoder representation is cached without gradients or augmentation. This
fits a busy 10 GB RTX without placing the entire ASR model in VRAM. The result
is saved into the original hybrid .nemo with its original tokenizer/encoder.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import logging
from pathlib import Path
import random
import shutil
import time

from .common import digest, read_json, read_jsonl, write_json, write_jsonl
from .dataset import validate
from .evaluation import score_predictions
from .models import load_nemo, package_versions

LOG = logging.getLogger(__name__)


def training_order(rows: list[dict]) -> list[dict]:
    """Apply explicit train-only repetition without duplicating dataset records."""
    result = []
    for row in rows:
        repeats = row.get("training_repeats", 1)
        if type(repeats) is not int or not 1 <= repeats <= 10:
            raise ValueError("training_repeats deve ser inteiro entre 1 e 10")
        result.extend([row] * repeats)
    return result


def relocate_dataset(source: Path, destination: Path) -> None:
    """Make a separate container-native dataset; never rewrite Windows manifests."""
    if destination.exists():
        return
    shutil.copytree(source, destination)
    for path in [destination / "metadata.jsonl", *list((destination / "manifests").glob("*.jsonl"))]:
        rows = read_jsonl(path)
        for row in rows:
            # Source manifests use Windows separators even when read in Linux.
            original = row["audio_filepath"].replace("\\", "/")
            relative = original.split("/dataset/audio/", 1)[1]
            row["audio_filepath"] = str((destination / "audio" / relative).resolve())
        write_jsonl(path, rows)
    write_json(destination / "reports" / "release.json", {"ready": False, "reason": "validation_pending"})


def train_cached(dataset: Path, output: Path, checkpoint: Path, *, epochs=80, batch_size=8,
                 learning_rate=0.0003, patience=12, seed=42, resume=False, cpu_threads=4,
                 save_final_epoch=False) -> dict:
    import numpy as np
    import soundfile as sf
    import torch

    if not torch.cuda.is_available():
        raise ValueError("Este treinamento exige CUDA: não faz fallback silencioso para CPU.")
    if epochs < 1 or batch_size < 1 or patience < 1 or learning_rate <= 0:
        raise ValueError("Configuração de treino inválida")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    progress_path = output / "progress.json"
    started = time.time()

    def progress(phase, **values):
        write_json(progress_path, {"phase": phase, "started_unix": started, "updated_unix": time.time(),
                                   "max_epochs": epochs, **values})

    progress("validating", message="Validando os dados antes do treino")
    release = validate(dataset)
    if not release["ready"]:
        raise ValueError("Dataset reprovado")
    train_rows = read_jsonl(dataset / "manifests" / "train.jsonl")
    validation_rows = read_jsonl(dataset / "manifests" / "validation.jsonl")
    if not train_rows or not validation_rows:
        raise ValueError("Train/validation devem estar preenchidos")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(cpu_threads)
    torch.use_deterministic_algorithms(True, warn_only=True)
    progress("loading_checkpoint", message="Restaurando FastConformer e tokenizador originais")
    model, path = load_nemo(str(checkpoint), device="cpu")
    model.eval()
    model.preprocessor.featurizer.dither = 0.0
    model.preprocessor.featurizer.pad_to = 0
    # All modules are frozen except the existing CTC decoder. No new layers.
    for parameter in model.parameters():
        parameter.requires_grad = False
    for parameter in model.ctc_decoder.parameters():
        parameter.requires_grad = True
    initial_hash = digest(path)
    signature = {"checkpoint_sha256": initial_hash, "manifests": release["manifest_sha256"],
                 "seed": seed, "learning_rate": learning_rate, "batch_size": batch_size,
                 "mode": "frozen_encoder_cached_ctc", "cache_version": 2, "versions": package_versions()}
    cache_path = output / "encoder_cache.pt"
    signature_path = output / "cache_signature.json"
    if cache_path.exists() and signature_path.exists() and read_json(signature_path) == signature:
        features = torch.load(cache_path, map_location="cpu", weights_only=False)
    else:
        features = {}
        rows = train_rows + validation_rows
        for i, record in enumerate(rows, 1):
            progress("caching_encoder", completed=i - 1, total=len(rows), message="Calculando representações do encoder congelado em CPU")
            waveform, sample_rate = sf.read(record["audio_filepath"], dtype="float32")
            if sample_rate != 16000 or waveform.ndim != 1:
                raise ValueError("Cache exige WAV mono 16 kHz")
            signal = torch.from_numpy(waveform).unsqueeze(0)
            length = torch.tensor([len(waveform)], dtype=torch.long)
            with torch.inference_mode():
                processed, processed_len = model.preprocessor(input_signal=signal, length=length)
                encoded, encoded_len = model.encoder(audio_signal=processed, length=processed_len)
            target = torch.tensor(model.tokenizer.text_to_ids(record["text"]), dtype=torch.long)
            n = int(encoded_len[0])
            minimum_frames = len(target) + int((target[1:] == target[:-1]).sum())
            # Clone outside inference mode: the head needs gradients for its weights.
            features[record["id"]] = {"encoded": encoded[0, :, :n].clone(), "target": target, "length": n,
                                      "minimum_ctc_frames": minimum_frames,
                                      "ctc_trainable": bool(len(target)) and minimum_frames <= n}
            LOG.info("Encoder cache %s/%s", i, len(rows))
        torch.save(features, cache_path)
        write_json(signature_path, signature)
    original_train_count = len(train_rows)
    rejected_ctc = [{"id": row["id"], "speaker": row["speaker"], "duration": row["duration"],
                     "encoded_frames": features[row["id"]]["length"],
                     "minimum_ctc_frames": features[row["id"]]["minimum_ctc_frames"],
                     "code": "ctc_alignment_infeasible"} for row in train_rows if not features[row["id"]]["ctc_trainable"]]
    train_rows = [row for row in train_rows if features[row["id"]]["ctc_trainable"]]
    write_json(output / "ctc_eligibility.json", {"original_training_examples": original_train_count,
                                               "eligible_training_examples": len(train_rows), "excluded": rejected_ctc,
                                               "validation_unchanged": True, "test_unchanged": True})
    if not train_rows or {r["speaker"] for r in train_rows} != {r["speaker"] for r in rejected_ctc + train_rows}:
        raise ValueError("Filtragem CTC deixou um participante sem exemplos de treino")
    decoder = model.ctc_decoder.to("cuda")
    initial_head = {k: value.detach().cpu().clone() for k, value in decoder.state_dict().items()}
    optimizer = torch.optim.AdamW(decoder.parameters(), lr=learning_rate, weight_decay=0.001)
    blank = len(model.ctc_decoder.vocabulary)
    loss_function = torch.nn.CTCLoss(blank=blank, reduction="mean", zero_infinity=False)
    precision = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    use_scaler = precision == torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=use_scaler)
    torch.cuda.reset_peak_memory_stats()

    def batch(records):
        items = [features[r["id"]] for r in records]
        width = max(item["length"] for item in items)
        channels = items[0]["encoded"].shape[0]
        padded = torch.zeros(len(items), channels, width, dtype=torch.float32)
        for i, item in enumerate(items):
            padded[i, :, :item["length"]] = item["encoded"]
        return (padded.to("cuda"), torch.cat([item["target"] for item in items]).to("cuda"),
                torch.tensor([item["length"] for item in items], dtype=torch.long),
                torch.tensor([len(item["target"]) for item in items], dtype=torch.long))

    def validation():
        decoder.eval()
        predictions = {}
        with torch.no_grad():
            for start in range(0, len(validation_rows), batch_size):
                records = validation_rows[start:start + batch_size]
                x, _, lengths, _ = batch(records)
                # Full precision validation aligns with the final CPU NeMo artifact.
                log_probs = decoder(encoder_output=x)
                hypotheses = model.ctc_decoding.ctc_decoder_predictions_tensor(
                    log_probs.cpu(), decoder_lengths=lengths, return_hypotheses=False)
                if isinstance(hypotheses, tuple):
                    hypotheses = hypotheses[0]
                for row, hypothesis in zip(records, hypotheses):
                    predictions[row["id"]] = hypothesis if isinstance(hypothesis, str) else hypothesis.text
        return score_predictions(validation_rows, predictions)

    initial_validation = validation()
    best_wer = initial_validation["overall"]["wer"]
    best_cer = initial_validation["overall"]["cer"]
    best_state = deepcopy(initial_head)
    best_epoch, stale, start_epoch = 0, 0, 1
    history = [{"epoch": 0, "train_loss": None, "validation_wer": best_wer, "validation_cer": best_cer}]
    last_path = output / "last.pt"
    if resume:
        previous = torch.load(last_path, map_location="cpu", weights_only=False)
        if previous["signature"] != signature:
            raise ValueError("Retomada exige checkpoint/dados/configuração idênticos")
        decoder.load_state_dict(previous["head"])
        optimizer.load_state_dict(previous["optimizer"])
        scaler.load_state_dict(previous["scaler"])
        best_state, best_wer, best_cer = previous["best_head"], previous["best_wer"], previous["best_cer"]
        best_epoch, stale, start_epoch = previous["best_epoch"], previous["stale"], previous["epoch"] + 1
        history = previous["history"]
        random.setstate(previous["python_rng"])
        torch.set_rng_state(previous["torch_rng"])
        torch.cuda.set_rng_state_all(previous["cuda_rng"])
    config = {**signature, "max_epochs": epochs, "patience": patience,
              "precision": str(precision), "encoder_device": "cpu", "optimizer_device": "cuda",
              "gpu": torch.cuda.get_device_name(), "trainable_parameters": sum(p.numel() for p in decoder.parameters()),
              "total_parameters": sum(p.numel() for p in model.parameters()),
              "training_examples": len(train_rows), "ctc_excluded_examples": len(rejected_ctc),
              "effective_training_examples_per_epoch": len(training_order(train_rows)),
              "sampling": "explicit_train_repetition" if any(r.get("training_repeats", 1) != 1 for r in train_rows) else "uniform",
              "initial_validation": initial_validation["overall"]}
    write_json(output / "run.json", config)
    for epoch in range(start_epoch, epochs + 1):
        order = training_order(train_rows)
        random.shuffle(order)
        losses = []
        decoder.train()
        for start in range(0, len(order), batch_size):
            records = order[start:start + batch_size]
            x, targets, lengths, target_lengths = batch(records)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", dtype=precision):
                log_probs = decoder(encoder_output=x)
            loss = loss_function(log_probs.float().transpose(0, 1).contiguous(), targets, lengths, target_lengths)
            if not torch.isfinite(loss):
                raise ValueError("Perda CTC não finita; treino interrompido")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(decoder.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            losses.append(float(loss.detach()))
            progress("training", epoch=epoch, batch=min(start + batch_size, len(order)), total=len(order),
                     best_epoch=best_epoch, best_validation_wer=best_wer, history=history,
                     message="Otimizando o decoder CTC na RTX; encoder preservado")
        metrics = validation()
        wer, cer = metrics["overall"]["wer"], metrics["overall"]["cer"]
        improved = (wer, cer) < (best_wer, best_cer)
        if improved:
            best_wer, best_cer, best_epoch, stale = wer, cer, epoch, 0
            best_state = {k: value.detach().cpu().clone() for k, value in decoder.state_dict().items()}
            write_json(output / "best_validation_details.json", metrics)
        else:
            stale += 1
        history.append({"epoch": epoch, "train_loss": sum(losses) / len(losses), "validation_wer": wer, "validation_cer": cer})
        torch.save({"signature": signature, "epoch": epoch, "head": decoder.state_dict(), "optimizer": optimizer.state_dict(),
                    "scaler": scaler.state_dict(), "best_head": best_state, "best_wer": best_wer, "best_cer": best_cer,
                    "best_epoch": best_epoch, "stale": stale, "history": history, "python_rng": random.getstate(),
                    "torch_rng": torch.get_rng_state(), "cuda_rng": torch.cuda.get_rng_state_all()}, last_path)
        write_json(output / "history.json", history)
        LOG.info("Epoch %s | loss %.4f | validation WER %.4f | best %.4f @ %s", epoch, history[-1]["train_loss"], wer, best_wer, best_epoch)
        if stale >= patience:
            break
    progress("saving_checkpoint", epoch=history[-1]["epoch"], best_epoch=best_epoch, history=history,
             message="Salvando melhor checkpoint pela validação; teste ainda não utilizado")
    if save_final_epoch:
        decoder.to("cpu")
        model.eval()
        model.save_to(str(output / "final_epoch.nemo"))
    decoder.load_state_dict(best_state)
    decoder.to("cpu")
    model.eval()
    model.save_to(str(output / "best.nemo"))
    result = {**config, "status": "completed", "epochs_completed": history[-1]["epoch"], "best_epoch": best_epoch,
              "best_validation_wer_ctc": best_wer, "best_validation_cer": best_cer,
              "checkpoint_sha256": digest(output / "best.nemo"), "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
              "elapsed_seconds": time.time() - started, "head_changed": any(not torch.equal(initial_head[k], v) for k, v in best_state.items())}
    if save_final_epoch:
        result["final_epoch_checkpoint_sha256"] = digest(output / "final_epoch.nemo")
    write_json(output / "run.json", result)
    progress("trained", history=history, best_epoch=best_epoch, best_validation_wer=best_wer,
             message="Treino concluído. Avaliação independente pendente.")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--learning-rate", type=float, default=.0003)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(train_cached(args.dataset, args.output, args.checkpoint, epochs=args.epochs, learning_rate=args.learning_rate,
                       batch_size=args.batch_size, patience=args.patience, resume=args.resume))
