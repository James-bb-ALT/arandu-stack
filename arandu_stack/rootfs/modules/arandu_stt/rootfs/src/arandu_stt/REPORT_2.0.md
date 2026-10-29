# Relatório técnico — Arandu STT 2.0

Data: 2026-10-06. Destino: repositório Git `arandu-stt-github`, base `ecdc7ae`.
Status: implementação integrada, build Docker, testes e runtime INT8 verificados; pacote de código preparado. Aceitação em produção ainda depende de fala real, HAOS.

## Entrega

O perfil `full` do servidor passa automaticamente a transcrição pelo novo motor contextual.
FastConformer INT8, identificadores de programa/modelo, porta Wyoming, discovery Supervisor e método de implantação foram preservados.
Os perfis legados permanecem disponíveis. Não houve treinamento, fine-tuning, ação HA ou inclusão de dependência pesada.

| Grupo | Arquivos adicionados/alterados | Resultado |
|---|---|---|
| Pipeline | `server.py`, `launcher.py`, `lexical.py` | Integração, métricas/metadados, erro estruturado e rollback |
| Motor | `contextual_lexical/{__init__,normalizer,phonetic,candidates,scoring,semantic,context,decoder,config,errors}.py` | Vocabulário fechado, índices, n-gramas, beam search e snapshots |
| Contexto HA | `inventory_sync.py`, `home_assistant_context.py` | Andares, aliases, states-only automações, refresh periódico e logs sem payloads autenticados |
| Dados/configuração | `datasets/{household,names,commands,lexicon}`, `manifest.json`, arquivos funcional/suplementar, `config/lexical.json` | Quatro CSVs originais, incluindo 569 registros de nomes, deduplicação e pesos 7/10 |
| Verificação/distribuição | Novos testes, `benchmarks/*`, `tools/*`, Dockerfile/config.yaml, workflow, READMEs, arquitetura, upgrade e changelog | Avaliação reproduzível e mesmo App HA versão 2.0.0 |

`model_download.py`, `run.sh`, `wyoming_discovery.py`, `requirements.txt` e o método de inferência de `Engine` permaneceram sem alterações funcionais.
A cópia externa `../arandu_stt` não foi modificada. As mudanças estão no repositório distribuível mais recente.

## Atualização do dataset de nomes

O CSV fornecido substituiu o fallback: 569 registros, 496 tokens de nomes/variantes/apelidos e SHA-256 registrado no manifesto.
A auditoria está em `benchmarks/names_audit.json`. A deduplicação agora preserva grafias válidas sem repetir contagens.
Os dois testes novos cobrem todas as formas preenchidas no CSV e a frequência de variantes equivalentes.
Benchmarks textuais foram reexecutados; WER permaneceu igual, com zero regressões nos 400 casos limpos sintéticos.

## Testes executados

Antes da implementação: **10 testes passaram**.
Após implementação: **46 testes passaram**, com `python -m unittest discover -s arandu_stt/tests -q`.
Também passaram compilação de fontes, validação de metadata do App e conferência dos quatro hashes de datasets.
O ZIP foi extraído em diretório temporário: os mesmos 44 testes da primeira entrega, hashes dos CSVs, integridade e script shell com LF foram verificados.
O build `docker build --platform linux/amd64 --tag arandu-stt:2.0.0-verify .` passou; os 46 testes passaram novamente dentro dessa imagem, em Python 3.12.15.

Cobertura: vocabulário fechado com 120 OOVs determinísticos; preservação de palavras/negação/nomes/flexões;
exemplos fornecidos e fala informal; números/horários/unidades; termos compostos; geração indexada sem iteração do vocabulário;
priors HA limitados e desempate plausível; dependência da sequência futura; erros de recursos/configuração;
fallback HA; atualização concorrente e falha de rebuild; aliases/andares/automações;
metadados/erros por TCP Wyoming real; contrato do carregador INT8 com double; ausência de textos/segredos nos logs padrão;
contagens WER/CER e calibração isotônica.

Os testes unitários do carregador usam double; os testes de integração da suíte usam TCP real com saída acústica simulada.
Adicionalmente, o modelo INT8 original foi baixado pelo downloader existente, carregado e usado com um segundo de silêncio em três inferências.
O servidor real, com esse modelo e o corretor ativo, respondeu a `Describe`/`Transcribe`/PCM/`AudioStop` por Wyoming TCP.
O resultado vazio esperado e `all_tokens_allowed: true` foram confirmados. Isso verifica o runtime, sem avaliar precisão de fala.
A CI foi atualizada para Python 3.12 e versão 2.0.0, mas não foi executada remotamente nesta sessão.

O smoke real em Linux/WSL2, threads=2, mediu carga do modelo em 1,660 s e inferências em 32,04/31,27/27,49 ms.
Como o áudio era silêncio de um segundo, esses tempos não representam comandos reais nem comprovam a meta de 0,6 s.
Relatórios adicionais: `benchmarks/acoustic_smoke_results.json`, `wyoming_smoke_results.json`, `runtime_metrics.jsonl` e `model_manifest.json`.

## Ambiente de medição

Windows, AMD Ryzen 7 5700X (8 núcleos/16 threads), Python 3.10.6.
As latências abaixo medem exclusivamente o pós-processamento textual, sem PCM, modelo, rede, fila ou executor HA.
Métricas ponderam o número de palavras/caracteres de referência; normalização ignora caixa e espaços repetidos, mantendo acentos/pontuação.
JSONs e CSVs completos estão em `benchmarks/`.

## Avaliação sintética ampla

`generated_cases.jsonl` contém 400 exemplos distintos amostrados por domínio, com seed 20261006,
mais suas 400 versões limpas. As corrupções usam remoção, troca de letras adjacentes e substituição por vogal.
São exemplos do próprio corpus que alimenta o contexto. Portanto, o resultado é um teste de consistência e estresse,
com sobreposição entre construção e avaliação; **não é WER acústico real nem validação independente de generalização**.
Os coeficientes/n-gramas foram ajustados durante o desenvolvimento; esse corpus também não serve como conjunto final intocado.

| Métrica | Original | Corrigido |
|---|---:|---:|
| WER | 9,5035% | 0,0502% |
| CER | 2,3778% | 0,0095% |
| Casos com piora de WER | — | 0/800 |
| Casos limpos alterados incorretamente | — | 0/400 |

Latência de repetição: mediana **0,254 ms**, P95 **0,533 ms**, 5 repetições/caso.
Primeira passagem por caso: mediana **0,285 ms**, P95 **0,968 ms**. Alguns tokens já podem estar no cache de casos anteriores.
Construção de snapshot: **159,06 ms**, 2.329 chaves com o inventário de teste.
Medição separada com tracemalloc: **5,25 MiB** alocados em Python, pico **5,48 MiB**.
Essa memória exclui modelo acústico, processo total e alocações nativas; não representa consumo do App no HAOS.

Erros remanescentes nessa avaliação: palavra corrompida já válida (`porão` versus `portão`) e ambiguidade em `máquina de lavra` (`lava` versus `lavar`).
Esses casos ficam nos relatórios completos, sem exclusão das métricas.

## Avaliação manual e limites do modo fechado

`cases.jsonl`: 30 casos explícitos, incluindo nomes, comandos informais, sensores, mídia, números e três limitações deliberadas.
Parte das palavras/frases consta dos complementos de cobertura e dos testes; os números não são uma estimativa independente de precisão.

| Métrica | Original | Corrigido |
|---|---:|---:|
| WER | 10,6557% | 3,2787% |
| CER | 2,0408% | 3,0612% |
| Casos com piora de WER | — | 2/30 |

Latência de repetição: mediana **0,171 ms**, P95 **0,556 ms**, 30 repetições/caso.
Primeira passagem: P95 **1,114 ms**.

As duas regressões são palavras desconhecidas originalmente corretas: `antropologia epistemológica` e `xyzqvv`.
O modo fechado obriga substituição, com baixa confiança; isso reduz fidelidade fora do léxico e aumenta CER nesse conjunto.
`destranca a poda`, já composto de palavras válidas, permanece igual e continua diferente da referência `destranca a porta`.
A prioridade por integridade preserva palavras válidas em vez de transformar silenciosamente esse comando.

Não existe garantia simultânea de vocabulário finito fechado e preservação de todo ditado aberto.
Para ditado geral, `closed_vocabulary: false` preserva OOV. Para expandir o modo fechado, acrescente entradas explícitas a datasets,
reconstrua o App e avalie um corpus humano separado. O checker opcional não amplia o vocabulário.

## Aceitação e pendências

Implementado/verificado localmente: correção automática; quatro datasets recebidos e carregados;
termos HA de fixtures; política fechada; pesos configuráveis; publicação atômica; metadados/logs;
benchmark textual; testes de regressão; transporte Wyoming; ativação/desativação/rollback.

Ainda pendente:

1. Validar fala real, atualização de registries reais, discovery, reboot/update e STT selecionado no HAOS amd64.
2. Medir latência total e RAM/CPU no hardware de implantação. Não se afirma atingir 0,6 segundo.
3. Avaliar gravações rotuladas independentes e calibrar confiança se necessário. Metadados são heurísticos; HA Assist pode ignorar o contexto, portanto confirmação de ações continua responsabilidade do executor.

O pacote ZIP é um artefato local; o código e a documentação são distribuídos por este repositório GitHub.
A instalação no Home Assistant ainda não foi executada nesta sessão.
Não há credenciais HA nem gravações de fala disponíveis neste workspace.
O modelo baixado para verificação ficou em `dist/runtime-check/models/int8`, ignorado pelo Git e excluído do ZIP.
O Docker estava desligado inicialmente; foi iniciado para verificar e encerrado após os testes. A imagem local de verificação permanece disponível.

## Reproduzir e reverter

Comandos de teste/evaluação e instruções de atualização estão em [UPGRADE_2.0.md](UPGRADE_2.0.md).
Auditoria linguística, fórmula de ranking, política numérica e contratos estão em [ARCHITECTURE_2.0.md](ARCHITECTURE_2.0.md).
Para gerar novamente o pacote: `python tools/package_release.py`.
Para voltar ao corretor anterior: `lexical_enabled: false`, `profile: full`, reiniciar o App.
