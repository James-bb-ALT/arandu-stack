# Arquitetura e Especificação Técnica: Model Residency & LLM Model Manager

**Integração da Fase 4:** commit `549dc61` incorporado sem substituir o runtime
existente. Estes componentes permanecem isolados, sem novas rotas HTTP montadas.
Seus 20 testes usam fixtures/mocks; LFM2.5, LFM3 e Qwen3 1.7B não foram homologados
por inferência nesta entrega. O padrão de produção continua Tucano e o Qwen3 0.6B
existente é preservado. `DEFAULT_MODEL_ID` deste catálogo não altera essa seleção.
Residência/evicção aqui são registros contábeis: precisam de callbacks reais de
carga/descarga e verificação do processo antes de serem expostos como estado real.

**Módulo:** Arandu Assistant / App Control Plane  
**Branch de Desenvolvimento Isolado:** `feat/arandu-model-residency-llm-manager`  
**Escopo:** Catálogo com os três novos modelos LLM, download atômico GGUF com SHA-256 verificado, gerenciamento de residência em memória RAM (`RESIDENT`, `LAZY`, `STANDBY`), evicção cooperativa com STT/TTS e troca segura de modelo com rollback automático.

---

## 1. Catálogo experimental fornecido

Revisões, tamanhos e hashes fornecidos pelo commit de origem, mantidos abaixo. A incorporação não comprova compatibilidade de inferência com o llama.cpp instalado:

| Identificador | Nome Oficial | Família | Quantização | Tamanho (Bytes) | Hash SHA-256 | Licença |
|---|---|---|---|---|---|---|
| `lfm2.5-1.2b` | **LFM2.5-1.2B-Instruct QAD-Q4_0** | Liquid AI | QAD-Q4_0 | 695.755.488 | `bb741ebb106d543e9de114b843a3d3d73d51c74b5801e69da2abde821a0cb3e1` | LFM Open License |
| `lfm3-1.7b` | **LFM3-1.7B QAD-Q4_0** | Liquid AI | QAD-Q4_0 | 1.049.615.680 | `43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452` | LFM Open License |
| `qwen3-1.7b` | **Qwen3-1.7B Q4_K_M** | Qwen | Q4_K_M | 1.282.439.584 | `72c5c3cb38fa32d5256e2fe30d03e7a64c6c79e668ad84057e3bd66e250b24fb` | Apache-2.0 |
| `tucano` *(legado)*| **Tucano 2 Qwen 0.5B Instruct Q4_K_M** | Tucano | Q4_K_M | 305.603.296 | `49f16e9d27db128a39a3d976271a297f1ef183b763c62ca9a0c0ba7c72c55c91` | Apache-2.0 |

### Repositórios e Revisões no Hugging Face:
- `lfm2.5-1.2b`: `LiquidAI/LFM2.5-1.2B-Instruct-GGUF` (rev `8ed288026e23958ad9dfa92d53ed773a8eee7125`)
- `lfm3-1.7b`: `LiquidAI/LFM3-1.7B-GGUF` (rev `cd0c74c575b5c416f3011b5b4c298077f26ca8c2`)
- `qwen3-1.7b`: `bartowski/Qwen_Qwen3-1.7B-GGUF` (rev `dcb19155b962dbb6389f4691a982043a8e651022`)
- `tucano`: `2DTi/tucano2-0.5b-instruct-gguf` (rev `cbb5318b66a49e20967623e946365bcc8594c29a`)

---

## 2. Componentes Implementados

### 1. `arandu_stack/app/llm_catalog.py`
- Registro imutável `MODELS` com metadados estruturados de arquitetura, parâmetros, contexto e limites de memória.
- Função `verify_model_file(path, model_id)`:
  - Valida se o arquivo existe e confere tamanho exato em bytes.
  - Valida número mágico de 4 bytes do cabeçalho binário (`b'GGUF'`).
  - Executa streaming SHA-256 em blocos de 1 MB, abortando se houver divergência.
- Funções utilitárias: `validate_model_id`, `is_installed`, `get_model_file_path`, `describe_model`.

### 2. `arandu_stack/app/llm_downloads.py` (`LLMDownloadManager`)
- **Staging Seguro:** O download ocorre em pasta temporária isolada no mesmo filesystem (`llm-staging-XXXX/`).
- **Verificação Prévia de Disco:** `check_disk_space(model_id)` garante que há espaço livre para o arquivo mais margem de segurança de 64 MB.
- **Transparência e Observabilidade:** O progresso é gravado progressivamente em `.install-{model_id}.json` e emitido via callback assíncrono.
- **Promoção Atômica:** Somente após validação completa de tamanho, SHA-256 e cabeçalho GGUF o arquivo é movido via `os.replace` para `{root}/{model_id}/{file}`, junto com o manifesto `{root}/{model_id}/arandu-model.json`.
- **Cancelamento Limpo:** Eventos de cancelamento assíncrono (`DownloadCancelledError`) encerram o stream e apagam o diretório de staging sem corromper instalações prévias.
- **Exclusão Segura:** `delete_model(model_id)` impede a remoção acidental do modelo ativo em produção.

### 3. `arandu_stack/app/model_memory.py` (`ModelMemoryManager`)
- **Políticas de Residência (`ResidencyPolicy`):**
  - `RESIDENT`: Carregado permanentemente no startup, zero cold start para o Assistente.
  - `LAZY`: Carregado sob demanda na primeira requisição e mantido em cache LRU.
  - `STANDBY`: Metadados preparados, pesos descarregados para economizar memória.
  - Persistidas em `model-residency.json` em `data_dir`.
- **Orçamento e Headroom Cooperativo:**
  - Orçamento padrão de 2048 MB para o subsistema de modelos.
  - Reserva contábil de 512 MB para STT e 256 MB para TTS. Essas estimativas não garantem ausência de OOM; o Orukeet residente real pode exceder a reserva e o runtime precisa observar a RAM disponível.
- **Estimativa de Footprint Dinâmica:**
  - Modelos Liquid AI (LFM) combinam convoluções curtas e GQA, consumindo ~30% do KV-cache de transformers puros.
- **Evicção Graciosa:** Ordem estrita de descarte:
  1º Modelos em `STANDBY`.  
  2º Modelos em `LAZY` ordenados por tempo de último uso crescente (LRU).  
  3º Modelos `RESIDENT` apenas em situação crítica com alvo também prioritário.

### 4. `arandu_stack/app/llm_lab.py` (`LLMModelManager`)
- Orquestrador principal integrando catálogo, download e memória.
- `list_models()`: Expõe status completo para APIs e WebUI.
- `switch_model(model_id, confirm=True, restart_callback=None)`:
  - Exige confirmação explícita.
  - Salva estado anterior em `llm-last-good.json`.
  - Executa evicção cooperativa de memória antes de instanciar o novo modelo.
  - Monitora prontidão de serviço via healthcheck.
  - **Rollback Automático:** Caso o novo modelo falhe ao inicializar ou dê timeout, restaura instantaneamente o modelo anterior do `last-good`.
- `preload_resident_models(loader_callback)`: Inicializa no boot os modelos configurados como `RESIDENT` que couberem no headroom.

---

## 3. Interfaces para Conexão com a Fase 4 do Codex

Estes componentes foram desenvolvidos de forma 100% isolada e sem tocar nos arquivos compartilhados que o Codex está alterando. Para a unificação final com a Fase 4, os seguintes pontos de conexão foram deixados prontos:

1. **`arandu_stack/app/server.py`:**
   - Instanciar `LLMModelManager(manager.data)` no setup da aplicação web.
   - Rotas REST prontas para montagem:
     - `GET /api/llm/models` -> `manager.list_models()`
     - `POST /api/llm/models/install` -> `manager.start_install(model_id)`
     - `POST /api/llm/models/cancel` -> `manager.cancel_install(model_id)`
     - `POST /api/llm/models/delete` -> `manager.delete_model(model_id)`
     - `POST /api/llm/models/residency` -> `manager.set_residency_policy(model_id, policy)`
     - `POST /api/llm/models/apply` -> `manager.switch_model(model_id, confirm=True, restart_callback=...)`
2. **`arandu_stack/app/modules.py` / `module_worker.py`:**
   - No ciclo de vida do módulo `llm`, invocar `preload_resident_models()` na inicialização do serviço.
3. **`arandu_stack/app/settings.py`:**
   - Mapear a chave `llm_residency_policy` e o modelo padrão ativo `llm_model: 'lfm2.5-1.2b'`.
4. **Dashboard WebUI (`arandu_stack/app/static/`):**
   - Cartões de seleção de modelo no AI Lab / painel LLM exibindo status de residência (`Residente`, `Sob Demanda`, `Standby`), botão de alternância e barra de progresso de download em tempo real.

---

## 4. Resultados da Suíte de Testes

- `tests/test_llm_catalog.py`: 5 testes aprovados (100%).
- `tests/test_llm_downloads.py`: 4 testes aprovados (100%).
- `tests/test_model_memory.py`: 5 testes aprovados (100%).
- `tests/test_llm_model_manager.py`: 6 testes aprovados (100%).
- Suíte completa de regressão do Arandu: **302 testes aprovados, 22 skipped, 0 falhas**.


---

## 6. Integração Real de Residência em RAM (`v11.1.2`)

A partir da versão `11.1.2`, a política de residência em RAM do LLM foi conectada ao runtime real:
- **Configuração (`modules.py` / `settings.py`):** campo `resident_policy` em `DEFAULTS['llm']` (padrão `'LAZY'`, opções `'RESIDENT'`, `'LAZY'`, `'OFF'`), validado estritamente e propagado via `ARANDU_LLM_RESIDENCY` e `runtime_settings`.
- **Ciclo de vida real (`arandu_llm/service.py`):**
  - `RESIDENT`: pré-carrega o modelo configurado no hook `on_startup` (`warm`) após checar headroom mínimo de `512 MiB` (`MIN_HEADROOM_MB`), expondo `load_state='degraded'` e `load_error_code='memory_pressure'` se não houver RAM suficiente.
  - `LAZY`: inicia descarregado (`load_state='unloaded'`), carregando sob demanda na primeira inferência e mantendo o processo aquecido para reutilização.
  - `OFF`: mantém o modelo descarregado (`load_state='off'`) e recusa inferências com erro explícito (`code='llm_disabled_by_residency'`, HTTP 422).
- **Telemetria honesta (`/status`, `server.py`, `app.js`):** expõe `resident_policy`, `configured_model`, `is_model_loaded`, `model_ready`, `load_state`, `load_error_code`, `loaded_since`, `last_load_ms`, `process_rss_mb`, `available_memory_mb`, `preload_attempted`, `preload_success`, `recoveries_count` e `cold_start_last_request`.
- **Falso positivo corrigido (`llm_lab.py`):** `preload_resident_models()` sem `loader_callback` real não registra mais o modelo como carregado.
- **Testes:** coberto por `tests/test_llm_residency_integration.py` (usando `TestServer`/`TestClient` com adaptador simulado, sem execução física em HAOS).
