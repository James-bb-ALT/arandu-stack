# Avaliação PT-BR independente

Esta suíte mede conformidade interna do interpretador PT-BR pela fronteira HTTP pública. Não mede acurácia linguística geral.

## Limites

- Somente `UNDERSTANDING` é executado: texto mais catálogo sintético produz resposta v1.
- `EXECUTION` fica fora da suíte. O runner não chama, emula ou autoriza services Home Assistant.
- `RESPONSE GENERATION` fica fora do runner semântico.
- LLM não participa do hot path, não cria dados e não anota resultados.
- Nenhum material, output ou comportamento de Sophia, Aquila, outro motor, diretório irmão ou implementação anterior influencia corpus, labels ou código.
- Casos são `PROJECT_AUTHORED_SYNTHETIC`, licenciados Apache-2.0. Evidência externa só poderia entrar após admissão explícita como `ADMITTED_AUTONOMOUS`.

## Classes de resultado

- `exact_pass`: resposta validada equivale ao gold.
- `semantic_mismatch`: resposta válida difere do gold.
- `unexpected_rejection`: gold exige plano, produto rejeitou.
- `unsafe_acceptance`: gold exige rejeição, produto retornou plano.
- `protocol_error`: resposta viola contrato v1 ou limites semânticos.

Ordem de operações é semântica. Ordem de targets dentro de operação não é semântica depois da validação. `ambiguous`, `no_match` e `invalid_request` permanecem distintos.

## Dados e freeze

`data/catalog-v1.json` e `data/dataset-v1.jsonl` são artefatos versionados. `data/catalog-v2.json` e `data/dataset-v2.jsonl` são evolução separada, com identidade `ptbr-independent-v2`. Antes do primeiro request, execute o freeze correspondente (`freeze.py` para v1, `freeze_v2.py` para v2); o manifesto guarda hashes, contagens, autoria e digest de `addon/engine/src/`. Nenhum freeze sobrescreve versão existente.

`known-gap-future` fica visível nos relatórios, mas fora do denominador atual. Não ajuste labels ou buckets com base no output do produto. O dataset v2 tem 144 casos: 120 pontuados e 24 known gaps.

## Execução

Use os comandos `evaluation-check`, `evaluation-freeze`, `evaluation-run`, `evaluation-benchmark` e `evaluation-record` em `tools/mlp-dev.ps1`. O workflow usa `freeze_v2.py`, `run_v2.py` e `benchmark_v2.py`. Execute `evaluation-check` antes de qualquer avaliação. Revise `target/ptbr-independent/run-v2.json` e `benchmark-v2.json` antes de promover baseline. `evaluation-record` promove v2 para `baselines/v2` e recusa destino existente; artefatos v1 permanecem separados. O modo `--endpoint` serve somente diagnóstico; comandos de workflow exigem `--binary`. O adapter aceita somente endpoint HTTP local ou binário local iniciado pelo runner. Não recebe credenciais, não segue redirects e não usa rede externa.

## Resultado v2 revisado

- Run: 144/144 `exact_pass`; 120/120 casos pontuados; 24 known gaps.
- Benchmark: 1008 medições; zero falhas steady-state e cold start.
- Throughput: aproximadamente 1723 requests/s; p95 de 703323 ns.
- RSS não coletado pelo runner stdlib-only.
- Ausência de divergências não representa acurácia linguística externa.
