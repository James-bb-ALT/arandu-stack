# Arandu Decision 3.0.0 — proposta da Fase 3.1

Serviço passivo multimodelo: E5 Small (padrão), MiniLM Multilingual L12 e E5 Base. CPU preserva SentenceTransformer/PyTorch; GPU Intel usa OpenVINO 2024.6, target GPU explícito, FP32, mean pooling com máscara e normalização. GPU indisponível nunca troca silenciosamente para CPU. Apenas o HA valida e executa ações.

Revisões, arquivos, tamanhos, SHA-256 e licenças em `model_manifests.json`. Instalação individual explícita, staging e verificação; inferência offline. E5 usa query:/passage:, MiniLM não. Dimensões 384/384/768 e tokenizer/pooling próprios. Um único modelo/backend residente. Cache e calibração separados por assinatura efetiva, precisão, conversor, versão do tokenizer/runtime e algoritmo. Artefatos de calibração anteriores permanecem em disco e não autorizam a combinação nova.

CPU: `arandu-decision install --model e5-small --models /data/models` e `arandu-decision serve --model e5-small --backend cpu --models /data/models`.
GPU (Python 3.12 no app): instale o extra `[gpu]`, use `arandu-decision prepare --model e5-small --models /data/models` para exportação local explícita; depois `serve --backend gpu_intel`. Preparar IR não comprova GPU. Readiness exige dispositivo enumerado, compilação exclusiva GPU, execução numérica e dimensão verificadas. `video: true` pede acesso suportado pelo Supervisor; não exige privilégios extras nem instala driver/kernel no host.

Dashboard: instalar/verificar pesos, preparar GPU quando desejado e aplicar seleção com confirmação. A API só persiste após carga/smoke e restaura a seleção anterior em falha. Endpoints internos protegidos `/api/decision/models`, `/install`, `/prepare`, `/apply` no app; serviço loopback 11557 mantém `/infer` e acrescenta `/install`. Clientes antigos continuam podendo pedir E5 Small/CPU. O AI Lab nativo compara até seis combinações em dry-run e exporta somente métricas.

Relatório e handoff: `docs/PHASE-3.1-DECISION-MULTIMODEL.md` na raiz. Versão global/release ficam para integração com a Fase 3.2; esta branch não publica tag/release.

## Histórico até Decision 2.0.0

## Decision 2.0.0

`structured-v3` separa intenção e entidade em embeddings distintos, com área como filtro. Mantém a média dos cosines, pesos E5 residentes e caches limitados. `baseline-v2` permanece selecionável para comparação/rollback. A calibração v3 usa artefato próprio; não reaproveita probabilidades anteriores nem valida fixtures sintéticas para produção. O HA valida e executa, o serviço apenas propõe. Consulte `docs/DECISION-v2.0.0.md` na raiz.

## Atualização 1.0.1

Política semântica compartilhada: restrições operacionais recusadas antes da carga/inferência; compostos não viram proposta parcial. Nomes registrados, consultas e conversa são tratados pelo contexto. Modelos, embeddings e calibração preservados.

## Atualização 1.0.0

Ação explícita + alvo único são resolvidos sem carregar E5, sem inventar confiança numérica. Erro de grafia inequívoco é hipótese que exige confirmação; nenhuma ação é enviada antes de “sim”. O HA revalida ação, alvo, catálogo e permissões. Empates genuínos continuam sem execução.

O score semântico passou de mínimo para média dos cossenos, evitando platôs que empatavam intenções distintas. Usa um lote de até três consultas e cache limitado de 128 textos; descritores e modelo continuam residentes. Calibração anterior pertence a outro algoritmo e não autoriza execução: `arandu-joint-v2` / `embedding-v2` exigem nova calibração rotulada para aceitação semântica automática >=80%. Resolução explícita e confirmação funcionam sem essa calibração. Não há limiar reduzido nem confiança fabricada.

Com pesos instalados, o serviço pré-carrega E5 antes de escutar HTTP e expõe `preload_ms`; importação/carga ainda têm custo no início do módulo. Resolução explícita não exige esse modelo. Veja medições e limites em `docs/INTELLIGENCE-v6.0.0.md` na raiz. Pesos não são incluídos no pacote.

# Arandu Decision

Pacote Python independente, versão 2.0.0. Não importa o NLU nem o outro pacote. Serviço passivo: recebe catálogo autorizado completo do Home Assistant e devolve proposta; credenciais, autorização e execução ficam na integração HA.

Modelo padrão: intfloat/multilingual-e5-small (MIT), CPU, vetores normalizados com prefixos query/passage. Downloads somente com comando explícito, revisão fixada no código. Pesos não acompanham o pacote.

```powershell
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/arandu-decision install
.venv/Scripts/arandu-decision serve
```

Para CPU, instale torch==2.9.1 pelo índice https://download.pytorch.org/whl/cpu antes do pacote. Não há calibração de fábrica: similaridade não é acurácia ou confiança. Sem calibração válida o serviço se abstém de aceitar ações.

Com confiança conjunta calibrada de pelo menos 80%, margem mínima 0,08 e alvo inequívoco, a integração executa a ação inferida sem exigir reconhecimento literal do verbo. O limite é inclusivo; configurações antigas abaixo de 0,80 usam 0,80 efetivo. Negação, ação literal conflitante, permissões e validação final continuam protegidas. O LLM mantém a confirmação de hipóteses incertas.

HTTP loopback :11557: GET /status e POST /infer, cabeçalho X-Arandu-Lab: 1, sem Origin. Inferência: JSON {action: "decision", text: "...", catalog: {areas: [...], entities: [...]}}. Campos de entidade: entity_id, name, domain, actions, aliases, area_id, attributes.available; áreas: area_id, names. Limites: 2 MiB, 8192 entidades, 2048 áreas, texto 512 caracteres. Catálogo excedente é recusado inteiro. Cada resposta inclui fingerprint e quantidade de entidades, além de tempos e uso de memória quando disponíveis.

Ações adicionais: load, unload, calibrate (train/validation disjuntos; regras do protocolo Arandu v1).

O AI Lab detecta o serviço pela porta. Pacote ausente, serviço parado ou pesos ausentes: componente indisponível, sem fallback escondido. Os demais modelos experimentais permanecem no Lab. Nenhum serviço consulta a base HA diretamente ou recebe token HA.

Contratos wire v1 extraídos do Lab, mantidos localmente para independência de instalação. Código Apache-2.0; licenças dos pesos preservadas. Testes de contrato em tests/; execute com `python -m unittest discover -s tests`. Benchmarks reais continuam nos botões do AI Lab. A alteração de execução automática foi validada com testes de contrato e integração simulada; não representa calibração do modelo instalado nem teste no Home Assistant real.
