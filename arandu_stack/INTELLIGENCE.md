# Camadas de inteligência — app 0.4.0 / HACS 0.7.0

## Habilitar

1. Atualize o app e a integração HACS. O carregamento inicial de uma atualização HACS segue o procedimento normal do Home Assistant; as mudanças posteriores nos toggles não exigem reinício.
2. Abra Configurações → Dispositivos e serviços → Arandu Assistant → Configurar. Marque **Camadas de inteligência** e avance.
3. Ajuste **Usar Decision Engine** e **Usar LLM como fallback**, depois salve. A mesma entidade de conversa continua selecionada no Assist.

As opções `decision_enabled` e `llm_enabled` pertencem exclusivamente à integração
`local_nlu`. Instalações anteriores recebem o comportamento `False` sem migração.
Opções antigas, associações de identidade e frases personalizadas são preservadas.
O slug continua `arandu_stack`; Wyoming permanece independente.

| Decision | LLM | Roteamento real |
|---|---|---|
| Desligado | Desligado | NLU, comportamento anterior |
| Ligado | Desligado | NLU, depois Decision se necessário |
| Desligado | Ligado | NLU, depois LLM com recuperação própria |
| Ligado | Ligado | NLU, depois Decision, depois LLM se necessário |

O modo contextual seguro é usado quando qualquer camada está habilitada,
inclusive se a configuração antiga selecionava o runtime legado. Com ambos
desligados, a escolha antiga do runtime continua valendo.

## Processos e política são controles distintos

No app, iniciar/parar Decision ou LLM controla o serviço e sua memória. Isso não
altera os toggles HACS. Habilitar uma camada não baixa pesos nem inicia processos.
Desabilitar o roteamento não descarrega pesos. Para liberar memória, pare o módulo
ou use o descarregamento do Lab. O LLM mantém um único `llama-server`, em loopback,
reutilizado até unload/stop ou falha; a primeira chamada pode carregar pesos.

Configure o endpoint Arandu Stack nas opções HACS quando não tiver sido descoberto
automaticamente. O gateway usa `/infer`; internamente Decision atende em 11557 e
LLM em 11558. Os modelos nunca recebem credenciais HA nem chamam serviços HA.

## Execução e segurança

O caminho é ConversationEntity → preparação de frases → LocalNluRuntime →
ContextualRuntime. Este resolve identidade, origem e sessão, cria o catálogo com
entidades expostas e ações permitidas e chama Rust `/v4/interpret` primeiro.

Um plano NLU segue direto para o executor existente. Somente `no_match` com motivo
semântico permitido, ou `target_not_found` sem candidatos nem diálogo pendente,
admite fallback. Ambiguidades, operações sem capacidade, autenticação, permissões,
confirmações, falhas de transporte do NLU e catálogos inválidos não admitem fallback.
Clarificações reais do NLU continuam usando seu diálogo existente.

O catálogo autorizado, texto e contexto espacial limitado são enviados à camada
habilitada. Decision só propõe automaticamente com calibração válida para a
impressão digital do modelo e catálogo. A política padrão exige confiança calibrada
**maior que 0,50**, e margem **pelo menos 0,08**. Similaridade bruta nunca é
probabilidade. Os ajustes do módulo podem endurecer essa política; a integração
nunca usa limites inferiores a esses mínimos para execução real.

Sem calibração, Decision pode ranquear para diagnóstico. A calibração existente
continua exigindo conjuntos distintos de treino e validação (mínimos 50 e 30).
Mudar nomes, aliases, capacidades, exposição, disponibilidade ou modelo pode
invalidar a calibração. O botão Verificar modelos consulta instalação e serviço;
a consulta de prontidão verifica a calibração sem carregar pesos.

LLM recebe candidatos limitados. Sem ranking Decision útil, o serviço faz recuperação
lexical no catálogo completo, independente de Decision. A integração valida o
contrato e a pertinência aos candidatos retornados. Para execução real, o fallback
LLM suporta um dispositivo explícito: exige evidência lexical de nome/alias,
considera área/origem e recusa empate entre dispositivos autorizados. Negação,
condição e múltiplos alvos são recusados nessa camada. Essa política conservadora
pode pedir reformulação mesmo quando um humano compreenderia o comando.
Autoconfiança numérica do LLM não autoriza execução.

Todas as propostas viram operações tipadas e passam por `ContextualRuntime.execute`.
O runtime verifica novamente usuário, exposição, capacidades, permissões, estado,
confirmação e assinatura contra execução duplicada. Antes de executar um fallback,
reconstrói o catálogo e compara sua geração. Mudanças de política durante inferência
invalidam a requisição; não se misturam decisões de duas configurações.

Cada camada tem limite de 30 segundos na integração de produção; o Lab permite
inferências mais longas (95 segundos por estágio). Timeout nunca autoriza proposta
parcial nem execução tardia. O serviço pode terminar uma inferência já iniciada
para liberar seu lock, mas não possui executor HA. As chamadas são serializadas
por módulo; Wake, STT e TTS mantêm seus ciclos próprios.

## AI Lab

O painel indica **teste independente**: suas seleções não alteram produção.
O resumo mostra comando digitado/transcrito, entendimento de cada camada,
hipótese Decision mesmo quando rejeitada, ação final, nome amigável, ID, cômodo,
origem da proposta, validação e possibilidade de execução. A simulação inicial
nunca envia ações. Execução real continua exigindo revisão explícita e token
temporário de uso único, com nova validação HA; propostas sensíveis ficam bloqueadas
no laboratório e continuam exigindo confirmação na conversa real.

O modo avançado mostra similaridades, margem, confiança calibrada, JSON completo
e tempos. Os estágios expandem seu diagnóstico. VAD/STT em texto digitado aparecem
como não necessários. Resposta HTTP 200 significa que o serviço respondeu,
nunca que uma ação foi aceita. TTS com falha deixa o resultado semântico intacto.
Benchmark, comparação, calibração, gravação e histórico continuam disponíveis.

## Problemas identificados por leitura e regressões

- LLM isolado chamava Decision e a integração validava somente IDs do ranking externo. Agora a chamada é independente e valida o ranking da recuperação retornada pelo próprio LLM.
- O app desativava a WebUI TTS, mas o Lab tentava `/api/synthesize` na porta do painel. O módulo agora oferece síntese somente em loopback 11559; `/synthesize` no gateway faz uma ponte limitada ao WAV e exige os mesmos controles de origem do `/infer`. Nenhuma porta de síntese nova é publicada.
- O gateway descartava o status do erro do modelo. Agora preserva `upstream_http_<status>` com estágio/fase, sem encaminhar corpos brutos que poderiam conter dados sensíveis.
- Leituras HTTP com `read(n)` podiam receber somente um fragmento da resposta. Gateway e adaptador LLM agora leem até EOF, com limites de bytes, inclusive para WAV.
- O adaptador Tucano mencionava Qwen3.5 numa falha de carga e encerrava o servidor após cada chamada. A mensagem foi corrigida e o servidor agora é reutilizado.
- Decision mede `load_ms`, `retrieval_ms`, `inference_ms` e `total_ms`. A recuperação inclui construção/cache de descritores e embeddings dos candidatos; inferência mede o comando e o ranking. O runtime mede validação e total da integração.

Os tempos observados de 8.834,2 ms e 11.899,9 ms não foram reproduzidos neste
computador com os modelos do HAOS. Não há evidência para atribuir esses números
a uma causa única. Compare uma chamada fria com outra aquecida, os tempos de carga,
recuperação e inferência, e os logs do módulo. Não há promessa de latência no
hardware real.

## Verificação manual no HAOS

1. Mantenha o mesmo agente no Assist. Teste um comando NLU conhecido nas quatro combinações: nenhuma inferência neural deve ocorrer.
2. Use um comando que o NLU não reconheça. Com ambos desligados, deve abster-se. Com apenas Decision, deve aceitar somente com calibração/margem válidas; caso contrário deve pedir reformulação.
3. Pare Decision e deixe apenas LLM habilitado: o fluxo deve funcionar com recuperação própria. Depois habilite ambos e confirme que Decision aceito evita LLM; Decision rejeitado pode chamar LLM.
4. Pare os serviços, teste ambiguidades e troque toggles durante uma inferência: nenhuma ação inválida deve ocorrer. Confirme os códigos nos diagnósticos e logs.
5. No AI Lab, teste texto, áudio, TTS, comparação e calibração; confirme nomes reais, hipóteses rejeitadas, modo de simulação e dados avançados. Teste desktop e mobile.

Os testes automatizados usam fakes de HA e de inferência e não medem acurácia dos
modelos. A verificação de Assist, HACS, Supervisor, áudio real, interface no frontend
HA e desempenho no hardware alvo ainda exige uma instalação HAOS real.
