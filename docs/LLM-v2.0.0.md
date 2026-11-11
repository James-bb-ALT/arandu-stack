# Fase 3 — LLM 2.0

App **7.0.0**, HACS **8.0.0**, serviço LLM **2.0.0**. Publicação experimental. NLU 0.6.3, Decision 2.0.0, STT 4.0.1, TTS 0.2.1 e dependências preservados. Não implementa a Fase 4.

## A. Diagnóstico e escopo

A baseline já tinha Tucano GGUF residente, conversa geral, recuperação lexical completa, JSON restrito, validação HA e confirmação. Seu contrato descrevia intenção/alvo, sem parâmetros tipados ou planos compostos. Não havia seleção de múltiplos GGUF no serviço integrado. A Fase 1 já fornecia isolamento de sessão, invalidação de contexto, autorização e recusa semântica; a Fase 2 já fornecia resolução determinística e Decision estruturado. Essas proteções continuam no fluxo.

O contrato novo separa automação, conversa e esclarecimento. A melhora medida é do **conjunto extração de evidência, contrato, decoder e validação**, sem treinamento ou calibração neural. A extração é fechada: construções e modificadores desconhecidos pedem esclarecimento. Não é um parser universal de português. Uma gramática não prova compreensão de fala humana.

## B. Implementação e uso

### Contrato e executor

Envelope v2: `version`, `type`, `operations`, `question`, `response_text`. Cada operação contém `action`, `entity_id`, `parameters`, `evidence` (índice da cláusula). Até quatro operações independentes, ordenadas; sem dependências, condicionais, alternativas ou exclusões. O modelo não escolhe URLs, serviços arbitrários, código ou credenciais. JSON truncado, incompleto, com campos extras ou valores não finitos é recusado.

`automation_contract.py` é distribuído em duas cópias idênticas: serviço LLM e integração HA; teste exige igualdade byte a byte. A fala original, catálogo autorizado completo e contexto são analisados novamente no HA. A proposta gerada deve corresponder integralmente às evidências. Quantidades/modos/cores comprovados restringem o decoder, sem reparo silencioso do JSON recebido. Nomes e aliases não autorizam restrições descartadas. Empates genuínos esclarecem. A lista diagnóstica de oito candidatos não limita a busca pelo catálogo completo (máximo 8192 entidades, 2048 áreas; requisição 2 MiB).

| Família existente | Operações aproveitadas |
| --- | --- |
| Energia/consulta | ligar, desligar, consultar; sensor de temperatura/umidade |
| Luz | brilho, cor registrada, temperatura de cor |
| Clima | temperatura, modo HVAC, modo de ventilação |
| Ventilador | percentual, oscilação, direção |
| Cobertura | abrir, fechar, posição |
| Mídia | play, pause, stop, próxima/anterior, volume |
| Cena/automação | ativar cena, habilitar/desabilitar automação |
| Aspirador | iniciar, pausar, voltar à base |

Cada ação ainda exige a capacidade real do adaptador HA, disponibilidade, exposição e permissão. Não acrescenta controle de fechadura/alarme nem um segundo executor. O HA revalida o plano inteiro antes de chamar qualquer serviço. Uma operação inválida impede as demais. Sensibilidade e confirmação existentes continuam valendo.

`value` é absoluto por padrão. `relative: 1/-1` usa **estado atual no HA**, nunca um estado estimado pelo modelo. Unidade explícita deve coincidir com a configurada; percentual não vira temperatura. Faixa, passo e frações não representáveis são rejeitados, sem clamp/arredondamento silencioso no contrato novo. “Um pouco” pede quantidade. Um “mais dois” exige alvo/ação recente inequívocos. A sessão existente separa usuário, origem e conversa, tem TTL e é invalidada por mudança de catálogo/origem; acrescenta apenas a última ação, sem armazenar todas as falas.

O fluxo permanece STT → NLU Rust → Decision → LLM → validação HA → executor existente. NLU/Decision resolvidos evitam LLM. Conversa geral recebe prompt de fala, sem catálogo/ferramentas e sem operações executáveis.

### Modelos, instalação e troca

| Chave | Repositório / quantização | Revisão imutável | Bytes |
| --- | --- | --- | ---: |
| `tucano` (padrão) | [2DTi/tucano2-0.5b-instruct-gguf](https://huggingface.co/2DTi/tucano2-0.5b-instruct-gguf), Q4_K_M | `cbb5318b66a49e20967623e946365bcc8594c29a` | 305603296 |
| `qwen3` (experimental) | [Qwen/Qwen3-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF), Q8_0 | `23749fefcc72300e3a2ad315e1317431b06b590a` | 639446688 |

Ambos Apache-2.0, baixados e testados de verdade em CPU. O repositório oficial Qwen3 consultado fornece Q8_0; não anunciamos Q4 inexistente nesse registro. Tucano SHA-256 `49f16e9d27db128a39a3d976271a297f1ef183b763c62ca9a0c0ba7c72c55c91`; Qwen3 `9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031`.

Qwen3.5 0.8B do antigo catálogo PC foi auditado em metadados (`ggml-org/Qwen3.5-0.8B-GGUF`, revisão `807dfd9a6fadbf703207aac3bfb0b2b3040d077c`, Q4_0); não foi inferido nem promovido a backend integrado. A interface informa essa indisponibilidade, sem afirmar incompatibilidade não demonstrada.

Na dashboard do app, abra configurações LLM, instale/verifique individualmente, selecione modelo/perfil e salve/aplique com confirmação. No AI Lab, escolha o modelo e contrato do teste; aplicar ao Assist é outra ação confirmada. Status distingue configurado, instalado, íntegro e efetivamente carregado.

Instalação é explícita: revisão HTTPS pinada, limite de tamanho/tempo/espaço, staging, magic GGUF, tamanho e hash completos, fsync e promoção atômica. Nunca baixa na inferência. Pesos ficam no cache persistente externo à imagem/Git. Instalação ocupa a trava do LLM, mas não a fila compartilhada de inferência do Decision. Inferência offline reutiliza um processo CPU residente; troca descarrega o anterior e verifica saúde/identidade. Falha tenta restaurar modelo/configuração anteriores. Uma solicitação por vez, excesso retorna ocupado, sem fila ilimitada.

O runtime llama.cpp mantém revisão `f1a7dea99aae1623bad10c4ce31fe220033ab7f6`, template GGUF próprio, CPU sem offload, no máximo quatro threads, contexto máximo 8192 e geração máxima 512 tokens. O perfil novo usa 384 tokens; configurações salvas antigas são preservadas. Qwen3 usa `enable_thinking:false`, Tucano não recebe opção não suportada. Prefix cache é solicitado desativado; só se reporta cache efetivo quando há uso informado pelo servidor. O decoder usa `prefixItems` sem `items`, pois o conversor dessa revisão prioriza `items`.

### Migração, compatibilidade e rollback

`modules.json` v2 continua legível pelo app anterior. Seleção/contrato/perfis por modelo ficam em `llm-policy.json` v1, separado. Arquivo inválido preserva bytes e sinaliza falha, recuperando Tucano/contrato legado até um salvamento explícito. Não muda pesos/dependências de outros módulos. `decision-policy.json` permanece independente.

Clientes novos anunciam `accept_contracts:[1,2]`; antigos sem negociação recebem v1/Tucano. Qwen3 exige v2. O laboratório pode testar explicitamente v1 sem trocar a produção. HACS antigo não recebe plano novo como se fosse proposta antiga. A API só persiste a escolha depois que o serviço confirma modelo carregado e integridade. Modelo anterior continua disponível para implantação/rollback.

### AI Lab e privacidade

Painel nativo HA usa WebSocket autenticado/admin, `stack_endpoint` configurado e transporte interno protegido. Não usa porta 8765. Mostra proposta, candidatos, evidências, JSON recebido/validado, erros, carga/inferência, TTFT, tokens, tokens/s e RSS quando medidos. Comparação LLM sequencial (até dois modelos, vinte exemplos, 180 s) usa os mesmos dados, restaura o residente anterior e não oferece token nem executa ações. Teste individual é dry-run; execução explícita mantém token único, prazo e revalidações existentes.

JSON/CSV novos exportam apenas métricas, tipo, modelo e índice do caso; não falas, catálogo, nomes residenciais, IDs, JSON bruto ou credenciais. Resultado LLM v2 não entra automaticamente no histórico local. Salvar exemplos explicitamente continua sendo escolha do administrador. Texto de modelo é exibido como texto, não HTML. Sem persistência indiscriminada de conversas.

## C–E. Verificação e benchmark

Resultados finais, metodologia e limites estão em [LLM-v2.0.0-validation.md](LLM-v2.0.0-validation.md) e no [artefato de métricas](evidence/llm-v2.0.0.json). O script reproduzível é `tests/llm_v2_check.py`; controle real em `tests/llm_gateway_check.py`. Testes de contrato/executor em `tests/test_llm_v2.py`; frontend em `tests/test_ai_lab_panel.js`.

A baseline é o comportamento legado congelado na conclusão da Fase 2 (LLM 1.0.1, evolução de 1.0.0), com somente instrumentação adicional. Não apresentamos 1.0.1 como binário original 1.0.0. Todos os modelos usam o mesmo catálogo, frases, dois threads, contexto 4096 e teto 384 na comparação. É teste isolado de LLM, complementado por integração da cascata real Rust/serviços e HA simulado. As recusas antes da geração aparecem separadas das inferências.

## F–G. Publicação e limites

Release [v8.0.0](https://github.com/James-bb-ALT/arandu-stack/releases/tag/v8.0.0), experimental, com pacote HACS. Critérios estatísticos/validação física não foram inventados: não foi fornecido corpus representativo de 300 falas, hardware i5-6500T ou acesso autenticado a HA físico. Fixtures técnicas aprovadas não são gold representativo e não calibram confiança. A validação real disponível usa Ryzen 7 5700X, Docker Linux e dois modelos CPU; HA/executor são exercitados localmente com serviços simulados e transporte/modelos reais. O controle físico permanece pendente. Qwen3 permanece experimental, Tucano padrão. Nenhuma meta de latência em i5 foi comprovada.

Riscos residuais: cobertura linguística fechada pode esclarecer pedidos válidos; concorrência devolve ocupado; contexto longo ainda custa prefill; runtime pode falhar e rollback também pode falhar (status/log registra, não anuncia carga falsa). A comparação não demonstra superioridade geral entre famílias/modelos. Regressões históricas por corpus congelado ausente permanecem descritas na documentação da Fase 1; não são reclassificadas como testes aprovados.
