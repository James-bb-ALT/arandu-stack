# Arandu Stack v11.3.0 — STT Multimodelo + Silero VAD v6.2 + WebRTC Audio Processing

## Resumo da Implementação

Esta atualização estende o subsistema `arandu_stt`, o AI Lab e a Dashboard do Arandu Stack mantendo o motor de produção (`orukeet_int8`) e todos os filtros acústicos **desativados por padrão** em produção (`vad_enabled=False`, `high_pass_filter=False`, `noise_suppression=False`, `auto_gain_control=False`, `echo_cancellation=False`), sem alterar NLU, Decision Engine ou LLM.

## Componentes Implementados

### 1. Novos Backends STT (`load()`, `recognize()`, `close()` persistentes)

| Identificador | Modelo | Runtime | Licença | Revisão Hugging Face (Imutável) | Tamanho | SHA-256 Verificado |
|---|---|---|---|---|---|---|
| `parakeet_ultra_q8` | Parakeet Ultra Q8_0 | `transcribe.cpp` (CPU persistente) | CC-BY-4.0 | `a1539be1b849ea7463b88343e5277b373d0c4ea9` | 740.363.168 B | `007a59761e9258779f189df396b50d12b83bb5b79e66df4b955c230d2f2a0a59` |
| `parakeet_ultra_q4` | Parakeet Ultra Q4_K_M | `transcribe.cpp` (CPU persistente) | CC-BY-4.0 | `a1539be1b849ea7463b88343e5277b373d0c4ea9` | 486.280.096 B | `c69b7a5f9071a7afd8a1818ef7ae542c386c4b01ed0cac380c19a99947646bf9` |
| `phonon_2_5` | Phonon 2.5 | `fermion-research` (em memória) | CC-BY-4.0 | `2af4bb8ccceb66a8ce06c4081bb968f1802c1e58` | 156.181.652 B | `49da2dbe8301433caf7486368afa98c8d1a0e0477fbbc465895713ff6b510a66` |
| `nemotron_unified_0_6b` | NVIDIA Nemotron Labs Unified ASR 0.6B | `nemo.collections.asr` (opcional/isolado) | OpenMDW-1.1 | `023ce907267a09a308a5cb69239447cb3d0c1597` | 2.507.735.040 B | `1718cc1b44fb8597b2356328d706a8d89f31c389ca6f2256c2e3f743c0246936` |

> **Nota de Licença OpenMDW-1.1:** O checkpoint `nemotron-labs-unified-asr-0.6b.nemo` é opcional, não é pré-carregado e preserva os termos originais da licença OpenMDW-1.1 no catálogo e manifesto.

- **Arquivos criados/atualizados:**
  - `arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/parakeet_ultra_backend.py`
  - `arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/phonon_backend.py`
  - `arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/nemotron_unified_backend.py` (offline + perfis streaming `precision` 560/560ms, `balanced` 160/400ms, `fast` 80/160ms, `experimental` 80/80ms com `left_context_ms=5600`)
  - `arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/backends.py`
  - `arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/model_catalog.py` (inclui `remove()`)
  - `arandu_stack/model_registry/models.json` (total: 29 modelos; 13 STT)

### 2. Silero VAD ONNX v6.2

- **Arquivo:** `arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/vad_processor.py`
- **Runtime:** `onnxruntime` CPU (sem PyTorch).
- **SHA-256 fixado:** `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` (`silero_vad.onnx` v6.2).
- **Parâmetros padrão:** 16.000 Hz, threshold `0.50`, min speech `180 ms`, min silence `450 ms`, pre-roll `300 ms` (ring buffer), post-roll `200 ms`, estado recorrente `(2, 1, 128)` e contexto `64` amostras isolados por sessão.

### 3. WebRTC Audio Processing

- **Arquivo:** `arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/audio_processor.py`
- **Recursos:** Framing de 10 ms (160 amostras @ 16 kHz), resampling de 8/16/32/44.1/48 kHz para 16 kHz mono, High-pass filter, Noise Suppression (níveis 0–3, padrão 1), AGC2 (desativado por padrão) e AEC3.
- **Regra estrita de AEC:** AEC3 só é marcado como ativo quando há `far_end_pcm` real; na ausência de referência far-end (ex.: Wyoming satélite sem loopback), `aec_diagnostics()` reporta explicitamente `reference_available=False` e `active=False`.
- **Presets no AI Lab:** `original`, `vad`, `balanced`, `custom`, com retorno de `processed_wav_b64` para audição pós-filtro.

### 4. Dashboard & AI Lab

- **Dashboard (`arandu_stack/app/static/app.js`, `settings.py`, `modules.py`, `server.py`):**
  - Seção `Processamento de áudio` nas configurações do STT (tudo `False` por padrão).
  - Gerenciador de modelos com runtime, licença, RAM estimada, latência de carga e botão **Remover** (`POST /api/stt/remove`).
- **AI Lab (`custom_components/local_nlu/stt_lab_panel.js`, `ai_lab.py`):**
  - Novos modelos selecionáveis e comparáveis (até 12 modelos), seletor de perfil Nemotron, presets acústicos, controles individuais (`custom`), remoção de modelos e player do áudio pós-processado.

## Matriz de Validação & Limitações de Ambiente

- **Testes automatizados executados (Windows dev venv, Python 3.13.9):**
  - `pytest tests/test_stt_v11_3_vad_webrtc.py tests/test_pipeline_universal_v11_2.py tests/test_stt_multimodel.py -q`: **25 passed** (contratos de backends, erros explícitos quando runtimes nativos estão ausentes, state machine Silero VAD, gating de referência far-end no AEC, resampling, remoção de modelos, defaults de produção, preservação de negação `"não desligue"`).
- **Limitações honestas (pendente medição em hardware-alvo HAOS x86-64 Intel Core i5-6500T, 16 GB RAM):**
  - O ambiente de desenvolvimento Windows não possui `transcribe_cpp`, `fermion_research`, `pywebrtc_audio`, `onnxruntime` nem `nemo` instalados para inferência real de áudio.
  - Latência real de inferência, RTF, RSS de RAM em produção e adequação de tempo real do Nemotron Unified ASR 0.6B no i5-6500T permanecem **pendentes de medição no container Linux x86-64**.
