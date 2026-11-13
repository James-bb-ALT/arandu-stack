"""Admin-only laboratory. The existing HA runtime remains the only executor."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
import json
from pathlib import Path
import secrets
import time
from types import SimpleNamespace

from .ai_lab_core import MODELS, benchmark, digest, finite, pipeline, settings, validate_proposal
from .client import LocalNluClient


class StageError(ValueError):
    """Safe structured diagnostics; upstream bodies may contain secrets."""
    def __init__(self, code, phase, message):
        self.code, self.phase = code, phase
        super().__init__(message)


async def local_request(session, endpoint, path, *, payload=None, body=None, timeout=90, headers=None, binary=False):
    """Reuse production local-origin validation and DNS pinning; never follow redirects."""
    from aiohttp import ClientTimeout
    client = LocalNluClient(session, endpoint)
    origin = await client._async_pinned_origin()
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if payload is not None else body
    if data is not None and len(data) > 2_097_152:
        raise ValueError("Entrada excedeu 2 MiB")
    request_headers = {"Host": client._host_header, "X-Arandu-Lab": "1", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    limit = 4_194_304 if binary else 2_097_152
    async with session.request("POST" if data is not None else "GET", origin + path, data=data, headers=request_headers,
                               timeout=ClientTimeout(total=timeout), allow_redirects=False) as response:
        raw = bytearray()
        async for chunk in response.content.iter_chunked(8192):
            raw.extend(chunk)
            if len(raw) > limit:
                raise ValueError("Resposta excedeu limite")
        if response.status != 200:
            code, phase = f"http_{response.status}", "connection" if response.status in (502, 503, 504) else "inference"
            messages = {
                'backend_unavailable':'GPU Intel indisponível. Verifique a detecção OpenVINO e a preparação IR no app. Nenhuma troca silenciosa para CPU.',
                "llm_output_truncated": "LLM atingiu o limite de tokens. Nenhuma ação autorizada. Nas opções LLM, use max_tokens 192 ou 256 no legado; 384 para planos v2 maiores. Salve e aplique o módulo.",
                "llm_invalid_json": "LLM retornou JSON inválido. Nenhuma ação autorizada. Confira o limite de geração e os logs do runtime.",
                "llm_response_invalid": "LLM retornou um envelope de resposta inválido. Nenhuma ação autorizada.",
                "llm_response_incomplete": "LLM não concluiu a resposta normalmente. Nenhuma ação autorizada.",
            }
            try:
                diagnostic = json.loads(raw)
                upstream = diagnostic.get("code", "")
                if type(upstream) is str and upstream.startswith("upstream_http_") and upstream.removeprefix("upstream_http_").isdigit():
                    code = upstream
                    phase = diagnostic.get("phase") if diagnostic.get("phase") in {"loading", "retrieval", "inference", "validation"} else "unknown"
                elif type(upstream) is str and upstream in messages:
                    code, phase = upstream, "validation"
            except (ValueError, AttributeError):
                pass
            raise StageError(code, phase,
                             messages.get(code, f"Serviço local retornou HTTP {response.status}. Consulte os logs do módulo."))
        return (bytes(raw), dict(response.headers)) if binary else json.loads(raw)


class Lab:
    def __init__(self, hass):
        self.hass = hass
        self.lock = asyncio.Lock()
        self.proposals = {}

    async def handle(self, user, payload):
        if user is None or user.is_active is not True or user.is_admin is not True:
            raise ValueError("AI Lab exige administrador ativo")
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(self.hass)
        command = payload.get("command", "run")
        from .const import DOMAIN
        entries = self.hass.config_entries.async_entries(DOMAIN)
        selected = next((e for e in entries if e.entry_id == payload.get('entry_id')), None)
        if selected is None and len(entries) == 1:
            selected = entries[0]
        stack_endpoint = selected.options.get('stack_endpoint', selected.data.get('stack_endpoint', '')) if selected else ''
        worker = payload.get('worker') or stack_endpoint or 'http://127.0.0.1:11556'
        llm_worker=stack_endpoint or worker
        if command in {"demo_suite", "demo_case"}:
            from .ai_lab_demo import suite, run_case
            if command == "demo_suite":
                return suite()
            if self.lock.locked():
                raise ValueError("Laboratório ocupado; aguarde")
            from .const import DOMAIN
            entries = self.hass.config_entries.async_entries(DOMAIN)
            entry = next((e for e in entries if e.entry_id == payload.get("entry_id")), None)
            if entry is None and len(entries) == 1:
                entry = entries[0]
            if entry is None:
                raise ValueError("Selecione a entrada NLU")
            async def demo_infer(body):
                return await local_request(session, worker, "/infer", payload=body)
            async with self.lock:
                return await run_case(payload, entry.runtime_data._client, demo_infer)
        if command == "status":
            try:
                result=await local_request(session, worker, "/status", timeout=5)
                try:
                    llms=await local_request(session,llm_worker,'/api/llm/models',timeout=5)
                    result['models']=[m for m in result['models'] if m.get('kind')!='llm']+[{**m,'kind':'llm'} for m in llms['models']]
                    decisions=await local_request(session,llm_worker,'/api/decision/models',timeout=5)
                    result['models']=[m for m in result['models'] if m.get('kind')!='decision']+[{**m,'kind':'decision'} for m in decisions['models']]
                    result['decision_backends']=decisions.get('backends',[])
                except Exception:pass
                return result
            except Exception as exc:
                return {"models": [{"id": key, **model, "available": False, "installed": None,
                                     "reason": model.get("unavailable") or "Worker inacessível; instalação não verificada"} for key, model in MODELS.items()], "error": str(exc)[:256]}
        if command in {'decision_models','decision_prepare','decision_apply'} or command=='install' and payload.get('model') in {'e5-small','minilm-multilingual','e5-base'}:
            if command=='decision_models':return await local_request(session,llm_worker,'/api/decision/models',timeout=10)
            if command=='decision_apply' and payload.get('confirm') is not True:raise ValueError('Confirmação explícita necessária')
            action={'decision_prepare':'prepare','decision_apply':'apply'}.get(command,'install')
            return await local_request(session,llm_worker,'/api/decision/'+action,payload={'model':payload.get('model','e5-small'),
                'backend':payload.get('backend','cpu'),'confirm':payload.get('confirm')},timeout=970 if action=='install' else 310)
        if command=='install' and payload.get('model') in {'tucano','qwen3'}:
            return await local_request(session,llm_worker,'/api/llm/install',payload={'model':payload['model']},timeout=360)
        if command=='llm_apply':
            if payload.get('confirm') is not True:raise ValueError('Confirmação explícita necessária')
            return await local_request(session,llm_worker,'/api/llm/apply',payload={'model':payload.get('llm_model'),'confirm':True},timeout=180)
        if command in {'transcribe', 'stt_models', 'stt_test', 'stt_compare', 'stt_install', 'stt_apply', 'stt_remove'}:
            if not stack_endpoint:
                raise ValueError('Configure o endpoint Arandu Stack nas opções da integração')
            if command == 'stt_models':
                variant = payload.get('variant', 'adapted')
                if variant not in ('adapted', 'original'):
                    raise ValueError('Variante STT inválida')
                return await local_request(session, stack_endpoint, '/api/stt/models?variant=' + variant)
            action = 'test' if command == 'transcribe' else command.removeprefix('stt_')
            model = payload.get('stt_model', payload.get('model', 'orukeet_int8'))
            variant = payload.get('variant', 'adapted')
            # Legacy selector remains compatible while the panel migrates.
            if model in ('original', 'adapted'):
                variant, model = model, 'fastconformer'
            allowed = {
                'fastconformer', 'whisper_tiny', 'whisper_base', 'whisper_small',
                'parakeet_ptbr_30s', 'parakeet_tagarela', 'orukeet_int8', 'granite_turboctc_q4',
                'parakeet_ultra_q8', 'parakeet_ultra_q4', 'phonon_2_5', 'nemotron_unified_0_6b',
            }
            if type(model) is not str or model not in allowed or variant not in ('original', 'adapted'):
                raise ValueError('Modelo STT inválido')
            body = {'model': model, 'variant': variant}
            for key in (
                'audio', 'models', 'threads', 'profile', 'reference', 'entity_reference',
                'confirm', 'preset', 'audio_processing', 'nemotron_profile',
            ):
                if key in payload:
                    body[key] = payload[key]
            if action in ('test', 'compare'):
                audio = body.get('audio')
                if type(audio) is not str or len(audio) > 1_866_668:
                    raise ValueError('Áudio excedeu 1,4 MB')
                decoded = base64.b64decode(audio, validate=True)
                if not 1 <= len(decoded) <= 1_400_000:
                    raise ValueError('Áudio vazio ou excedeu 1,4 MB')
                if action == 'compare' and (type(body.get('models')) is not list or not 1 <= len(body['models']) <= 12 or any(type(m) is not str or m not in allowed for m in body['models'])):
                    raise ValueError('Selecione 1–12 modelos STT válidos')
            if self.lock.locked():
                raise ValueError('Laboratório ocupado; aguarde')
            async with self.lock:
                result = await local_request(session, stack_endpoint, '/api/stt/' + action, payload=body, timeout=780)
            if command == 'transcribe':
                row = result['results'][0]
                if row.get('state') != 'ready':
                    raise ValueError(row.get('error', 'STT indisponível'))
                return {'text': row['final_transcript'], 'model': model, 'latency_ms': result['request_total_ms'], 'output': row, 'vad_ms': None}
            return result
        if command == "tts":
            text = payload.get("text")
            if type(text) is not str or not 1 <= len(text) <= 512:
                raise ValueError("Texto TTS inválido")
            engine = payload.get("tts_model", "piper")
            if engine not in ("piper", "pocket"):
                raise ValueError("Motor TTS inválido")
            before = time.perf_counter()
            audio, metadata = await local_request(session, stack_endpoint or payload.get("tts_endpoint", "http://127.0.0.1:8099"), "/synthesize" if stack_endpoint else "/api/synthesize",
                payload={"text": text, "engine": engine}, headers={"X-Arandu-UI": "1", "X-Arandu-Token": payload.get("tts_token", "")}, binary=True)
            if not audio.startswith(b"RIFF"):
                raise ValueError("TTS não retornou WAV")
            return {"audio": base64.b64encode(audio).decode(), "latency_ms": (time.perf_counter() - before) * 1000,
                    "first_chunk_ms": metadata.get("X-Arandu-Ttfb-Ms"), "model": engine}
        if self.lock.locked():
            raise ValueError("Laboratório ocupado; aguarde")
        async with self.lock:
            return await self._handle(user, payload, session, worker)

    async def _handle(self, user, payload, session, worker):
        from homeassistant.core import Context
        from .contextual_catalog import build, origin_area
        from .contextual_runtime import ContextualRuntime, Session
        from .contextual_protocol import Operation, parse
        from .identity import ResolvedIdentity
        from .const import DOMAIN
        entries = self.hass.config_entries.async_entries(DOMAIN)
        entry = next((e for e in entries if e.entry_id == payload.get("entry_id")), None)
        if entry is None:
            if len(entries) != 1:
                raise ValueError("Selecione entry_id da integração")
            entry = entries[0]
        runtime = ContextualRuntime(self.hass, entry.runtime_data._client, lambda: dict(entry.options))
        try:
            options = runtime.current_options()
            runtime.request_revision = runtime.options_revision
            snapshot = build(self.hass, user, options)
            catalog = snapshot.payload
            command = payload.get("command", "run")
            now = time.monotonic()
            self.proposals = {k: v for k, v in self.proposals.items() if v["expires"] > now}
            if command == "catalog":
                return {**catalog, "entities": [{**row, "state": (state.state[:255] if (state := self.hass.states.get(row["entity_id"])) else None)} for row in catalog["entities"]]}
            if command == "readiness":
                policy = {"nlu": True, "decision": options.get("decision_enabled", False), "llm": options.get("llm_enabled", False)}
                try:
                    readiness = await local_request(session, options.get('stack_endpoint') or worker, "/infer", payload={"action": "readiness", "catalog": catalog}, timeout=5)
                except Exception:
                    readiness = {"calibration_valid": None, "can_accept": False, "reason": "Serviço Decision indisponível; calibração não verificada."}
                return {"production_policy": policy, "decision": readiness}
            if command == "execute":
                saved = self.proposals.pop(payload.get("token"), None)
                if not saved or saved["user"] != user.id or saved["entry"] != entry.entry_id or saved["catalog"] != digest(catalog) or saved["options"] != digest(options) or saved.get("test_area_id", "") != payload.get("test_area_id", ""):
                    raise ValueError("Proposta expirada, alterada ou já utilizada; processe novamente")
                if payload.get("confirm") is not True:
                    raise ValueError("Confirmação explícita obrigatória")
                input_ = SimpleNamespace(text=saved["text"], context=Context(user_id=user.id), conversation_id=secrets.token_hex(16), device_id=None, satellite_id=None, language="pt-BR")
                # No confirmed=True: production independently blocks sensitive operations.
                result = await runtime.execute(saved["operations"], snapshot, input_, Session(now + 30), options, ResolvedIdentity(user.id, user, "context"))
                return asdict(result)
            if command in ("unload", "load", "install", "calibrate"):
                body = {"action": command, "catalog": catalog, "model": payload.get("model", "e5-small"),'backend':payload.get('backend','cpu')}
                body['algorithm'] = payload.get('algorithm', 'structured-v3')
                if command == "calibrate":
                    body.update(train=payload["train"], validation=payload["validation"], provenance=payload.get('provenance'))
                return await local_request(session, worker, "/infer", payload=body, timeout=180)
            if command=='decision_matrix':
                from .ai_lab_core import decision_matrix
                label_source=payload.get('label_source','administrator_supplied')
                if label_source not in {'administrator_supplied','approved_technical_fixtures'}:raise ValueError('Fonte de rótulos inválida')
                examples=payload.get('examples');combinations=payload.get('combinations')
                if type(examples) is not list or not 1<=len(examples)<=20:raise ValueError('Use 1–20 exemplos')
                if type(combinations) is not list or not 1<=len(combinations)<=6:raise ValueError('Use 1–6 combinações')
                for c in combinations:
                    if type(c) is not dict or c.get('model') not in {'e5-small','minilm-multilingual','e5-base'} or c.get('backend') not in {'cpu','gpu_intel'}:raise ValueError('Combinação inválida')
                ids={r['entity_id'] for r in catalog['entities']};areas={a['area_id'] for a in catalog['areas']}
                for example in examples:
                    if type(example) is not dict or type(example.get('text')) is not str or not 1<=len(example['text'])<=512:raise ValueError('Exemplo inválido')
                    label=example.get('expected')
                    if label is not None:
                        if type(label) is not dict or not label or set(label)-{'intent','target_entities','target_area','requires_clarification'}:raise ValueError('Rótulo inválido')
                        if 'intent' in label and label['intent'] not in (None,'turn_on','turn_off','query'):raise ValueError('Intenção inválida')
                        if 'target_entities' in label and (type(label['target_entities']) is not list or any(type(e) is not str or e not in ids for e in label['target_entities'])):raise ValueError('Alvo inválido')
                        if 'requires_clarification' in label and type(label['requires_clarification']) is not bool:raise ValueError('Rótulo inválido')
                        if label.get('target_area') is not None and label['target_area'] not in areas:raise ValueError('Área inválida')
                area=payload.get('test_area_id') or origin_area(self.hass,SimpleNamespace(device_id=None,satellite_id=None),options)
                if area is not None and area not in areas:raise ValueError('Área inválida')
                result=await local_request(session,options.get('stack_endpoint') or worker,'/infer',payload={'action':'compare',
                    'combinations':combinations,'examples':[{'text':e['text']} for e in examples],
                    'repetitions':payload.get('repetitions',3),'warmup':payload.get('warmup',1),'catalog':catalog,'context':{'origin_area':area}},timeout=310)
                return {'dry_run':True,'execution_allowed':False,'home_assistant_actions':0,'rows':result['comparisons'],
                    'metrics':decision_matrix(result['comparisons'],examples,catalog),'repetitions':payload.get('repetitions',3),
                    'warmup':payload.get('warmup',1),'label_source':label_source,'representative':False}
            if command == 'decision_compare':
                examples = payload.get('examples')
                if type(examples) is not list or not 1 <= len(examples) <= 20:
                    raise ValueError('Use 1–20 exemplos rotulados')
                area = payload.get('test_area_id') or origin_area(self.hass, SimpleNamespace(device_id=None,satellite_id=None),options)
                if area is not None and area not in {a['area_id'] for a in catalog['areas']}:
                    raise ValueError('Área de teste inválida')
                rows = []
                metrics = {a:{'labelled':0,'correct':0,'false_activations':0} for a in ('baseline-v2','structured-v3')}
                for example in examples:
                    if type(example) is not dict or type(example.get('text')) is not str or not 1 <= len(example['text']) <= 512:
                        raise ValueError('Exemplo inválido')
                    label = example.get('expected')
                    if label is not None:
                        if type(label) is not dict or not label or not set(label) <= {'intent','target_entities','target_area','requires_clarification'}:
                            raise ValueError('Rótulo expected inválido')
                        if 'intent' in label and label['intent'] not in (None,'turn_on','turn_off','query'):
                            raise ValueError('Intenção de referência inválida')
                        if 'target_entities' in label and (type(label['target_entities']) is not list or len(label['target_entities']) > 4
                                or any(type(e) is not str or e not in {r['entity_id'] for r in catalog['entities']} for e in label['target_entities'])):
                            raise ValueError('Alvo de referência inválido')
                        if 'requires_clarification' in label and type(label['requires_clarification']) is not bool:
                            raise ValueError('Rótulo de esclarecimento inválido')
                        if label.get('target_area') is not None and label['target_area'] not in {a['area_id'] for a in catalog['areas']}:
                            raise ValueError('Área de referência inválida')
                    output = await local_request(session,worker,'/infer',payload={
                        'action':'decision','text':example['text'],'catalog':catalog,'model':'e5-small',
                        'context':{'origin_area':area},'compare_algorithms':True}, timeout=180)
                    for result in output.get('algorithm_comparison', []):
                        algorithm = result.get('algorithm','baseline-v2')
                        p = result.get('proposal') or {}
                        actual = {'intent':p.get('intent'),'target_entities':p.get('target_entities',[]),
                                  'target_area':p.get('target_area'), 'requires_clarification':p.get('requires_clarification',True)}
                        if label is not None and algorithm in metrics:
                            metrics[algorithm]['labelled'] += 1
                            metrics[algorithm]['correct'] += int(all(actual[k] == value for k,value in label.items()))
                            metrics[algorithm]['false_activations'] += int(label.get('requires_clarification') is True and bool(p) and not p.get('requires_clarification'))
                    rows.append({'text':example['text'],'expected':label,
                                 'comparison':output})
                return {'dry_run':True,'execution_allowed':False,'catalog_generation':catalog['generation'],
                        'algorithm_comparison':rows,'metrics':metrics,'home_assistant_actions':0}
            if command=='llm_compare':
                from .automation_contract import validate
                examples=payload.get('examples');models=payload.get('models',['tucano','qwen3'])
                if type(examples) is not list or not 1<=len(examples)<=20:raise ValueError('Use 1–20 exemplos')
                if type(models) is not list or not 1<=len(models)<=2 or any(m not in {'tucano','qwen3'} for m in models):raise ValueError('Modelo LLM inválido')
                area=payload.get('test_area_id') or origin_area(self.hass,SimpleNamespace(device_id=None,satellite_id=None),options)
                if area is not None and area not in {a['area_id'] for a in catalog['areas']}:raise ValueError('Área inválida')
                rows=[];metrics={m:{'samples':0,'labelled':0,'correct_plan':0,'false_activations':0,'errors':0} for m in models}
                async with asyncio.timeout(180):
                    for index,example in enumerate(examples):
                        text=example.get('text') if type(example) is dict else None
                        if type(text) is not str or not 1<=len(text)<=512:raise ValueError('Exemplo inválido')
                        expected=example.get('expected_plan')
                        if expected is not None and (type(expected) is not dict or set(expected)-{'type','operations'}):raise ValueError('Rótulo expected_plan inválido')
                        compared=await local_request(session,options.get('stack_endpoint') or worker,'/infer',payload={'action':'llm_compare','models':models,
                            'text':text,'catalog':catalog,'context':{'origin_area':area}},timeout=180)
                        for result in compared['comparisons']:
                            model=result['model'];plan=result.get('plan');actual={'type':result.get('kind'),
                                'operations':[{k:o[k] for k in ('action','entity_id','parameters')} for o in (plan or {}).get('operations',[])]}
                            if actual['type']=='automation':validate(plan,text,catalog,{'origin_area':area})
                            metrics[model]['samples']+=1;metrics[model]['errors']+=int(actual['type']=='error')
                            correct=None if expected is None else all(actual[k]==v for k,v in expected.items())
                            if expected is not None:
                                metrics[model]['labelled']+=1;metrics[model]['correct_plan']+=int(correct)
                                metrics[model]['false_activations']+=int(expected.get('operations')==[] and bool(actual['operations']))
                            rows.append({'case':index,'text':text,'model':model,'correct':correct,'actual':actual,'result':result})
                return {'dry_run':True,'execution_allowed':False,'home_assistant_actions':0,'rows':rows,'metrics':metrics}
            if command not in ("run", "compare"):
                raise ValueError("Comando desconhecido")
            test_area_id = payload.get("test_area_id", "")
            if type(test_area_id) is not str or (test_area_id and test_area_id not in {a["area_id"] for a in catalog["areas"]}):
                raise ValueError("Escolha uma área de teste existente no catálogo")
            selected_area = test_area_id or origin_area(self.hass, SimpleNamespace(device_id=None, satellite_id=None), options)
            test_context = {"origin_area": selected_area, "source": "lab_selection" if test_area_id else "integration_default" if selected_area else "none"}
            settings(payload)
            await runtime._send(snapshot)

            async def nlu(text):
                raw = await runtime.client.async_interpret_v4({"version": 4, "generation": catalog["generation"], "text": text,
                    "origin_area": selected_area, "origin_area_source": "default" if selected_area else "unknown", "last_area": None, "last_targets": [], "pending": None})
                parse(raw)
                return raw

            async def run(text, model, mode):
                semantic_context = {"origin_area": selected_area,
                    'temperature_unit':getattr(getattr(getattr(self.hass,'config',None),'units',None),'temperature_unit',None)}
                async def decision(data):
                    threshold, margin = settings(payload)
                    threshold = max(.8, threshold)
                    response = await local_request(session, worker, "/infer", payload={**data, "action": "decision", "catalog": catalog, "context": semantic_context,
                        "model": model,"backend":payload.get("backend","cpu"), "config": {"threshold": threshold, "min_margin": margin, "algorithm":payload.get('algorithm','structured-v3')}})
                    if response.get("decision_status") == "accepted":
                        confidence, gap = response.get("confidence"), response.get("margin")
                        if not finite(confidence) or confidence < threshold or not finite(gap) or gap < margin:
                            raise ValueError("Worker retornou aceitação sem confiança/margem válidas")
                        proposed = validate_proposal(response.get("proposal"), catalog)
                        top = response["ranking"][0]
                        if proposed["target_entities"] != [top["entity_id"]] or proposed["intent"] != top["intent"] or proposed["confidence"] != confidence:
                            raise ValueError("Proposta não corresponde ao ranking calibrado")
                    if response.get('decision_status') in {'resolved', 'confirmation_required', 'accepted'}:
                        from .intelligence import accepted_decision
                        accepted_decision(response, catalog, text, semantic_context)
                    return response
                async def llm(data):
                    # LLM service performs its own retrieval when Decision was not called.
                    from .intelligence import llm_hint
                    data = {**data,'decision':llm_hint(data.get('decision'))}
                    raw = await local_request(session, options.get('stack_endpoint') or worker, "/infer", payload={**data, "action": "llm", "catalog": catalog, "context": semantic_context, "llm_model": payload.get("llm_model", "tucano"),'contract_version':payload.get('llm_contract',2)})
                    if raw.get('contract_version')==2 and raw.get('kind') in {'automation','clarification','conversation'}:
                        if raw['kind']=='automation':
                            from .intelligence import typed_operations
                            typed_operations(raw,text,snapshot,semantic_context)
                        return raw
                    retrieval = raw.get("retrieval") or data.get("decision") or {}
                    proposal = validate_proposal(raw["proposal"], catalog, {r["entity_id"] for r in retrieval.get("ranking", [])})
                    proposal["confidence"] = None
                    return {**raw, "proposal": proposal}
                from .semantic_context import clearly_conversational
                result = await pipeline(text, nlu, decision, llm, mode=mode, timeout=95, general=clearly_conversational(text, catalog))
                result.update(dry_run=True, catalog_generation=catalog["generation"], model=model,
                              catalog=catalog, test_mode="independent", test_context=test_context,
                              production_policy={"nlu": True, "decision": options.get("decision_enabled", False), "llm": options.get("llm_enabled", False)})
                proposal = result["proposal"]
                if proposal and result["source"] != "nlu" and result.get("kind") != "conversation":
                    from .intelligence import proposal_route
                    from .semantic_context import clarification, confirmation_question
                    output = result["stages"][result["source"]].get("output") or {}
                    route = proposal_route(text, proposal, catalog, semantic_context, output.get("confirmation_required") is True,
                        auto_decision=result['source'] == 'decision' and output.get('decision_status') in {'accepted', 'resolved'})
                    if route == "confirmation_required":
                        row = next(r for r in catalog["entities"] if r["entity_id"] == proposal["target_entities"][0])
                        question = confirmation_question(proposal["intent"], row, catalog)
                        proposal = {**proposal, "requires_clarification": False, "clarification_question": None}
                        result.update(proposal=proposal, confirmation_required=True, clarification_question=question)
                    elif route == "clarification":
                        proposal = clarification(text, catalog, semantic_context, reason="action_or_target_unresolved")
                        result.update(proposal=proposal, clarification_question=proposal["clarification_question"])
                operations = ()
                if result.get('plan') and result.get('kind')=='automation':
                    from .intelligence import typed_operations
                    operations=typed_operations(result['stages']['llm']['output'],text,snapshot,semantic_context)
                elif proposal and result["source"] == "nlu":
                    operations = parse(proposal).operations
                elif proposal:
                    validate_proposal(proposal, catalog)
                    if not proposal["requires_clarification"] and proposal["intent"] not in ("conversation", None):
                        by_entity = {row["entity_id"]: key for key, row in snapshot.by_id.items()}
                        operations = (Operation("lab." + proposal["intent"], proposal["intent"], tuple(sorted(by_entity[e] for e in proposal["target_entities"])), {}, evidence=("ai_lab_review",)),)
                if operations:
                    validation_started = time.perf_counter()
                    try:
                        prepared = tuple(runtime.prepare(op, snapshot, user, options) for op in operations)
                        runtime.validate_plan(prepared)
                        runtime.validate_bulk_snapshot(operations, snapshot, user, options)
                        result["action_json"] = [op.as_dict() for op in operations]
                        result["target_entities"] = sorted({snapshot.by_id[t]["entity_id"] for op in operations for t in op.targets})
                        result["sensitive"] = any(p.needs_confirmation for p in prepared)
                        result["stages"]["validation"] = {"status": "executed", "result": "confirmation_required" if result["sensitive"] or result.get("confirmation_required") else "accepted"}
                    except Exception as exc:
                        result.update(validation_error=str(exc), action_json=None, clarification_question="Não foi possível validar essa ação no Home Assistant.")
                        result["stages"]["validation"] = {"status": "error", "error": type(exc).__name__, "phase": "validation", "reason": str(exc)[:256]}
                        operations = ()
                    result["stages"]["validation"]["latency_ms"] = (time.perf_counter() - validation_started) * 1000
                else:
                    result["stages"]["validation"] = {"status": "skipped", "reason": "Nenhuma ação HA proposta" if result.get("kind") == "conversation" else "Nenhuma operação proposta"}
                if result.get("kind") == "conversation":
                    if result.get("response_text"):
                        result["stages"]["response"] = {"status": "executed", "reason": "Resposta gerada pelo LLM"}
                    else:
                        result["stages"]["response"] = {"status": "error", "reason": result.get("reason") or "llm_conversation_empty"}
                else:
                    result["stages"]["response"] = {"status": "executed", "reason": result["clarification_question"] or "Diagnóstico concluído"}
                result["stages"] = {name: result["stages"][name] for name in ("vad", "stt", "nlu", "decision", "llm", "validation", "ha", "response", "tts")}
                result["operations_validated"] = bool(operations)
                return result, operations

            if command == "compare":
                models = payload.get("models", [payload.get("model", "e5-small")])
                examples = payload.get("examples", [])
                if type(models) is not list or not 1 <= len(models) <= 4 or any(m not in ("brazembed", "e5-small", "minilm-multilingual", "e5-base", "saracura", "jatoba") for m in models) or type(examples) is not list or not 1 <= len(examples) <= 20:
                    raise ValueError("Compare 1–4 motores e 1–20 exemplos por lote")
                output = {}
                for example in examples:
                    if type(example) is not dict or type(example.get("text")) is not str:
                        raise ValueError("Exemplo deve conter text")
                    label = example.get("expected")
                    if label is not None and (type(label) is not dict or not label or not set(label) <= {"intent", "target_entities", "target_area", "requires_clarification"}):
                        raise ValueError("Rótulo expected inválido")
                for model in models:
                    rows = []
                    for example in examples:
                        result, operations = await run(example["text"], model, payload.get("mode", "pipeline"))
                        expected = example.get("expected")
                        actual = None
                        if operations:
                            areas = {snapshot.by_id[t].get("area_id") for op in operations for t in op.targets}
                            actual = {"intent": operations[0].action, "target_entities": result["target_entities"], "target_area": next(iter(areas)) if len(areas) == 1 else None, "requires_clarification": False}
                        elif expected is not None:
                            actual = {"intent": None, "target_entities": [], "target_area": None, "requires_clarification": True}
                        rows.append({"expected": expected, "actual": actual, "result": result})
                    output[model] = {"rows": rows, "metrics": benchmark(rows)}
                return {"catalog_generation": catalog["generation"], "sequential": True, "dry_run": True, "models": output}
            result, operations = await run(payload.get("text"), payload.get("model", "e5-small"), payload.get("mode", "pipeline"))
            if operations and not result.get("sensitive") and not result.get("confirmation_required"):
                token = secrets.token_urlsafe(24)
                if len(self.proposals) >= 32:
                    self.proposals.pop(next(iter(self.proposals)))
                self.proposals[token] = {"user": user.id, "entry": entry.entry_id, "catalog": digest(catalog), "options": digest(options),
                                         "operations": operations, "text": result["text"], "test_area_id": test_area_id, "expires": time.monotonic() + 30}
                result["execution_token"] = token
            return result
        finally:
            runtime.close()


async def async_register(hass):
    """Register one sidebar panel and authenticated websocket route for all entries."""
    if "local_nlu_ai_lab" in hass.data:
        return
    from homeassistant.components import panel_custom, websocket_api
    from homeassistant.components.http import StaticPathConfig
    lab = Lab(hass)

    @websocket_api.websocket_command({"type": "local_nlu/ai_lab", "payload": dict})
    @websocket_api.async_response
    async def handle(connection_hass, connection, msg):
        try:
            result = await lab.handle(connection.user, msg["payload"])
            connection.send_result(msg["id"], result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            connection.send_error(msg["id"], "ai_lab_error", str(exc)[:256])
    websocket_api.async_register_command(hass, handle)
    await hass.http.async_register_static_paths([
        StaticPathConfig("/local_nlu_ai_lab/stt.js", str(Path(__file__).with_name("stt_lab_panel.js")), False),
        StaticPathConfig("/local_nlu_ai_lab/panel.js", str(Path(__file__).with_name("ai_lab_panel.js")), False),
        StaticPathConfig("/local_nlu_ai_lab/brand", str(Path(__file__).with_name("brand")), False),
    ])
    await panel_custom.async_register_panel(hass, webcomponent_name="arandu-ai-lab", frontend_url_path="arandu-ai-lab",
        module_url="/local_nlu_ai_lab/panel.js", sidebar_title="Arandu AI Lab", sidebar_icon="mdi:flask-outline", require_admin=True)
    hass.data["local_nlu_ai_lab"] = lab
