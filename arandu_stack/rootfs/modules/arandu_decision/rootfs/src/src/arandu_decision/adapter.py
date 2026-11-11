"""Explicit CPU/Intel GPU offline inference. Downloads and conversion are separate."""
from __future__ import annotations

import asyncio
import gc
import json
import os
from pathlib import Path
import time

from .contracts import MODELS, selection, ACTION_TEXT, VERSION, candidates, decision_policy, digest
from .semantic_context import decomposition, understood_action, resolve_device, clarification, confirmation_question, hypothesis_allowed, semantic_refusal, clearly_conversational, speech_only_request


class Encoder:
    def __init__(self, root: Path, threads=2):
        self.root, self.threads = root, threads
        self.model = None
        self.key = None
        self.active_backend = None
        self.cache = {}
        self.query_cache = {}
        self.load_ms = 0
        self.phase = "loading"
        self._threads_configured = False
        self.backend_name="cpu"
        self.model_key="e5-small"
        self.verified={}
        self.measurements={}

    def unload(self):
        if self.model is not None and hasattr(self.model,"close"):self.model.close()
        self.active_backend=None
        self.model = None
        self.key = None
        self.cache.clear()
        self.query_cache.clear()
        gc.collect()

    def load(self, key, backend=None):
        started=time.perf_counter()
        from .contracts import selection
        from .installation import signature, verify
        from .backends import create_backend
        backend=backend or self.backend_name
        selection(key,backend)
        bound=signature(key,backend);self.load_ms=0
        if self.key==bound and self.model is not None:return bound
        directory=self.root/key
        identity=tuple((n,(directory/n).stat().st_size,(directory/n).stat().st_mtime_ns) for n in MODELS[key]['files'])
        if self.verified.get(key)!=identity:
            verify(directory,key);self.verified[key]=identity
        previous=(getattr(self,'active_model_key',self.model_key),self.active_backend) if self.model is not None else None
        self.unload()
        candidate=None
        try:
            import torch
            torch.set_num_threads(self.threads)
            if not self._threads_configured:
                torch.set_num_interop_threads(1);self._threads_configured=True
            candidate=create_backend(self.root,key,backend,self.threads)
            probe=candidate.encode([MODELS[key]['prefixes']['query']+'teste do Arandu'])
            import numpy as np
            if probe.shape!=(1,MODELS[key]['dimension']) or not np.isfinite(probe).all():raise ValueError('Smoke de embedding inválido')
            self.model=candidate;self.key=bound;self.model_key=key
            self.active_model_key=key
            self.backend_name=backend;self.active_backend=backend
            self.load_ms=(time.perf_counter()-started)*1000
            return bound
        except BaseException:
            candidate=None
            self.unload()
            if previous:
                try:self.load(*previous)
                except Exception:pass
            raise

    def encode(self,texts,*,query=False):
        prefix=MODELS[self.model_key]['prefixes']['query' if query else 'passage']
        values=self.model.encode([prefix+t for t in texts],normalize_embeddings=True,batch_size=32,show_progress_bar=False)
        import numpy as np
        if values.shape!=(len(texts),MODELS[self.model_key]['dimension']) or not np.isfinite(values).all():
            raise ValueError('Dimensão ou conteúdo de embedding inválido')
        metrics=getattr(self.model,'metrics',{})
        for name in ('tokenization_ms','embedding_ms'):self.measurements[name]=self.measurements.get(name,0)+metrics.get(name,0)
        return values

    def calibration_path(self,key,algorithm='baseline-v2'):
        from .installation import signature
        return self.root/(key+'-'+self.backend_name+'-'+algorithm+'-'+signature(key,self.backend_name)[:20]+'-calibration.json')

    def readiness(self, catalog, algorithm='baseline-v2'):
        from .installation import signature,installed
        key=self.model_key;bound=signature(key,self.backend_name)
        path=self.calibration_path(key,algorithm)
        try:calibration=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):calibration=None
        if algorithm=='structured-v3':
            from .structured import binding_valid,operational_scope
            valid=bool(calibration and binding_valid(calibration,bound,catalog) and calibration.get('operational_scope')==operational_scope(catalog))
        else:
            valid=bool(calibration and calibration.get('version')==VERSION and calibration.get('model')==bound
                and calibration.get('catalog')==digest(catalog) and calibration.get('validated') is True
                and calibration.get('provenance',{}).get('representative') is True)
        valid=bool(valid and installed(self.root,key))
        ready=self.model is not None and self.key==bound
        return {'algorithm':algorithm,'calibration_valid':valid,'can_accept':valid and ready,'diagnostic_only':not(valid and ready),
            'reason':'calibrated' if valid else 'combination_calibration_missing','model':key,'backend':self.backend_name,
            'loaded':ready,'runtime_ready':ready,'installed':installed(self.root,key),'deterministic_available':True}


    def infer(self, text, catalog, key, config, context=None, *, semantic_only=False):
        selection(key,config.get('backend',self.backend_name))
        self.model_key=key;self.backend_name=config.get('backend',self.backend_name);self.measurements={}
        algorithm = config.get('algorithm', 'baseline-v2')
        if algorithm not in {'baseline-v2','structured-v3'}:
            raise ValueError('Algoritmo Decision desconhecido')
        if algorithm!=getattr(self,'cache_algorithm',None):
            self.cache.clear();self.query_cache.clear();self.cache_algorithm=algorithm
        if algorithm == 'structured-v3':
            from .structured import infer
            return infer(self, text, catalog, key, config, context, rank_only=semantic_only)
        total_started = time.perf_counter()
        self.phase = "grounding"
        refusal = semantic_refusal(text, catalog) or (None if hypothesis_allowed(text, catalog) else 'semantic_compound')
        if clearly_conversational(text, catalog) or speech_only_request(text):
            refusal = refusal or 'conversation_not_action'
        if refusal:
            return {'decision_status': 'rejected', 'reason': refusal, 'proposal': None,
                    'confidence': None, 'margin': None, 'raw_score': None, 'ranking': [],
                    'model': key, 'catalog_fingerprint': digest(catalog), 'embedding_used': False,
                    'load_ms': 0, 'inference_ms': 0, 'retrieval_ms': 0,
                    'total_ms': (time.perf_counter() - total_started) * 1000}
        known = understood_action(text, catalog)
        row = resolve_device(text, catalog, context, action=known) if not semantic_only and known and hypothesis_allowed(text, catalog) else None
        if row:
            proposal = {'intent': known, 'target_entities': [row['entity_id']], 'target_area': None,
                        'confidence': None, 'requires_clarification': False, 'clarification_question': None,
                        'reason': 'explicit_action_and_unique_target'}
            return {'decision_status': 'resolved', 'reason': proposal['reason'], 'proposal': proposal,
                    'confidence': None, 'margin': None, 'raw_score': None, 'ranking': [],
                    'grounding': 'deterministic-v1', 'catalog_fingerprint': digest(catalog), 'model': key,
                    'load_ms': 0, 'inference_ms': 0, 'retrieval_ms': 0, 'analysis_ms': 0,
                    'total_ms': (time.perf_counter()-total_started)*1000, 'embedding_used': False,
                    'confirmation_required': False}
        # Spelling evidence may ask for confirmation; it never grants automatic execution.
        if not semantic_only and not known and hypothesis_allowed(text, catalog):
            analysis = decomposition(text, catalog, context)
            action_rows = analysis['action']['ranking']
            if action_rows[0]['lexical_similarity'] >= .8 and action_rows[0]['lexical_similarity'] - action_rows[1]['lexical_similarity'] >= .15:
                action = action_rows[0]['intent']
                row = resolve_device(text, catalog, context, action=action)
                if row:
                    return {'decision_status': 'confirmation_required', 'reason': 'spelling_hypothesis',
                        'proposal': {'intent': action, 'target_entities': [row['entity_id']], 'target_area': None,
                            'confidence': None, 'requires_clarification': True,
                            'clarification_question': confirmation_question(action, row, catalog), 'reason': 'spelling_hypothesis'},
                        'confirmation_required': True, 'confidence': None, 'margin': None, 'raw_score': None,
                        'ranking': [], 'analysis': analysis, 'embedding_used': False, 'model': key,
                        'catalog_fingerprint': digest(catalog), 'load_ms': 0, 'inference_ms': 0,
                        'retrieval_ms': 0, 'analysis_ms': 0, 'total_ms': (time.perf_counter()-total_started)*1000}
        self.phase = "loading"
        cpu_start = time.process_time()
        signature = self.load(key)
        self.phase = "retrieval"
        started = time.perf_counter()
        rows = candidates(catalog)
        import numpy as np
        def encode(texts, query=False):
            return self.encode(texts,query=query)
        descriptions = list(dict.fromkeys([r["description"] for r in rows] + list(ACTION_TEXT.values())))
        if len(self.cache) + len(descriptions) > 32768:
            self.cache.clear()
        missing = [d for d in descriptions if d not in self.cache]
        if missing:
            self.cache.update(zip(missing, encode(missing)))
        retrieval_ms = (time.perf_counter() - started) * 1000
        inference_started = time.perf_counter()
        self.phase = "inference"
        analysis = decomposition(text, catalog, context)
        fragment = analysis['action']['text']
        device_fragment = analysis['device']['name'] or text
        analysis['device']['text'] = device_fragment
        texts = [text, fragment or text, device_fragment]
        unique = list(dict.fromkeys(texts))
        if len(self.query_cache) + len(unique) > 128:
            self.query_cache.clear()
        absent = [value for value in unique if value not in self.query_cache]
        if absent:
            self.query_cache.update(zip(absent, encode(absent, query=True)))
        queries = [self.query_cache[value] for value in texts]
        query = queries[0]
        ranking_started=time.perf_counter()
        ranking = []
        for row in rows:
            target_score = float(np.dot(query, self.cache[row["description"]]))
            intent_score = float(np.dot(query, self.cache[ACTION_TEXT[row["intent"]]]))
            ranking.append({**row, "raw_score": max(-1., min(1., (target_score + intent_score) / 2)),
                            "target_score": target_score, "intent_score": intent_score,
                            "reason": "mean(intent cosine, device cosine); joint-v2 candidate"})
        ranking_ms=(time.perf_counter()-ranking_started)*1000
        result = self.finish(ranking, signature, catalog, key, config, inference_started, cpu_start)
        result['ranking_ms']=ranking_ms
        analysis_started = time.perf_counter()
        part_queries = queries[1:]
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
        if (result.get('decision_status') == 'uncalibrated' and result.get('ranking')
                and result['raw_score'] >= .75 and result['margin'] >= config.get('min_margin', .08)):
            top = result['ranking'][0]
            grounded = resolve_device(text, catalog, context, action=top['intent'])
            if grounded and grounded['entity_id'] == top['entity_id'] and hypothesis_allowed(text, catalog):
                result.update(decision_status='confirmation_required', reason='uncalibrated_grounded_hypothesis',
                    confirmation_required=True, proposal={'intent': top['intent'],
                        'target_entities': [top['entity_id']], 'target_area': None, 'confidence': None,
                        'requires_clarification': True,
                        'clarification_question': confirmation_question(top['intent'], grounded, catalog),
                        'reason': 'uncalibrated_grounded_hypothesis'})
        proposal = result.get("proposal")
        known = understood_action(text, catalog)
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
        calibration_file = self.calibration_path(key)
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

    def calibrate(self, catalog, key, train, validation, *, algorithm='baseline-v2', provenance=None):
        """Empirical bins fitted on joint intent+entity correctness, evaluated on disjoint labels."""
        if algorithm == 'structured-v3':
            from .structured import calibrate
            return calibrate(self,catalog,key,train,validation,provenance)
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
            result = self.infer(sample["text"], catalog, key, {}, semantic_only=True)
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
                  "validated": covered >= 30 and ece is not None and ece <= .1 and (provenance or {}).get("representative") is True,
                  "provenance":provenance or {},
                  "dataset": digest({"train": train, "validation": validation})}
        path = self.calibration_path(key)
        path.write_text(json.dumps(result), encoding="utf-8")
        return result
