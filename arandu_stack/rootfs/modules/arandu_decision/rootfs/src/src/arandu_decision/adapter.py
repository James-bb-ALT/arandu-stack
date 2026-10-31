"""CPU-only, offline inference. Downloading is an explicit, separate operation."""
from __future__ import annotations

import asyncio
import gc
import json
import os
from pathlib import Path
import time

from .contracts import ACTION_TEXT, VERSION, candidates, decision_policy, digest
from .semantic_context import decomposition, understood_action, resolve_device, clarification, confirmation_question


class Encoder:
    def __init__(self, root: Path, threads=2):
        self.root, self.threads = root, threads
        self.model = None
        self.key = None
        self.cache = {}
        self.load_ms = 0
        self.phase = "loading"
        self._threads_configured = False

    def unload(self):
        self.model = None
        self.key = None
        self.cache.clear()
        gc.collect()

    def load(self, key):
        if key != "e5-small":
            raise ValueError("Adaptador indisponível")
        directory = self.root / key
        if not (directory / "arandu-model.json").is_file():
            raise ValueError("Pesos não instalados; execute install --model " + key)
        signature = digest({"key": key, "adapter": "embedding-v1", "manifest": json.loads((directory / "arandu-model.json").read_text())})
        self.load_ms = 0
        if self.key != signature:
            self.unload()
            started = time.perf_counter()
            import torch
            torch.set_num_threads(self.threads)
            if not self._threads_configured:
                torch.set_num_interop_threads(1)
                self._threads_configured = True
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(str(directory), device="cpu", local_files_only=True, trust_remote_code=False)
            self.model.max_seq_length = 256
            self.key = signature
            self.load_ms = (time.perf_counter() - started) * 1000
        return signature

    def readiness(self, catalog):
        """Inspect binding without loading weights or fitting calibration."""
        manifest = self.root / "e5-small" / "arandu-model.json"
        calibration_file = self.root / "e5-small-calibration.json"
        valid = False
        reason = "joint_calibration_missing"
        if manifest.is_file() and calibration_file.is_file():
            signature = digest({"key": "e5-small", "adapter": "embedding-v1", "manifest": json.loads(manifest.read_text())})
            calibration = json.loads(calibration_file.read_text())
            valid = (calibration.get("version") == VERSION and calibration.get("model") == signature
                     and calibration.get("catalog") == digest(catalog) and calibration.get("validated") is True)
            reason = "joint_calibrated" if valid else "calibration_model_or_catalog_changed"
        return {"calibration_valid": valid, "can_accept": valid, "diagnostic_only": not valid,
                "reason": reason, "loaded": self.model is not None,
                "note": "Execução automática exige confiança conjunta calibrada >= 80% e margem suficiente."}


    def infer(self, text, catalog, key, config, context=None):
        total_started = time.perf_counter()
        self.phase = "loading"
        cpu_start = time.process_time()
        signature = self.load(key)
        self.phase = "retrieval"
        started = time.perf_counter()
        rows = candidates(catalog)
        import numpy as np
        def encode(texts, query=False):
            prefix = ("query: " if query else "passage: ") if key == "e5-small" else ""
            return self.model.encode([prefix + t for t in texts], normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        descriptions = list(dict.fromkeys([r["description"] for r in rows] + list(ACTION_TEXT.values())))
        if len(self.cache) + len(descriptions) > 32768:
            self.cache.clear()
        missing = [d for d in descriptions if d not in self.cache]
        if missing:
            self.cache.update(zip(missing, encode(missing)))
        retrieval_ms = (time.perf_counter() - started) * 1000
        inference_started = time.perf_counter()
        self.phase = "inference"
        query = encode([text], query=True)[0]
        ranking = []
        for row in rows:
            target_score = float(np.dot(query, self.cache[row["description"]]))
            intent_score = float(np.dot(query, self.cache[ACTION_TEXT[row["intent"]]]))
            ranking.append({**row, "raw_score": max(-1., min(1., min(target_score, intent_score))),
                            "target_score": target_score, "intent_score": intent_score,
                            "reason": "minimum(intent cosine, device cosine); joint candidate"})
        result = self.finish(ranking, signature, catalog, key, config, inference_started, cpu_start)
        analysis_started = time.perf_counter()
        analysis = decomposition(text, catalog, context)
        fragment = analysis["action"]["text"]
        device_fragment = analysis["device"]["name"] or text
        analysis["device"]["text"] = device_fragment
        part_queries = encode([fragment or text, device_fragment], query=True)
        if fragment:
            action_query = part_queries[0]
            for item in analysis["action"]["ranking"]:
                item["semantic_similarity"] = float(np.dot(action_query, self.cache[ACTION_TEXT[item["intent"]]]))
            analysis["action"]["ranking"].sort(key=lambda item: (-item["semantic_similarity"], -item["lexical_similarity"], item["intent"]))
        devices = {}
        for item in ranking:
            device_score = float(np.dot(part_queries[1], self.cache[item["description"]]))
            devices[item["entity_id"]] = max(devices.get(item["entity_id"], -1), device_score)
        analysis["device"]["ranking"] = [{"entity_id": entity, "semantic_similarity": score}
            for entity, score in sorted(devices.items(), key=lambda item: (-item[1], item[0]))[:5]]
        result["analysis"] = analysis
        result["analysis_ms"] = (time.perf_counter() - analysis_started) * 1000
        proposal = result.get("proposal")
        known = understood_action(text)
        automatic = (result.get('decision_status') == 'accepted'
                     and isinstance(result.get('confidence'), (int, float)) and result['confidence'] >= .8)
        if proposal and known != proposal["intent"] and not (automatic and known is None):
            row = resolve_device(text, catalog, context)
            question = (confirmation_question(proposal["intent"], row, catalog)
                        if row and row["entity_id"] in proposal["target_entities"]
                        else clarification(text, catalog, context)["clarification_question"])
            result["proposal"] = {**proposal, "requires_clarification": True, "clarification_question": question}
            result["confirmation_required"] = True
        # Diagnostics are separate from the calibrated joint score; do not change
        # ranking, confidence or the calibration algorithm to authorize a guess.
        result.update(retrieval_ms=retrieval_ms, total_ms=(time.perf_counter() - total_started) * 1000)
        return result

    def finish(self, ranking, signature, catalog, key, config, started, cpu_start):
        self.phase = "validation"
        calibration_file = self.root / (key + "-calibration.json")
        calibration = json.loads(calibration_file.read_text()) if calibration_file.exists() else None
        result = decision_policy(ranking, signature, catalog, config, calibration)
        try:
            import psutil
            memory = psutil.Process().memory_info().rss
        except ImportError:
            memory = None
        result.update(model=key, model_fingerprint=signature, load_ms=self.load_ms,
                      inference_ms=(time.perf_counter() - started) * 1000,
                      cpu_ms=(time.process_time() - cpu_start) * 1000, memory_bytes=memory,
                      eligible_pairs=len(ranking), catalog_fingerprint=digest(catalog))
        return result

    def calibrate(self, catalog, key, train, validation):
        """Empirical bins fitted on joint intent+entity correctness, evaluated on disjoint labels."""
        if len(train) < 50 or len(validation) < 30 or len(train) + len(validation) > 500:
            raise ValueError("Use ≥50 exemplos de calibração e ≥30 de validação; máximo 500.")
        if {r["text"] for r in train} & {r["text"] for r in validation}:
            raise ValueError("Calibração e validação devem ter textos distintos")
        all_samples = train + validation
        if len({r["text"] for r in all_samples}) != len(all_samples):
            raise ValueError("Exemplos repetidos não contam como amostras independentes")
        known = {r["entity_id"] for r in catalog["entities"]}
        for sample in all_samples:
            if type(sample.get("text")) is not str or not 1 <= len(sample["text"]) <= 512 or type(sample.get("requires_clarification")) is not bool:
                raise ValueError("Texto ou rótulo de esclarecimento inválido")
            entities = sample.get("expected_entities")
            if type(entities) is not list or len(entities) > 1 or any(e not in known for e in entities) or sample.get("expected_intent") not in (*ACTION_TEXT, None):
                raise ValueError("Use intenção e alvo único reais; ou lista vazia para esclarecimento")
            if not sample["requires_clarification"] and (not entities or sample.get("expected_intent") is None):
                raise ValueError("Exemplo positivo exige intenção e alvo")
        bins = [{"n": 0, "correct": 0} for _ in range(5)]
        def measured(sample):
            result = self.infer(sample["text"], catalog, key, {})
            top = result["ranking"][0] if result["ranking"] else None
            correct = bool(top and sample.get("expected_intent") == top["intent"] and sample.get("expected_entities") == [top["entity_id"]] and sample.get("requires_clarification") is False)
            return min(4, max(0, int((top["raw_score"] if top else 0) * 5))), correct
        for sample in train:
            index, correct = measured(sample)
            bins[index]["n"] += 1
            bins[index]["correct"] += correct
        validation_bins = [{"n": 0, "correct": 0} for _ in bins]
        for sample in validation:
            index, correct = measured(sample)
            validation_bins[index]["n"] += 1
            validation_bins[index]["correct"] += correct
        covered = sum(v["n"] for b, v in zip(bins, validation_bins) if b["n"] >= 10)
        ece = sum(v["n"] * abs(b["correct"] / b["n"] - v["correct"] / v["n"]) for b, v in zip(bins, validation_bins) if b["n"] >= 10 and v["n"]) / covered if covered else None
        result = {"version": VERSION, "model": self.key, "catalog": digest(catalog), "bins": bins,
                  "validation_bins": validation_bins, "validation_n": len(validation), "ece": ece,
                  "validated": covered >= 30 and ece is not None and ece <= .1,
                  "dataset": digest({"train": train, "validation": validation})}
        path = self.root / (key + "-calibration.json")
        path.write_text(json.dumps(result), encoding="utf-8")
        return result
