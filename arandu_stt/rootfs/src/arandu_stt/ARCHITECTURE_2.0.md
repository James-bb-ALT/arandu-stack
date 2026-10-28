# Arquitetura e auditoria do Arandu STT 2.0

## Arquitetura preservada

O repositório Git ativo é `arandu-stt-github`, com base em `ecdc7ae` (0.1.1).
A pasta externa contém uma cópia anterior; não foi sincronizada nem usada como destino das mudanças.

- `launcher.py` lê `/data/options.json`, garante o modelo, sincroniza o inventário e executa `server.py`.
- `model_download.py` mantém o download/cache do checkpoint convertido, em `/data/models/int8`. Nenhum peso é distribuído.
- `Engine` mantém `OfflineRecognizer.from_nemo_ctc`, `model.int8.onnx`, `tokens.txt`, CPU, 16 kHz, 80 features, greedy search e exclusão mútua de inferências.
- `Handler` mantém PCM Wyoming, conversão para mono PCM16, limite de 35 segundos, padding opcional e resposta `Transcript` em `pt-BR`.
- `wyoming_discovery.py` mantém a publicação da descoberta no Supervisor. O App continua usando a integração oficial Wyoming.

Não existe neste repositório uma custom integration que registre diretamente `stt.arandu_stt`.
O Home Assistant cria/configura a entidade STT pela integração Wyoming; seu ID depende do registro do HA.
O nome do programa e do modelo não mudou. A versão anunciada passou a 2.0.0.

O App permanece `amd64`, Python 3.12 em Debian slim. Dependências de runtime não mudaram.
Não há requisito de GPU, LLM, API de correção remota ou modelo adicional.
O processo ainda precisa de conexão inicial para baixar os pesos quando o cache está vazio.

## Auditoria dos materiais recebidos

| Origem | Registros | Frases normalizadas repetidas ignoradas |
|---|---:|---:|
| Léxico recebido | 2.200 | 84 |
| Household recebido | 1.551 | 43 |
| Comandos v2 recebidos | 1.946 | 1.583 |
| Nomes recebidos | 569 | 396 |

Os três CSVs recebidos não apresentam linhas completamente idênticas. Existem colisões de caixa/acentos e repetição de templates.
As contagens da última coluna são de campos/frases, não de linhas CSV duplicadas.
Os templates removem placeholders antes da deduplicação. Assim, exemplos diferentes não repetem artificialmente o mesmo template.
Cada frase contribui uma vez por origem. Termos repetidos em entidades/aliases HA também não multiplicam frequência.
As frequências são contagens internas do corpus, não frequências estatísticas da língua brasileira.

O arquivo `arandu_nomes_comuns_brasil.csv` foi recebido após o primeiro pacote e substitui integralmente o fallback anterior.
São 569 registros, 529 nomes canônicos distintos após normalizar acentos/caixa e 496 chaves de tokens incluindo variantes e apelidos.
Não há linhas idênticas nem nomes canônicos vazios. Há 144 registros com variantes gráficas, 51 com variantes de componentes e 133 com apelidos.
As 40 colisões de nomes canônicos e 396 repetições de frases/campos não inflacionam frequência. Suas grafias válidas são preservadas no vocabulário de superfícies.
`datasets/manifest.json` registra origem e SHA-256 dos quatro CSVs. Para importar futuras revisões, coloque os arquivos em `New artifacts` e execute `python tools/package_datasets.py` a partir do repositório.
O runtime usa somente os quatro caminhos canônicos; nunca carrega versões antigas por glob.

O léxico fornecido é especializado, com cerca de 2,3 mil chaves após união, não um dicionário amplo de português.
`functional_ptbr.txt` mantém artigos, pronomes, contrações, auxiliares e negação sem prioridade por fonte/frequência.
`supplemental_ptbr.txt` explicita cobertura informal, unidades e algumas flexões ausentes, como `quartos`.
Essa extensão é pequena e auditável; não tenta gerar flexões arbitrárias ou prometer cobertura de ditado geral.

## Auditoria do corretor inicial

O script recebido serviu como referência. Não foi carregado no pipeline de produção.
Problemas identificados: duas buscas completas RapidFuzz por token; outra varredura dos termos HA; templates consultados em cada beam;
frequência HA inflada pelo peso 10; ausência de política completa para números; perda de pontuação ao juntar tokens;
estimativas de confiança dependentes do shortlist; possível substituição de palavras já válidas por priors contextuais.
A função fonética também removia acentos antes de algumas regras e não resolvia adequadamente todos os sons de `c`.

O corretor legado possui regras úteis em `resources/asr_confusions.json` e métodos de resgate de comandos em `lexical.py`.
Algumas removem repetições ou trocam palavras válidas a partir de inventário. Essas regras continuam disponíveis no perfil `legacy_full`,
mas não rodam antes do novo modo fechado: isso impediria avaliar e preservar a transcrição original.
As confusões de palavra alimentam apenas a geração de candidatos permitidos, sem autorizar termos externos.

## Fluxo novo

`FastConformer INT8 → texto bruto → ContextualResolver → normalização/spans → índices → ranking → beam search → validação final → Wyoming`.

O pacote `contextual_lexical` é independente do reconhecedor acústico:

1. `normalizer.py`: normalização Unicode e spans; mantém pontuação. Chaves ignoram caixa/acentos, mas a superfície válida é preservada e acentos ausentes podem ser restaurados.
2. `context.py`: carrega CSVs estritos, vocabulário funcional, complementos e nomes relevantes do inventário. Constrói bigramas/trigramas deduplicados e associa nomes a pistas de entidade.
3. `candidates.py`: índices de bigramas de caracteres, buckets fonéticos e prefixos. Postings são limitadas durante a construção. Consulta e shortlist são limitados; não existe varredura do vocabulário por palavra.
4. `scoring.py` e `semantic.py`: RapidFuzz, Levenshtein, fonética PT-BR, frequência normalizada, prior de origem, wordlist opcional e compatibilidade doméstica explícita.
5. `decoder.py`: beam search com palavras válidas ancoradas, contexto de prefixo, palavra seguinte e metadados. Pontuação de uma frase não atravessa limites de sentença.

Palavras já válidas têm um único candidato. Isso protege negação, nomes e flexões, mas impede corrigir erros do ASR que já sejam outra palavra válida.
Por exemplo, `destranca a poda` permanece assim, mesmo que a referência humana seja `destranca a porta`.
A confiança de preservação se refere somente à decisão lexical, não à correção acústica dessa palavra.

Termos compostos registrados são suportados. Formas separadas de `ar-condicionado`, `Wi-Fi`, `micro-ondas` e `lava-louças` podem ser normalizadas.
Outras frases já válidas, como `lava e seca`, mantêm seus tokens. Nomes compostos HA mantêm a grafia local, sem expandir aliases para outros nomes.

## Ranking e confiança

Os coeficientes estão em `config/lexical.json`. São heurísticos e não estatisticamente validados.
O prior de origem usa `peso / (peso + 10)` e só contribui com similaridade textual/fonética plausível.
A contribuição máxima de fonte é limitada independentemente do peso configurado. Fonte 100 não vence uma alternativa distante por inventário.
Tokens funcionais têm prior/frequência preferencial zero; contexto gramatical ainda pode ajudar.
Bigramas/trigramas usam contagens relativas ao mesmo prefixo e contribuições limitadas. Não são probabilidades de linguagem.

Cada token retorna original, corrigido, alteração, obrigatoriedade, score, margem local, margem de evidência,
margem entre sequências sobreviventes, alternativas, origem e confiança lexical heurística.
Um candidato favorecido pelo contexto não ganha confiança alta se sua evidência de grafia continuar ambígua.
Margens de sequência são aproximadas: beam pruning pode eliminar alternativas. `sequence_score: null` indica alternativa fora dos beams finais.

A frase informa média geométrica, confiança mínima, flag de baixa confiança e timings por etapa.
`confidence_is_calibrated_probability` permanece `false`. Nunca interprete 0,98 como 98% de acerto do ASR.
O utilitário `benchmarks/calibrate.py` ajusta regressão isotônica com decisões rotuladas e calcula Brier em arquivo de validação separado.
O artefato não é ativado automaticamente. Sua validade depende do corpus e precisa de revisão antes de ser usado pelo executor.
Exemplo: `python arandu_stt/benchmarks/calibrate.py --train treino.jsonl --validation validacao.jsonl --output calibracao.json`.

## Política de saída e erros

O perfil `full` usa vocabulário fechado por padrão. OOV sempre recebe o melhor candidato disponível, mesmo com confiança baixa.
A validação final usa as mesmas regras de tokenização. Nenhum dicionário auxiliar amplia a saída permitida.
Wordlist opcional aceita texto ou `.dic` com flags removidas; não aplica affixes Hunspell nem requer libhunspell.

Regras dinâmicas permitidas: números com sinal, até 12 dígitos inteiros e 6 decimais; vírgula/ponto decimal; percentual;
temperaturas com `°`, `°C` ou `°F`; horários `H:MM`/`HH:MM` no intervalo 00:00–23:59.
Unidades verbais/simbólicas como `kg`, `kWh`, `graus` e `minutos` são entradas explícitas do léxico.
Pontuação comum é mantida. Símbolos não suportados, placeholders e horários inválidos geram erro, sem remoção silenciosa.
O limite padrão é de 256 spans antes de normalizar compostos.

Recursos obrigatórios ausentes, configuração inválida ou saída proibida geram `LexicalError` com código estável.
Falhas durante requisições geram evento Wyoming `Error`, sem `Transcript` de sucesso.
Ausência/corrupção de inventário HA no startup usa vocabulário genérico. Falha de atualização mantém o snapshot anterior.

## Home Assistant e disponibilidade

`inventory_sync.py` continua usando o proxy WebSocket Supervisor e `SUPERVISOR_TOKEN`.
Inclui áreas, andares e aliases, nomes amigáveis/originais, aliases Assist disponíveis,
pessoas, cenas, scripts e automações presentes somente em states. Entidades ocultas/desabilitadas e categorias técnicas
não alimentam o novo vocabulário. A lista de áreas/andares não depende de exposição de dispositivos.
A extração de `assist_aliases` é oportunista: somente campos efetivamente fornecidos pelo HA são usados.

`home_assistant_context.py` consulta o inventário periodicamente (60 segundos por padrão), fora da inferência.
Uma alteração reconstrói o snapshot em thread. A referência é publicada somente após índices, decoder e resolver legado estarem completos.
Cada transcrição captura uma única geração. Inventários semanticamente idênticos não provocam reconstruções repetidas.
A escrita do JSON é atômica. Não existe consulta HA por palavra nem qualquer chamada de serviço/ação no corretor.
Somente o JSON do inventário é persistido; índices são reconstruídos no início. O custo local medido não justificou um cache de índices com versionamento adicional.
Caches de normalização/fonética/candidatos são limitados em memória.

Por padrão os logs contêm contagens, scores e timings agregados, sem texto. `debug_text` habilita texto e detalhes das correções.
Falhas de conexão em logs de inventário mostram apenas tipo de exceção, sem token, URL autenticada ou payload do servidor.
O inventário persistente contém nomes da casa; já era local em `/data` e permanece fora do controle de versão.

O Wyoming transporta metadados em `Transcript.context.arandu`. O HA Assist padrão pode ignorá-los.
A implementação não cria uma barreira de confirmação no executor. Consumidores que precisem confirmar ações devem verificar
`low_confidence`, `minimum_token_confidence`, texto e sua própria política de autorização.
Reconhecimento, correção, intenção e execução permanecem separados.

Referências verificadas em 2026-10-06: [WebSocket HA](https://developers.home-assistant.io/docs/api/websocket/),
[áreas](https://developers.home-assistant.io/docs/area_registry_index/),
[andares no Core](https://github.com/home-assistant/core/blob/dev/homeassistant/components/config/floor_registry.py),
[Transcript Wyoming](https://github.com/rhasspy/wyoming/blob/master/wyoming/asr.py),
[Error Wyoming](https://github.com/rhasspy/wyoming/blob/master/wyoming/error.py).
