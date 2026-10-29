from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random
import sqlite3
import threading
import uuid

import numpy as np
import soundfile as sf

from .audio_validation import acoustic_features, canonical_clip, inspect_audio, write_wav_atomic
from .common import ROOT, StopRequested, WorkerLock, atomic_json, check_stop, load_config, now, stable_seed, wait_interruptibly
from .store import Store
from .texts import contains_wake, normalized, spec, wake_count
from .voice_bank import assess_diversity, profiles

LOG = logging.getLogger("wakegen")


class Heartbeat:
    def __init__(self, run_id, preflight):
        self.path = ROOT / "state" / ("preflight-progress.json" if preflight else "progress.json")
        self.data = {"pid": os.getpid(), "run_id": run_id, "started_at": now(), "stage": "starting"}
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.run, name="heartbeat", daemon=True)
        self.thread.start()

    def update(self, **values):
        with self.lock:
            self.data.update(values)
            self.data["heartbeat_at"] = now()
            atomic_json(self.path, self.data)

    def run(self):
        while not self.finished.wait(10):
            self.update()

    def close(self):
        self.finished.set()
        self.thread.join(timeout=12)


class VoiceBankBlocked(RuntimeError):
    pass


class VoiceReferenceExhausted(VoiceBankBlocked):
    pass


class Generator:
    def __init__(self, config, status, run_id):
        from .engine import Engine
        self.config = config
        self.status = status
        self.run_id = run_id
        self.store = Store()
        self.store.freeze(config)
        self.store.recover()
        self.engine = Engine(config, status)
        revisions = {key: {"repo": value["repo"], "revision": value["revision"]} for key, value in self.engine.registry.items()}
        previous = self.store.setting("model_revisions")
        if previous is not None and previous != revisions:
            raise ValueError("Revisões dos modelos mudaram; não misturar gerações sem nova versão do dataset")
        self.store.set_setting("model_revisions", revisions)
        self.profiles = list(profiles(config))
        self.new_samples = []
        self.counts = self.store.counts()
        self.status.update(counts=self.counts, targets=config["targets"], accepted_voice_profiles=len(self.store.voices()))

    def ensure_voice(self, index, reference_only=False, allow_design=True):
        profile = self.profiles[index]
        ident = profile["speaker_id"]
        existing = self.store.voice(ident)
        if existing:
            path = ROOT / existing["reference_path"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != existing["reference_sha256"]:
                raise VoiceBankBlocked(f"Referência existente ausente/adulterada: {ident}; não será substituída")
            return existing
        folder = ROOT / "data/voices" / ident
        folder.mkdir(parents=True, exist_ok=True)
        committed = folder / "profile.json"
        if committed.exists():
            recovered = json.loads(committed.read_text(encoding="utf-8"))
            path = ROOT / recovered["reference_path"]
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == recovered["reference_sha256"]:
                self.store.add_voice(recovered)
                return recovered
            raise VoiceBankBlocked(f"Referência interrompida inconsistente: {ident}")
        for attempt in range(self.config["voice_bank"]["max_reference_attempts"]):
            check_stop()
            seed = stable_seed(profile["reference_seed"], attempt)
            reference = folder / f"reference_attempt_{attempt:02d}.wav"
            stage = folder / f"reference_attempt_{attempt:02d}.json"
            if reference.exists() and not stage.exists():
                # Preserva órfão; nunca sobrescreve um WAV potencialmente válido.
                continue
            if stage.exists():
                candidate = json.loads(stage.read_text(encoding="utf-8"))
                if candidate.get("rejected"):
                    continue
                if hashlib.sha256(reference.read_bytes()).hexdigest() != candidate["reference_sha256"]:
                    raise VoiceBankBlocked(f"Referência de tentativa adulterada: {reference}")
                audio, rate = sf.read(reference, dtype="float32")
            else:
                if not allow_design:
                    continue
                candidate = dict(profile)
                candidate["reference_seed"] = seed
                audio, rate = self.engine.design(candidate, seed)
                quality = inspect_audio(audio, rate, self.config, kind="reference")
                checksum = write_wav_atomic(reference, audio, rate)
                candidate.update(reference_path=reference.relative_to(ROOT).as_posix(), reference_sha256=checksum,
                                 reference_quality=quality, native_rate=rate, acoustic_features=acoustic_features(audio, rate),
                                 reference_generation_parameters=self.engine.kwargs(reference=True),
                                 reference_execution=self.engine.execution_details(),
                                 reference_model=self.engine.registry["design"], reference_text=self.config["voice_bank"]["reference_text"])
                reference_asr = self.engine.recognize(audio, rate)
                score = SequenceMatcher(None, normalized(self.config["voice_bank"]["reference_text"]), normalized(reference_asr.get("text") or "")).ratio()
                candidate.update(reference_asr=reference_asr, reference_transcript_similarity=score)
                if not quality["valid"]:
                    candidate.update(rejected=True, rejection_reason="reference_acoustic_quality")
                elif reference_asr["enabled"] and score < 0.35:
                    candidate.update(rejected=True, rejection_reason="reference_transcript_mismatch")
                atomic_json(stage, candidate)
                if candidate.get("rejected"):
                    continue
            if reference_only:
                return candidate
            embedding = candidate.get("speaker_embedding")
            if embedding is None:
                embedding = self.engine.prepare_voice(candidate)
            others = self.store.voices()
            # Jamais ajustar uma voz train em função de validação/teste.
            if candidate["split"] == "train":
                others = [v for v in others if v["split"] == "train"]
            candidate["speaker_embedding"] = embedding
            diversity = assess_diversity(candidate, others, self.config["voice_bank"]["max_speaker_cosine_similarity"])
            nearest = diversity["similarity"]
            candidate.update(diversity_validation=diversity, similarity=nearest,
                             embedding_method="Qwen3-TTS Base speaker encoder; screening, not identity certification")
            similarity = nearest["cosine_similarity"]
            if diversity["rejection_reason"]:
                candidate.update(rejected=True, rejection_reason=diversity["rejection_reason"])
                atomic_json(stage, candidate)
                continue
            candidate.update(identity_status="synthetic_profile_embedding_screened_not_human_verified", created_at=now())
            atomic_json(committed, candidate)
            self.store.add_voice(candidate)
            self.status.update(accepted_voice_profiles=len(self.store.voices()))
            LOG.info("Perfil %s aceito; similaridade mais próxima %s", ident, similarity)
            return candidate
        raise VoiceReferenceExhausted(f"Não foi possível obter referência distinta válida para {ident}; tentativas preservadas")

    def make_sample(self, job, voice, attempt):
        seed = stable_seed(job["seed"], "attempt", attempt)
        rng = random.Random(seed)
        positive = job["category"] == "positive"
        text = job["text"]
        core_text = "Arandu," if job["kind"] == "context" else text
        audio, rate = self.engine.clone(core_text, voice, seed)
        quality = inspect_audio(audio, rate, self.config, "word" if positive else "general")
        if not quality["valid"]:
            raise ValueError("acoustic:" + ",".join(quality["reasons"]))
        asr = self.engine.recognize(audio, rate)
        recognized = asr.get("text") or ""
        if not positive and contains_wake(recognized):
            raise ValueError("asr_negative_contains_wake_word_review_rejected")
        if positive and wake_count(recognized) > 1:
            raise ValueError("asr_abnormal_wake_repetition")
        native = audio
        context = None
        if job["kind"] == "context":
            command, command_rate = self.engine.clone(job["command"], voice, stable_seed(seed, "command"))
            if command_rate != rate or not inspect_audio(command, command_rate, self.config, "general")["valid"]:
                raise ValueError("context_command_invalid")
            pause = rng.uniform(0.12, 0.28)
            native = np.concatenate([audio, np.zeros(int(rate * pause), dtype=np.float32), command])
            context = {"construction": "two_syntheses_not_continuous_forced_alignment", "wake_native_interval_seconds": [0, len(audio) / rate],
                       "command_start_seconds": len(audio) / rate + pause, "text": text,
                       "canonical_clip_contains_only_wake_word": True}
        canonical, processing = canonical_clip(audio, rate, self.config, rng)
        if self.store.duplicate(processing["content_hash_before_padding_and_speed"]):
            raise ValueError("duplicate_audio_before_padding_and_speed")
        canonical_quality = inspect_audio(canonical, 16000, self.config, "word" if positive else "general")
        if not canonical_quality["valid"]:
            raise ValueError("canonical_acoustic:" + ",".join(canonical_quality["reasons"]))
        split = voice["split"]
        ident = job["sample_id"]
        relative = Path("data") / job["category"] / split / f"{ident}.wav"
        native_path = Path("data/native") / job["category"] / split / f"{ident}.wav"
        # Ambos são novos; crash antes do sidecar preserva órfãos e avança sem sobrescrever.
        native_sha = write_wav_atomic(ROOT / native_path, native, rate)
        checksum = write_wav_atomic(ROOT / relative, canonical, 16000)
        supported = contains_wake(recognized) if positive else False
        diversity = voice.get("diversity_validation", {})
        review = positive or job["category"] == "negative_hard" or bool(quality["warnings"]) or not recognized or diversity.get("requires_review", False)
        validation = {"status": "review" if review else "acoustic_and_text_checks_passed",
                      "acoustic": canonical_quality, "native_acoustic": quality, "asr": asr,
                      "pronunciation": "asr_supported_not_phonetically_verified" if supported else "unconfirmed",
                      "pronunciation_confidence": "medium" if supported else "unconfirmed",
                      "synthetic_not_human_verified": True,
                      "diversity": diversity,
                      "training_export_requires_explicit_review_opt_in": review}
        record = {"sample_id": ident, "category": job["category"], "speaker_id": voice["speaker_id"],
                  "locale": voice["locale"], "accent_validation": voice["accent_validation"],
                  "speaker_profile_path": f"data/voices/{voice['speaker_id']}/profile.json", "split": split,
                  "canonical_transcript": "Arandu" if positive else text, "intended_text": text, "tts_text": core_text,
                  "voice_description": voice["description"], "accent": voice["accent"], "region": voice["region"],
                  "intonation": job["intonation"], "kind": job["kind"], "seed": seed, "job_seed": job["seed"], "attempt": attempt,
                  "model": self.engine.registry["base"], "generation_parameters": self.engine.kwargs(),
                  "execution": self.engine.execution_details(),
                  "path": relative.as_posix(), "native_path": native_path.as_posix(), "wav_sha256": checksum,
                  "native_sha256": native_sha, "sample_rate": 16000, "native_rate": rate,
                  "duration_seconds": len(canonical) / 16000, "processing": processing, "validation": validation,
                  "context": context, "phonetic_note": job.get("phonetic_note"),
                  "wake_word_interval_seconds": processing["speech_region_seconds"] if positive else None,
                  "boundary_method": "known_isolated_synthesis_plus_energy_margins" if positive else None,
                  "created_at": now(), "run_id": self.run_id, "index": job["index"]}
        self.store.commit_sample(record)
        self.counts[job["category"]] += 1
        self.new_samples.append({"sample_id": ident, "category": job["category"], "path": relative.as_posix(), "wav_sha256": checksum})
        # Apenas os primeiros poucos registros são necessários para readiness; não guarda corpus na RAM.
        if len(self.new_samples) > 8:
            self.new_samples = self.new_samples[:8]
        self.status.update(counts=dict(self.counts), last_sample=ident, last_success_at=now(), stage="sample_committed")
        LOG.info("WAV real %s: %s, %.2fs, QC=%s", ident, job["category"], len(canonical) / 16000, validation["status"])
        return record

    def ready(self, preflight):
        has_positive = any(s["category"] == "positive" for s in self.new_samples)
        has_negative = any(s["category"] != "positive" for s in self.new_samples)
        if has_positive and has_negative:
            target = ROOT / "state" / ("preflight.json" if preflight else "ready.json")
            if target.exists() and not preflight:
                old = json.loads(target.read_text(encoding="utf-8"))
                if old.get("run_id") == self.run_id and old.get("pid") == os.getpid():
                    return True
            atomic_json(target, {"ready": True, "pid": os.getpid(), "run_id": self.run_id,
                "created_at": now(), "counts": dict(self.counts), "new_validated_samples": list(self.new_samples),
                "validated_new_count": len(self.new_samples), "accepted_voice_profiles": len(self.store.voices()),
                "pronunciation_status": "review_not_human_verified", "device": self.engine.device,
                "config_identity": self.store.setting("identity")})
            return True
        return False

    def prepare_voice_bank(self, unavailable):
        """Duas passagens: VoiceDesign em lote, depois validação somente com Base."""
        prepared = []
        total = len(self.profiles)
        self.status.update(phase="preparing_references", planned_voice_profiles=total,
                           prepared_reference_profiles=0)
        for index in range(total):
            check_stop()
            if index in unavailable:
                continue
            try:
                self.ensure_voice(index, reference_only=True)
                prepared.append(index)
                self.status.update(prepared_reference_profiles=len(prepared))
            except VoiceReferenceExhausted as error:
                unavailable.add(index)
                self.store.set_setting("unavailable_speaker_indices", sorted(unavailable))
                LOG.warning("Referência esgotada no preparo do banco: %s", error)
        self.status.update(phase="validating_voice_bank", validated_voice_profiles=0)
        validated = 0
        for index in prepared:
            check_stop()
            try:
                # Esta passagem nunca recarrega VoiceDesign para tentar outra referência.
                self.ensure_voice(index, allow_design=False)
                validated += 1
                self.status.update(validated_voice_profiles=validated)
            except VoiceReferenceExhausted as error:
                unavailable.add(index)
                self.store.set_setting("unavailable_speaker_indices", sorted(unavailable))
                LOG.warning("Perfil esgotado na validação do banco: %s", error)
        self.status.update(phase="generating_samples", unavailable_speaker_indices=sorted(unavailable),
                           accepted_voice_profiles=len(self.store.voices()))
        self.store.set_setting("voice_bank_preparation", {"prepared": len(prepared), "validated": validated,
                               "unavailable": sorted(unavailable), "completed_at": now()})

    def run(self, preflight=False):
        cursor = self.store.setting("cursor", 0)
        consecutive = 0
        unavailable = set(self.store.setting("unavailable_speaker_indices", []))
        if not preflight and self.config.get("resources", {}).get("prepare_voice_bank_first", False):
            self.prepare_voice_bank(unavailable)
        while any(self.counts[k] < limit for k, limit in self.config["targets"].items()):
            check_stop()
            self.engine.disk_guard()
            job = spec(self.config, cursor)
            if len(unavailable) >= len(self.profiles):
                raise VoiceBankBlocked("Todos os perfis esgotaram referências válidas; dados preservados")
            if job["speaker_index"] in unavailable:
                cursor += 1
                self.store.set_setting("cursor", cursor)
                continue
            if self.store.exists(job["sample_id"]) or self.counts[job["category"]] >= self.config["targets"][job["category"]]:
                cursor += 1
                self.store.set_setting("cursor", cursor)
                continue
            try:
                voice = self.ensure_voice(job["speaker_index"])
            except VoiceReferenceExhausted as error:
                unavailable.add(job["speaker_index"])
                self.store.set_setting("unavailable_speaker_indices", sorted(unavailable))
                self.store.fail(job["sample_id"], {"job": job, "failures": [str(error)],
                                "created_at": now(), "status": "voice_rejected_preserved"})
                self.status.update(unavailable_speaker_indices=sorted(unavailable))
                LOG.warning("Perfil rejeitado; seguindo para outro: %s", error)
                cursor += 1
                self.store.set_setting("cursor", cursor)
                continue
            success = False
            failures = []
            # Órfãos de crash são preservados; não os sobrescreve para tentar novamente.
            output = ROOT / "data" / job["category"] / voice["split"] / f"{job['sample_id']}.wav"
            native_output = ROOT / "data/native" / job["category"] / voice["split"] / f"{job['sample_id']}.wav"
            if output.exists() or native_output.exists():
                failures.append("orphan_wav_preserved_no_overwrite")
            else:
                for attempt in range(self.config["generation"]["max_attempts_per_sample"]):
                    try:
                        self.make_sample(job, voice, attempt)
                        success = True
                        consecutive = 0
                        break
                    except StopRequested:
                        raise
                    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
                        failures.append(f"{type(error).__name__}: {error}")
                        LOG.exception("Falha %s tentativa %s", job["sample_id"], attempt)
                        if isinstance(error, FileExistsError) or output.exists() or native_output.exists():
                            break
                        if "out of memory" in str(error).casefold():
                            self.engine.force_cpu = True
                            self.engine.release()
                            wait_interruptibly(self.config["resources"]["retry_wait_seconds"])
            if not success:
                self.store.fail(job["sample_id"], {"job": job, "failures": failures, "created_at": now(), "status": "rejected_preserved"})
                consecutive += 1
                if consecutive >= self.config["resources"]["max_consecutive_failures"]:
                    raise RuntimeError("Falhas consecutivas excederam o limite; bloqueio técnico registrado")
            cursor += 1
            self.store.set_setting("cursor", cursor)
            if self.ready(preflight) and preflight:
                self.status.update(stage="preflight_passed", counts=dict(self.counts))
                return
        self.status.update(stage="targets_reached", counts=dict(self.counts))


def configure_logging():
    (ROOT / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(ROOT / "logs/generation.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = parser.parse_args()
    configure_logging()
    status = None
    generator = None
    try:
        with WorkerLock():
            check_stop()
            status = Heartbeat(args.run_id or uuid.uuid4().hex, args.preflight)
            generator = Generator(load_config(args.config), status, status.data["run_id"])
            generator.run(args.preflight)
        return 0
    except StopRequested:
        if status:
            status.update(stage="stopped_by_request")
        return 0
    except Exception as error:
        LOG.exception("Gerador bloqueado; dados preservados")
        if status:
            status.update(stage="blocked", error=f"{type(error).__name__}: {error}")
        return 1
    finally:
        if generator:
            generator.engine.release()
            generator.store.close()
        if status:
            status.close()


if __name__ == "__main__":
    raise SystemExit(main())
