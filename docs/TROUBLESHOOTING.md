# Troubleshooting — Arandu Stack

## 1. Isolamento de ambientes Python (`/opt/venvs/*`)

Para impedir conflitos transitivos entre bibliotecas de IA (como `huggingface-hub==0.36.0` + `transformers==4.57.3` no Decision/STT vs. `transformers>=5.0` exigido por `fermion-research`), cada módulo roda em seu próprio `venv`:

- `/opt/venvs/stt`: FastConformer (`sherpa-onnx`), Whisper (`faster-whisper` + `ctranslate2`), Parakeet TDT (`onnx-asr` + `onnxruntime`), Parakeet Ultra / Granite (`transcribe_cpp`), WebRTC (`pywebrtc_audio`), Silero VAD (`onnxruntime`).
- `/opt/venvs/phonon`: `fermion-research==0.2.15` herdando `torch==2.9.1` CPU-only do sistema (`--system-site-packages`), acionado via worker persistente isolado por `phonon_backend.py`.
- `/opt/venvs/decision`: `sentence-transformers==5.2.0`, `transformers==4.57.3`, `huggingface-hub==0.36.0`, OpenVINO/ONNX.
- `/opt/venvs/llm`: `aiohttp`, `huggingface-hub==0.36.0`, `llama-server`.
- `/opt/venvs/tts`: `piper-tts`, `pocket-tts`, `wyoming`.
- `/opt/venvs/wake`: `wyoming-openwakeword`, `pyopen-wakeword`.
- `/opt/venvs/nemotron` (opcional/experimental): NVIDIA NeMo não é pré-instalado na imagem CPU padrão; sem ele, `nemotron_unified_0_6b` aparece com `runtime_available: false` (`MODEL_RUNTIME_UNAVAILABLE`) sem afetar os demais backends.

## 2. Verificação de dependências antes do build

```bash
python scripts/check_dependencies.py --dry-run
```

Dentro de uma imagem já construída:

```bash
python scripts/docker_smoke_test.py --image arandu:preflight
```

## 3. Códigos estruturados de erro (`ARANDU_EVENT`)

| Código | Significado | Ação recomendada |
|---|---|---|
| `DEPENDENCY_CONFLICT` | Conflito de pacotes / falha no `pip check` | Rodar `scripts/check_dependencies.py --dry-run` e isolar o runtime conflitante |
| `NATIVE_LIBRARY_MISSING` | Biblioteca nativa ausente (`libtranscribe.so`, `sherpa_onnx`, etc.) | Verificar estágio de build e `ldconfig` |
| `MODEL_RUNTIME_UNAVAILABLE` | Runtime opcional ausente (`/opt/venvs/phonon` ou `/opt/venvs/nemotron`) | Selecionar backend suportado ou instalar o runtime isolado |
| `MODEL_LOAD_FAILED` | Falha ao carregar pesos na RAM | Conferir memória livre e logs do modelo |
| `MODEL_INTEGRITY_FAILED` | SHA256 ou revisão divergente do catálogo | Reinstalar o modelo pela dashboard |
| `STT_TIMEOUT` | Inferência excedeu timeout configurado (`PHONON_TIMEOUT_SEC`, etc.) | Reduzir carga/threads ou verificar áudio muito longo |
| `NLU_TIMEOUT` / `DECISION_UNAVAILABLE` / `LLM_TIMEOUT` | Estágio de raciocínio indisponível ou lento | NLU direto permanece protegido; verificar serviço afetado |
| `TTS_FAILURE` | Falha na síntese de voz | O comando Home Assistant não é repetido |
| `QUEUE_SATURATED` | Fila de inferência cheia | Aguardar conclusão das requisições em andamento |
| `MODULE_STARTUP_FAILED` | Falha ao iniciar serviço/venv | Inspecionar `supervisor` / container logs |

## 4. Segurança de atualização e recuperação no Home Assistant Supervisor

- **Dados e modelos em `/data`**: Pesos instalados são gravados de forma atômica via diretório `.staging-*` + backup `.previous` antes da substituição em `model_catalog.py`. Uma falha de download/verificação restaura `.previous` automaticamente.
- **Falha de build de imagem no Supervisor**: O Home Assistant Supervisor **não faz rollback automático de imagem Docker** caso um novo build falhe; porém, quando o build falha antes de iniciar o novo container, a versão anterior em execução e os dados persistidos em `/data` permanecem preservados.
- **Procedimento de recuperação**:
  1. Antes de atualizar o add-on, crie um **Backup** pelo painel do Home Assistant (Configurações → Sistema → Backups).
  2. Se um build falhar, não remova `/data` — corrija ou volte para uma release estável anterior no repositório e clique em **Reconstruir / Atualizar**.
  3. Se uma troca de modelo STT falhar, o `server.py` / `stt_lab.py` preserva a seleção anterior funcional.
