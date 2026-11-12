# Fase 5 — NLU contextual, automações, listas e consultas

Base: `dfe574b327fccb6a04a8c2ee05340f1c9081abe7`. PR #2 da Fase 4 integrado
com merge normal; `549dc61` e `af43bb4` preservados no histórico.

## Comportamento entregue

- Automações existentes: consultar, habilitar, desabilitar e solicitar execução.
  Execução exige confirmação e envia `automation.trigger` com
  `skip_condition: false`. A resposta não afirma que as ações internas ocorreram.
- Listas expostas: leitura, inclusão literal, conclusão, remoção e renomeação.
  Mutação de item existente resolve UID novamente antes do serviço; homônimos
  são recusados. Limpeza de concluídos exige confirmação com quantidade e
  comparação do conjunto de UID/resumo. A feature DELETE_ITEM (2) autoriza
  limpeza; MOVE_ITEM (8) não autoriza remoção.
- Contexto efêmero por identidade, conversa e origem: copiar ação absoluta
  para área exata ou único outro alvo. Não copia ações críticas, cenas ou
  automações. Undo guarda apenas uma alteração reversível confirmada pelo HA,
  captura pré-estado imediatamente antes do serviço e compara pós-estado antes
  da inversão. Alteração externa detectada, expiração ou mudança de catálogo
  invalida o registro. Não há transação atômica com outros clientes do HA.
- Consultas atuais: extremos de temperatura ambiente, ranking de bateria e
  potência, com unidades compatíveis e até oito fontes. Energia e potência
  ambíguas pedem esclarecimento. Sensores de equipamento/exterior identificados
  não entram no extremo ambiente; a preferência explícita pode classificá-los.
- Histórico: energia acumulada por diferença de estatísticas, tendência, pico
  e duração observada de estado. Somente leitura de fontes expostas/autorizadas.
  Sem Recorder ou cobertura suficiente, retorna erro específico, sem estimar.
- Cenas: nome/alias exato e único; `modo cinema`, cumprimentos e hora de dormir
  só acionam cena correspondente exposta. Homônimos de automação não substituem
  cenas. Não cria rotinas, automações, condições contínuas nem anúncios.

## Contratos e limites

O Rust permanece passivo: nenhum token HA, serviço arbitrário ou efeito. A
integração HA continua sendo o único executor e revalida identidade, exposição,
capability e origem após I/O e antes de efeitos. Conteúdo de lista é dado literal.
As famílias novas ambíguas não caem no Decision/LLM. Os testes determinísticos
substituem o transporte neural por uma função que falha se chamada.

Protocolo v4 aditivo. `client.py` recebeu uma negociação mínima de capability
via catálogo vazio (`reason: phase5_supported`); é a única extensão fora da lista
principal de propriedade, necessária à compatibilidade com servidor antigo.
Servidor antigo mantém comandos antigos e recusa contexto novo. O catálogo
entregue aos modelos antigos remove apenas as ações novas e recalcula geração;
o executor conserva o catálogo autoritativo. Diagnósticos permanecem iguais.

Recorder usa helpers locais revisados para **HA 2026.11** e valida versão e
assinatura. São interfaces internas versionadas, não uma promessa de API estável.
Outras versões retornam `history_unavailable`. Não usa SQL, HTTP para si mesmo,
token nem exportação de histórico. Janelas usam fuso do HA, com DST. Histórico
bruto: até 25 horas, 4096 pontos, 1 MiB, um trabalho por HA e timeout de 5 s.
Cancelamento não libera admissão enquanto o trabalho nativo continua. Estatísticas
semanais são horárias, limitadas a sete dias; a resposta informa cobertura.
Duração exige amostras com intervalos até cinco minutos; tendência/pico bruto,
até quinze minutos. Mudanças de unidade, lacunas e resets são recusados.
Energia exige limites horários completos; períodos parciais podem não ter dados
suficientes. Recusa conservadora é preferível a inventar consumo.

Contratos revisados: [todo](https://www.home-assistant.io/integrations/todo/),
[automation.trigger](https://www.home-assistant.io/actions/automation.trigger/),
e código oficial HA Core `2026.11.1` em `components/recorder/{history,statistics}`
e `components/todo/const.py`. Nenhuma dependência nova.

## Arquivos e integração

Rust: `contextual/{contract,grammar,mod,resolver,slots}.rs`, `grammar.json` e
`engine/tests/test_phase5.rs`. HA: runtime/protocolo/catálogo/capabilities/queries/
errors/client e novos `automation_actions`, `todo_actions`, `dialogue_context`,
`advanced_queries`. Testes `tests/test_phase5_*`.

Não foram alterados arquivos exclusivos da Fase 6, modelos, STT/TTS, Decision,
LLM, conversation/config_flow/voice_controls ou metadados globais. Não há release
isolada. Candidatos para integração final: app/HACS **11.0.0**, NLU **1.0.0**
(nova etapa, sujeitos à release conjunta F5/F6). Versões atuais preservadas.
Integrador deve manter o gancho existente `ContextualRuntime.process`, juntar
as duas fases e executar novamente os gates antes de uma única publicação.

## Evidência e limites

Testes HTTP executam binário Rust real e executor HA simulado, sem ações físicas.
Histórico usa fixtures de contrato; não foi validado em Recorder residencial.
Não há precisão linguística medida: exemplos novos são `FIXTURE_TECNICA`,
autoria do projeto, Apache-2.0. Benchmark de 100 consultas usa host Windows de
desenvolvimento e HA simulado; não representa i5-6500T nem latência de HA físico.
Resultados detalhados e comandos estão em `PHASE-5-EVIDENCE.json`.

O gate histórico `tools/mlp-check` não existe nesta base. Testes legados de
empacotamento dependem de Ruby ausente no Windows; demais falhas legadas foram
comparadas com a F4. Não foram corrigidas cópias HACS históricas internas.
