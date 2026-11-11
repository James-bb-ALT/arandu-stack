# Arandu LLM 2.0.0

Resolvedor passivo de fallback PT-BR. Contrato v2 tipado de operações, parâmetros e evidências; conversa geral sem ferramentas. O HA recompõe o plano, valida permissões, capacidades e estado atual e continua único executor. Não interpreta parâmetros como serviços, URLs, código ou templates.

Tucano 2 Qwen 0.5B Q4_K_M padrão; Qwen3 0.6B Q8_0 experimental. Registro em `models.py` com revisões/tamanhos/SHA-256 imutáveis. Instalação explícita, staging, integridade e cache persistente. Nenhum download na inferência. Um processo CPU residente, troca serializada e rollback de carga, perfis de geração por modelo no app. Qwen3 recebe enable_thinking=false; Tucano não recebe esse parâmetro no v2. Templates vêm dos respectivos GGUF.

CLI: `arandu-llm install --model tucano` ou `--model qwen3`; `arandu-llm serve --model tucano --contract-version 2`. A API recebe contract_version explícito ou accept_contracts=[1,2]; clientes sem negociação conservam v1/Tucano. Contrato v1 continua selecionável no app. Porta passiva 11558/loopback e controles de origem preservados.

Guia, resultados reais e limitações: `docs/LLM-v2.0.0.md` na raiz. Testes são executados nesta etapa; não interpretar as notas históricas abaixo como status da entrega atual.

## Histórico até 1.0.1

## Atualização 1.0.1

Política semântica compartilhada: restrições operacionais recusadas antes da carga/inferência; compostos não viram proposta parcial. Nomes registrados, consultas e conversa são tratados pelo contexto. Modelos, embeddings e calibração preservados.

## Atualização 1.0.0

Pedidos claramente gerais (piadas, explicações, perguntas factuais) recebem geração de texto em PT-BR, sem catálogo doméstico, ferramentas ou gramática de ações. O resultado volta ao Assist como fala, com zero operações. O modelo é residente e reutilizado entre pedidos; truncamento e respostas inválidas são recusados.

Automação mantém JSON validado, candidatos autorizados, controle de ação/alvo e confirmação de hipóteses. O modelo não possui acesso à internet e não conhece estados em tempo real além do contexto fornecido. Respostas podem ser incorretas; o porte de 0,5B limita a qualidade. Veja `docs/INTELLIGENCE-v6.0.0.md` na raiz.

# Arandu LLM

Versão atual 1.0.1: último fallback com pistas compactas do NLU/Decision e fala original preservada. O modelo pode discordar das pistas; elas não concedem autorização. Rankings auxiliares limitados a duas hipóteses, sem diagnósticos do NLU. Ganho de acurácia/latência ainda não medido no hardware real.

Pacote Python independente. Não importa o NLU nem o outro pacote. Serviço passivo: recebe catálogo autorizado completo do Home Assistant e devolve proposta; credenciais, autorização e execução ficam na integração HA.

Modelo padrão: Polygl0t/Tucano2-qwen-0.5B-Instruct (Apache-2.0), quantização comunitária 2DTi Q4_K_M, llama.cpp CPU. Downloads somente com comando explícito, revisão fixada no código. Pesos não acompanham o pacote.

```powershell
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/arandu-llm install
$env:ARANDU_LLAMA_SERVER = "C:\caminho\llama-server.exe"
.venv/Scripts/arandu-llm serve
```

llama-server é um executável externo obrigatório. Sem Decision, a seleção lexical percorre todos os pares elegíveis do catálogo e envia até oito candidatos ao prompt. Quando o chamador fornece ranking do Decision, ele é usado. O modelo recebe os dispositivos recuperados; o serviço recebe e valida o catálogo inteiro. Não há truncamento dos primeiros dispositivos.

HTTP loopback :11558: GET /status e POST /infer, cabeçalho X-Arandu-Lab: 1, sem Origin. Inferência: JSON {action: "llm", text: "...", catalog: {areas: [...], entities: [...]}}. Campos de entidade: entity_id, name, domain, actions, aliases, area_id, attributes.available; áreas: area_id, names. Limites: 2 MiB, 8192 entidades, 2048 áreas, texto 512 caracteres. Catálogo excedente é recusado inteiro. Cada resposta inclui fingerprint e quantidade de entidades, além de tempos e uso de memória quando disponíveis.

Ação adicional: unload. O servidor llama.cpp é reutilizado até unload/stop ou falha de runtime. Erros de conteúdo mantêm o servidor carregado. `finish_reason=length` recusa toda a proposta; configure max_tokens 192 ou 256 para o contrato JSON e reinicie o módulo após salvar. Não há reparo de JSON, aumento automático do limite nem repetição automática de inferência.

O AI Lab detecta o serviço pela porta. Pacote ausente, serviço parado ou pesos ausentes: componente indisponível, sem fallback escondido. Os demais modelos experimentais permanecem no Lab. Nenhum serviço consulta a base HA diretamente ou recebe token HA.

Contratos wire v1 extraídos do Lab, mantidos localmente para independência de instalação. Tuning e expansão de ações ficam para próximas versões. Código Apache-2.0; licenças dos pesos preservadas. Testes de contrato em tests/; execute manualmente com `python -m unittest discover -s tests`. Benchmarks reais continuam nos botões do AI Lab. Testes não executados nesta alteração, por solicitação do usuário.
