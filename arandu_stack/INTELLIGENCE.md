# Camadas de inteligência — app 2.0.0 / HACS 3.0.0

A aba Fluxo de inteligência agora recebe rastreamento real da integração HACS,
separado do diagrama estático recolhido. Histórico limitado a 50 requisições em RAM,
tempos medidos por etapa, confirmação e esclarecimento distintos. Observação não
autoriza execução e falha de entrega não bloqueia o comando. A preparação inclui
consulta da política/frases do app; o NLU inclui transporte e validação do contrato;
HA inclui a execução/consulta e suas checagens ao vivo. Os tempos não medem CPU
exclusiva nem reprodução física. Wake/STT/TTS permanecem não observados neste caminho.
Texto opcional desligado por padrão, sem áudio/tokens/URLs e sem persistência em disco.
Consulte `docs/REQUEST-TRACES.md` na raiz do repositório.

A nova apresentação mantém as APIs e a ordem condicional. A WebUI mostra estado
dos processos e diagrama explicitamente estático. O AI Lab separa Testar, Comparar
e Avançado com dados reais do HA; simulação e revisão explícita continuam valendo.
Prontidão/calibração e política de produção não são inferidas a partir de processo
ligado. Nenhuma inferência ou instalação é iniciada para decorar o painel.

O último fallback inclui resumo NLU com status, motivo, ação, menção e área disponíveis (campos até 128 caracteres); nunca inclui operações ou diagnóstico completo. O Decision fornece status, motivo, confiança disponível e até duas hipóteses por ranking auxiliar. A fala original prevalece e o LLM pode discordar das pistas. NLU/Decision aceitos continuam evitando LLM. O efeito na acurácia e na latência precisa ser medido no hardware real.

## Nomes de aparelho e entidades de controle

O nome físico do aparelho não determina o domínio HA. TV/televisão/televisor
pode estar em media_player, remote ou switch IR; ar/ar-condicionado/split pode
estar em climate ou switch. As famílias de nomes são indexadas a partir do tipo
de entidade, device_class e evidência dos nomes, aliases e IDs registrados. Não
há associação por endereço residencial, marca ou entidade específica do usuário.
Comandos de ligar/desligar com evidência nominal podem usar o backend real que
oferece aquela ação, sem confundir o nome da categoria com o domínio de controle.

Exemplos de famílias conhecidas: luz/lâmpada/luminária, ventilador/ventoinha,
TV/televisão/televisor, ar/ar-condicionado/split, cortina/cortinado,
persiana/estore, alto-falante/caixa de som, robô aspirador/aspirador robô,
timer/temporizador e agenda/calendário. Nomes não geram capacidades: um botão
Power não vira ligar/desligar com estado conhecido, nem um switch ganha controle
de temperatura. Termostatos sem evidência de refrigeração não viram ar-condicionado.

Área explícita prevalece sobre origem. Para tipos genéricos sem área explícita,
ações de ligar/desligar/abrir/fechar permanecem no cômodo de origem; se não houver
ali um alvo com a capacidade pedida, não é escolhido outro cômodo. Duplicidade
ou falta de capacidade continuam sem ação. Exposição e permissões HA são mantidas.

## TTS no ReSpeaker

URL FLAC recebida seguida de `Failed to create thread 'md_reader': error 12`
indica falha de alocação no firmware do satélite. O áudio não precisa ser gerado
ou reproduzido uma segunda vez pelo agente. Veja o [ajuste de memória ESPHome](ESPHOME-AUDIO.md).
Ele precisa ser mesclado, validado, compilado e instalado no aparelho; a atualização
do app/HACS não altera automaticamente o firmware.

O AI Lab mostra durações em ms abaixo de 1.000 ms e em segundos a partir desse
limite. Dados brutos e exportações mantêm os valores originais em milissegundos.

## Volume e parada da interação

“Volume 0” silencia a saída, “volume 3” = 30%, “volume 10” = 100%.
Com unidade explícita, “volume 5 por cento” = 5%, “volume 50%” = 50%.
Números por extenso são aceitos; valores inválidos não geram ações.

Esses comandos controlam o próprio satélite. Sem associação, a integração exige
uma saída media_player única e compatível no dispositivo registrado de origem.
Em Configurar, escolha Saídas de áudio — ver e editar associações. A lista mostra
origem → saída por nome/ID e o estado da saída. Selecione um satélite OU dispositivo
e a saída para adicionar ou substituir. Enviar mantém a tela aberta e atualiza a
lista; repita para os demais aparelhos. “Salvar e concluir” começa desmarcado.
Enviar sem preencher nada salva todas as alterações da sessão e fecha. Também é
possível marcar “Salvar e concluir” junto do último cadastro. As alterações só
entram em vigor ao concluir, evitando salvamentos parciais. Selecione apenas uma
associação existente para removê-la; a remoção restaura a detecção automática.
Identidade de voz aparece em uma seção própria e usa o mesmo fluxo em sequência.

Associação feita pelo dispositivo também vale quando o Assist informa somente
seu satélite registrado; associação feita pelo satélite também vale quando chega
somente o dispositivo. A associação exata do satélite prevalece. Se satélites no
mesmo dispositivo apontarem para saídas diferentes e não houver origem suficiente
para distingui-los, a integração recusa o empate. Cadastro e lista sinalizam saída
indisponível, não exposta ao Assist ou sem controle de volume; erros na execução
também distinguem falta de permissão do usuário e falta de origem identificada.
O media player deve estar exposto ao Assist e o usuário precisa de permissão;
a associação não concede acesso nem escolhe outro aparelho se o alvo ficar indisponível.

“Cancela”, “para”, “pare”, “deixa pra lá”, “esquece” e “nada” também podem encerrar
uma fala abandonada: “liga a luz, deixa pra lá”. O cancelamento é verificado antes
dos modelos e da fila de inferência, descarta pedidos ativos/aguardando do mesmo
usuário e origem e remove confirmações/seleções pendentes. Nenhuma ação futura da
interação cancelada é executada; ações já despachadas ao HA não são revertidas.
Comandos novos, recebidos após o cancelamento, funcionam normalmente.

Para parar a fala, usa media_stop ou, se necessário, media_pause da saída vinculada.
O cancelamento não produz outro TTS. Se o aparelho não oferece parada/pausa,
o resultado distingue a indisponibilidade do áudio do cancelamento do diálogo.
O comando precisa chegar à integração: escutar durante reprodução depende do
satélite, firmware e cancelamento de eco. Não há garantia de barge-in universal.
Use o método de reativar escuta do aparelho quando necessário. Sem origem de
satélite/dispositivo identificada, o diálogo é cancelado, mas nenhum áudio é adivinhado.

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

Para o i5-6500T, ambos usam duas threads por padrão, sem afinidade ou reserva de
núcleos. O LLM usa o mesmo limite na geração e no processamento do prompt, com
polling desativado; Decision usa espera passiva nas bibliotecas de CPU. Modelos
carregados permanecem disponíveis em RAM, mas não ficam calculando ociosos.
Um lock de arquivo do sistema operacional, compartilhado pelos dois processos,
serializa carga, inferência e calibração, inclusive entre pedidos independentes
ou quando um cliente expira antes do cálculo terminar. O lock é liberado ao
encerrar o processo. A consulta `/status` continua disponível durante o cálculo.

Na produção, NLU resolve primeiro; um plano NLU dispensa os modelos. Uma proposta
Decision aceita e validada dispensa o LLM. O LLM só participa do fallback elegível
restante, se habilitado. Os modos isolados do Lab continuam disponíveis apenas
para diagnóstico. Seu prompt leva comando, contexto espacial e evidências dos
candidatos; exclui dumps NLU, diagnósticos e descrições repetidas. Scores no prompt
são arredondados, sem alterar scores originais, calibração ou validação HA.

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
**pelo menos 0,80**, e margem **pelo menos 0,08**. Similaridade bruta nunca é
probabilidade. Os ajustes do módulo podem endurecer essa política; a integração
nunca usa limites inferiores a esses mínimos para execução real.

Decision aceito nessas condições pode inferir uma ação a partir de verbo mal
transcrito e executá-la sem pedir “sim”, encerrando o fluxo antes do LLM. Continua
exigindo evidência independente e inequívoca do alvo; negação, condição e intenção
literal explicitamente conflitante não são sobrepostas pelo modelo. A decisão
vira uma operação tipada validada pelo HA, preservando o texto original para
diagnóstico. Trace informa confiança e se houve inferência do verbo. A exigência
de confirmação verbal para hipótese do LLM não é relaxada. O AI Lab usa o mesmo
limite inclusivo de 80%, mantendo sua simulação até a execução explicitamente pedida.

Valores antigos do threshold abaixo de 0,80 são aceitos na configuração para
compatibilidade, mas usam 0,80 como limite efetivo do serviço; opções acima disso
continuam mais restritivas. Novas instalações usam 0,80 por padrão.

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

### Hipóteses e confirmação (app 0.5.0 / HACS 0.8.0)

Decision analisa separadamente ação, dispositivo e área. Remove nomes e áreas já
identificados para analisar o fragmento de ação, compara embeddings dos candidatos
de ação e calcula proximidade lexical genérica. Não existe regra particular de
substituição de uma palavra por outra. O ranking do dispositivo usa um fragmento
próprio; área explícita e área de origem continuam sendo contexto autorizado.
Esses campos aparecem em `analysis`, com `analysis_ms`, e no resumo do Lab.

O score conjunto e a calibração existentes continuam governando a aceitação do
Decision. Similaridade dos fragmentos não é probabilidade nem autorização. Se
Decision não aceitar, o LLM recebe os três campos, os rankings e o motivo da
recusa. LLM-only faz a análise lexical/contextual independente de Decision.

Uma hipótese plausível sobre uma ação suportada pode ser apresentada para
confirmação, inclusive com uma palavra de ação mal transcrita. Por exemplo:
“Acho que você gostaria de ligar Ventilador (Escritório de Jaime). É isso?”. A
hipótese não executa nada. O sim volta ao executor HA, reconstruindo o catálogo
e validando novamente identidade, permissões, exposição, origem e política.
“Não” cancela a hipótese; um comando completo corrigido substitui a hipótese e
volta ao NLU. A confirmação expira em até 30 segundos e não pode ser reutilizada.

Dispositivos realmente ambíguos, negação/condições e propostas incompatíveis
continuam sem hipótese executável. Sem evidência suficiente, pede reformulação.
Sem calibração válida, Decision não ganha aceitação automática por esses novos
scores; pode alimentar o LLM habilitado. Ações reconhecidas claramente pelo NLU
continuam no caminho direto.

No Lab, hipóteses confirmáveis têm `confirmation_required=true`, a pergunta e a
ação exata proposta, mas não recebem token para execução. A conversa real é quem
mantém a confirmação vinculada à sessão. No contrato dos serviços, a hipótese
também permanece marcada como `requires_clarification=true` para que clientes
anteriores não executem ao ignorar o metadado de confirmação.

### Esclarecimento sem hipótese suficiente

Desde app 0.4.3 / HACS 0.7.3, a falta de compreensão produz uma pergunta em
português, independentemente de a última camada ser NLU, Decision ou LLM.
Se o dispositivo for inequívoco mas a ação não, a resposta é semelhante a:
“Acredito que você esteja falando de Ventilador (Escritório de Jaime), mas não
entendi o que fazer. Pode repetir?”. Os nomes vêm do catálogo autorizado.
“Leva o ventilador” não autoriza ligar automaticamente. Desde 0.5.0, pode produzir
uma hipótese que exige confirmação explícita; sem hipótese suficiente, pede repetição.

Sem área explícita, a resolução usa a área registrada do satélite/dispositivo,
conforme a resolução de origem do NLU (incluindo a área padrão já configurada
quando não há origem registrada). Área explícita conhecida tem prioridade.
Área explícita desconhecida, empate ou dispositivo indisponível pedem repetição;
não se escolhe outro cômodo silenciosamente. O alvo reconhecido fica no contexto
da conversa; uma resposta posterior como “liga” volta ao NLU e às validações HA.

Uma área inventada pelo LLM não é corrigida para executar a proposta. O schema
restringe as saídas aos IDs reais; se ainda assim a proposta for semanticamente
inválida, o sistema se abstém e pergunta, sem executar. Esclarecimentos podem
conter um ID candidato com intent=null e requires_clarification=true. Esses IDs
nunca viram operações sem ação explícita e validação posterior. Instale app e
HACS juntos para manter o novo contrato compatível.

O painel indica **teste independente**: suas seleções não alteram produção.
Desde app 0.5.1 / HACS 0.8.1, use **Área de origem do teste** para escolher um
cômodo do catálogo real. O teste digitado não possui satélite; sem seleção,
somente a área padrão já configurada na integração pode fornecer contexto.
A seleção é enviada igualmente ao NLU, Decision e LLM e aparece em `test_context`.
Ela não muda a área padrão nem cria uma origem de satélite em produção.

A análise diferencia o nome principal do aparelho de referências incidentais
em sensores: “ventilador” não precisa empatar com “velocidade do ventilador”.
Quando a ação proposta só é compatível com um candidato, essa capacidade também
entra na resolução. Dois ventiladores compatíveis em cômodos diferentes continuam
ambíguos sem contexto: o painel e a pergunta listam as alternativas. Nesse caso,
selecione uma área de teste ou diga o cômodo no comando. O fragmento da ação
continua separado mesmo quando o dispositivo ainda estiver ambíguo.

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

### Saída LLM incompleta ou JSON inválido (app 0.4.1 / HACS 0.7.1)

O adaptador verifica `choices[0].finish_reason` antes de interpretar a ação.
`length` produz `llm_output_truncated`: aumente **LLM → Limite da resposta**
para 192 ou 256, salve e reinicie o módulo LLM. Limites como 64 podem cortar o
contrato JSON. O valor configurado não é aumentado silenciosamente.
`stop` com JSON malformado produz `llm_invalid_json`; outros encerramentos ou
envelopes inválidos também são recusados. Nenhum fragmento é reparado ou executado.
O servidor carregado é preservado quando o problema se limita ao conteúdo da saída.

Um log com 64 tokens gerados e JSONDecodeError sugere truncamento, mas somente
o `finish_reason` confirma esse diagnóstico. A carga e a inferência terem sido
concluídas não validam a ação. Avisos de GPU em uma compilação CPU não demonstram
a causa de uma falha de JSON. A implementação de
[response_format na revisão fixada do llama.cpp](https://github.com/ggml-org/llama.cpp/blob/f1a7dea99aae1623bad10c4ce31fe220033ab7f6/tools/server/server-common.cpp)
aceita o wrapper `json_schema.schema` já usado pelo adaptador.

### Outros problemas corrigidos

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
