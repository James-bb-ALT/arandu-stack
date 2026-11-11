# Fase 1 — segurança semântica e NLU PT-BR

Versões: app 5.0.1, HACS 6.0.1, NLU 0.6.3, Decision/LLM 1.0.1.
Base de implementação: `720b189`, preservando a evolução posterior à auditoria
`fd7d137`. STT 4.0.1, TTS, modelos, embeddings, calibração, thresholds e protocolos
de áudio não foram alterados.

## Fluxo e limites de confiança

Voz/Wyoming → texto original → preparação da integração → identidade/origem/sessão
→ catálogo autorizado → Rust `/v4/interpret` → plano ou recusa/esclarecimento.
Somente respostas semanticamente elegíveis chegam a Decision → LLM. O executor HA
é o único local que produz efeitos, após pré-validar o plano completo e revalidar
política, usuário, origem, catálogo, exposição, capacidade e disponibilidade.
Os modelos permanecem passivos. Resultado já resolvido pelo NLU evita os modelos.

A política `semantic_policy.json` é distribuída com o Rust, HACS e os dois pacotes
neurais. `semantic_context.py` tem cópias idênticas verificadas por teste. Não há
dependência nova. O app continua utilizando o motor em
`arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/addon/engine`; a integração
principal é `custom_components/local_nlu`. O snapshot de integração dentro do
módulo NLU mantém seu runtime legado, recebendo os mesmos bloqueios de execução e
invalidação de referências. Projetos irmãos não foram usados nem alterados.

## Gaps reproduzidos antes da correção

No Rust real, os exemplos negativos auditados **não produziram operações**.
Entretanto, `nem…` e `sem desligar…` retornavam `no_match`, elegível para fallback.
No Python, `hypothesis_allowed()` liberava `jamais`, `nem` e `sem`.
Um teste com plano positivo forjado demonstrou que o executor não conferia a
restrição do texto de maneira independente. Isso demonstra falta de defesa
adicional; não demonstra que o Rust original gerasse aquele plano forjado.

As revisões também reproduziram: perda da segunda ação no Decision em compostos
sem `e`; execução imediata de pedidos com horário/adiamento; confusão entre uma
pergunta negativa e uma operação positiva forjada; restrições de conversa como
“piada sem palavrões”; interação entre nomes como “Sem Fio” e prefixos naturais.
Esses contraexemplos passaram a integrar as regressões.

## Comportamento antes/depois

| Caso aprovado / fixture técnica | Antes | Agora |
|---|---|---|
| Não/Jamais ligue… | Rust abstinha; Python não cobria todas as formas | Recusa rastreável em todas as fronteiras |
| Nem pense em abrir… / Sem desligar… | Rust `no_match`; fallback possível | Recusa do plano inteiro; sem fallback |
| Desliga tudo, menos a TV | Sem operação, motivo genérico de alvo | `semantic_exclusion`; lote inteiro recusado |
| Se estiver quente… / após eu chegar / às 18 horas / daqui 10 minutos | Cobertura parcial; proposta neural imediata em alguns casos | `semantic_condition`; nenhum agendamento implícito |
| Pode ligar… / acende… / percentuais / lotes por área | Já suportados | Preservados |
| Liga a sala / todos os dispositivos da sala | Já suportados | Preservados, sem ampliar para dispositivos críticos |
| Queria … acesa / Dá pra desligar… | `no_match` na fixture real | Canonicalização determinística |
| Liga TV e depois apaga a luz | Separador duplicava `e` | Duas operações ordenadas quando alvos/capacidades/origem permitem |
| Agora diminui ela | Faltava domínio para ação relativa | Domínio do único alvo recente atual, sem adivinhar outro alvo |
| Quais luzes não estão ligadas? | Negação bloqueada indiscriminadamente | Pode seguir como leitura; plano positivo forjado é recusado |
| Liga Sem Fio / Dá pra ligar Sem Fio? | Bloqueio por palavra no nome | Nome exato contextual; não mascara prefixo negativo operacional |
| Conte uma piada sem palavrões | Conversa geral existente | Preservada; essa exceção nunca autoriza operação doméstica |

Nomes registrados só são mascarados em posição de alvo com prefixo conhecido e
sem verbo de efeito dentro do nome. Não há remoção indiscriminada de palavras do
comando. Perguntas somente de leitura e pedidos exclusivamente de fala têm seu
tipo conferido novamente contra as operações propostas. Negação em uma cláusula
nunca autoriza executar as outras cláusulas positivas.

Motivos estáveis, aditivos: `semantic_negation`, `semantic_exclusion`,
`semantic_condition`, `semantic_compound`, `semantic_non_effect_request`.
Negação preserva o status Rust `cancel`; restrições não suportadas usam
`unavailable`. Nenhum desses motivos pertence à allowlist de fallback. O texto
original permanece no pedido; normalização e canonicalização não o substituem.

## Contexto, autorização e confirmação

Referências recentes são descartadas ao mudar a geração do catálogo ou a origem
(área e proveniência), nos dois runtimes distribuídos. Isolamento por usuário,
origem e conversation_id, TTL, confirmação de uso único, revogação de permissão,
mudança de política durante inferência, cancelamento e proteção contra repetição
continuam passando pelas verificações existentes. Uma confirmação não transfere
autorização entre sessões nem permite completar uma inferência cancelada.

Área explícita prevalece sobre origem; falta/empate de alvo não escolhe outro
cômodo. Lotes mantêm exclusões, capacidades e `bulk_eligible`. O executor faz
pré-validação completa; efeitos já enviados ao HA não são revertidos por cancelamento.
Não foi criado executor Rust, ferramenta HA para LLM ou memória persistente.

## Validação e reprodução

Resultados efetivamente executados e tempos estão em
`NLU-PHASE1-VALIDATION-2026-11-11.json`. As frases do requisito são entradas
aprovadas pelo usuário; nomes adicionais são `FIXTURE_TECNICA`. Não são corpus
independente nem evidência de acurácia em fala humana.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
# Serviço Rust real isolado; nunca apontar este teste para um HA residencial.
$env:ARANDU_NLU_ENDPOINT='http://127.0.0.1:11569'
.\.venv\Scripts\python.exe -m pytest tests/test_phase1_e2e.py tests/test_semantic_safety.py -q
node --test tests/test_ai_lab_panel.js
```

`tests/phase1_compare.py` compara dois serviços Rust reais usando catálogo técnico
fornecido em JSON, sem ação HA. Mede p50/p95 de normalização, parsing, resolução e
total reportados pelo motor. Não mede áudio, modelos neurais, rede Assist ou
hardware i5-6500T; poucos exemplos não permitem inferir desempenho produtivo.

O gate histórico `tools/mlp-dev.ps1 -Task check` / `tools/dev/run.py check` foi
tentado no ambiente Docker disponível. O snapshot do antigo repositório NLU tem
artefatos de distribuição ausentes/divergentes. A suíte completa Rust também
encontra quatro falhas por ausência de `data/mlp/project-authored-synthetic-v1.jsonl`.
Não foi recriado gold a partir da saída do produto nem declarado gate completo
aprovado. As suítes disponíveis e o app integrado são verificados separadamente.

## Limitações e atualização

Instale app e HACS juntos e reinicie a integração pelo procedimento normal do HA.
Sem alteração de schema/configuração persistente; seleções, IR, bindings e
preferências existentes são preservados. Rollback de código usa a release anterior;
não há migração destrutiva a desfazer.

O NLU foi alinhado em 0.6.3 porque sua cópia HACS já estava em 0.6.2 enquanto o
motor estava em 0.6.0; isso evita downgrade da cópia distribuída. App/HACS e
Decision/LLM recebem incremento Minor na classificação do projeto (`x.y.Z`).

A política é conservadora e não é um parser universal de português. Restrições
operacionais não suportadas abstêm o plano inteiro. Nomes/títulos com construções
restritivas não reconhecidas podem exigir reformulação. Elipses sem ação explícita
como “agora a da sala” não ganham ação presumida; não se passou a herdar qualquer
ação/parâmetro histórico. Condições/agendamentos domésticos arbitrários continuam
fora deste fluxo. Device crítico continua exigindo sua autorização/confirmação.

Validação física no Home Assistant requer sessão administrativa autenticada;
HTTP 401 não é teste aprovado. Testes de HA simulado não comprovam operação em HAOS,
satélite ou residência real. Decision 2.0 e LLM 2.0, novos modelos, novos algoritmos
de embeddings e mudanças de calibração permanecem fora desta entrega.

### Resultado final

224 testes Python passaram (176 subtestes; 3 STT opt-in ignorados), 88 testes
Rust disponíveis passaram, 10 contratos dos serviços e 8 testes JavaScript passaram.
`cargo fmt --check` e Clippy de todos os alvos com warnings negados passaram.
Build Docker e API da imagem final 5.0.1 passaram sem sobreposição de fontes,
incluindo E5/Tucano reais. Foram verificados 17 pedidos restritos no Rust real e
planos adversariais no executor simulado: zero efeitos indevidos nesses cenários.

O microbenchmark técnico de quatro entidades, 30 repetições por frase, mediu
“liga a sala” em p50 0,0735 ms / p95 0,1226 ms no motor Rust; não inclui rede/HA/áudio.
As demais medições antes/depois estão no JSON. Nenhuma acurácia linguística foi calculada.

A execução ampla dos testes históricos Python resultou em 156 aprovados, 22 falhas
e 68 ignorados: 19 testes de distribuição/HACS precisam do executável Ruby no
host Windows; três expectativas de diagnóstico de identidade também falham na
cópia arquivada da baseline (acesso continua negado). No gate Linux, a distribuição
antiga rejeita `stack.py`, arquivo já existente; seu checker também identifica
a falta de `brand/icon.png`. O empacotamento da integração principal foi verificado
separadamente, incluindo manifesto, sintaxe, política e ausência de pesos.

As duas revisões independentes iniciais produziram contraexemplos corrigidos.
A nova revisão independente do diff final foi tentada, mas o serviço de agentes
atingiu limite de uso; a revisão final foi feita pelo agente principal e pelas
regressões dos contraexemplos. Não se declara aprovação independente final.
