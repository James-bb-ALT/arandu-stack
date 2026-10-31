# HACS 0.8.3 / app 0.5.2 (sem alteração)

- AI Lab converte automaticamente durações a partir de 1.000 ms para segundos, com duas casas decimais; abaixo desse limite mantém milissegundos. Formatação em português: 450,0 ms, 1,50 s.
- Aplica a mesma regra a etapas, total, percentis, comparativos, médias, carga, testes prontos, contador de execução e transcrição. Dados brutos e exportações continuam em ms para preservar compatibilidade.
- Mudança apenas no painel HACS; app e serviços Decision/LLM mantêm suas versões. Verificação inclui o limite de 1 segundo e a preservação dos dados originais.

# 0.5.2 / HACS 0.8.2

- Decision/LLM 0.2.2: modelos reutilizados em memória após o primeiro uso, com duas threads por modelo por padrão e sem reserva de núcleos. O LLM limita também threads de processamento do prompt e desativa polling; Decision limita paralelismo auxiliar e usa espera passiva.
- Lock compartilhado entre processos impede inferências simultâneas de Decision e LLM, inclusive entre pedidos independentes e se o cliente expirar antes de o cálculo terminar.
- Prompt de sistema do LLM passa de 983 para 671 caracteres (32% menor; não é contagem de tokens). Remove dumps NLU, diagnósticos redundantes e descrições repetidas do contexto; mantém candidatos, ações, evidência semântica, validação e confirmação humana.
- Preserva a saída antecipada da produção: NLU resolveu, encerra; Decision aceito e validado, encerra; LLM recebe apenas o fallback elegível restante, quando habilitado.
- Sincroniza versões, descrições e regra permanente de versionamento. Atualize o app para 0.5.2 e HACS para 0.8.2; serviços internos Decision/LLM 0.2.2.
- Validação: 108 testes Python passaram, 1 ignorado; 4 testes JavaScript passaram. Bloqueio entre processos verificado no Windows; verificação Linux pendente por falha do WSL. Sem benchmark no i5-6500T, build de imagem ou instalação no HA nesta atualização.

# 0.5.1 / HACS 0.8.1

- Corrige empates entre a referência ao aparelho e uma menção incidental em sensores relacionados, como “Ventilador” e “Velocidade do ventilador”. A resolução também verifica compatibilidade com a ação proposta; dois aparelhos realmente equivalentes continuam ambíguos.
- A separação do fragmento de ação não depende mais de resolver primeiro um dispositivo único. Os candidatos empatados aparecem no diagnóstico e na pergunta, em vez de somente “não entendi”.
- AI Lab permite escolher Área de origem do teste. A mesma área é enviada a NLU, Decision e LLM; não cria um satélite nem altera opções de produção. A seleção invalida tokens de execução antigos e o backend confere o vínculo à área do teste.
- Margem do ranking conjunto deixa de ser apresentada como prova de ambiguidade exclusiva do alvo. Códigos de resolução de contexto recebem explicação em português.
- Serviços Decision/LLM 0.2.1. Atualize app 0.5.1 e HACS 0.8.1 juntos.

# 0.5.0 / HACS 0.8.0

- Decision apresenta análise em três partes: ação, dispositivo e área. A ação usa similaridade semântica do fragmento e proximidade lexical genérica; o dispositivo tem ranking próprio; a área mantém a precedência de origem/menção explícita. Sem mapas de substituição de palavras.
- Essa análise e o motivo da recusa chegam ao LLM quando Decision não aceita. Os diagnósticos separados não modificam o score conjunto nem dispensam calibração para aceitação do Decision.
- Hipóteses de ação plausíveis com dispositivo inequívoco geram “Acho que você gostaria de ligar Ventilador (...). É isso?”. Só o “sim” executa, após nova validação HA. “Não” cancela; um comando corrigido substitui a hipótese.
- Confirmações são de uso único, expiram em até 30 segundos e ficam vinculadas ao usuário, origem, catálogo e política. O catálogo é reconstruído no sim; mudanças de permissões ou contexto impedem execução.
- AI Lab mostra as três partes e a hipótese aguardando confirmação. Não emite token de execução para hipóteses incertas. Propostas incertas continuam marcadas como esclarecimento no contrato do serviço para impedir execução por clientes antigos.
- Atualize app e HACS juntos: 0.5.0 / 0.8.0. Serviços Decision e LLM 0.2.0. Compreensão incompleta sem hipótese suficiente continua pedindo repetição; alvos ambíguos não são escolhidos por score.

# 0.4.3 / HACS 0.7.3

- Dispositivo reconhecido sem ação clara gera pergunta com nome e cômodo reais, em vez de converter `leva` em `ligar` por palpite. Aplica-se à recusa final do NLU e às propostas Decision/LLM.
- A resolução semântica usa área explícita antes da área registrada do satélite/dispositivo. Empates, áreas explícitas desconhecidas e dispositivos indisponíveis continuam sem execução.
- LLM recebe schema com IDs reais de áreas e dos candidatos recuperados. Propostas semanticamente inválidas, como nome de cômodo no lugar de area_id, viram esclarecimento sem ação; JSON truncado/malformado continua sendo erro técnico.
- Contrato de esclarecimento permite manter o dispositivo candidato sem intenção executável. A pergunta usa dados do catálogo autorizado, sem repetir nomes inventados pelo modelo. Contexto é revalidado antes de falar e preservado para o próximo comando.
- AI Lab mostra a pergunta e mantém simulação/diagnóstico. Atualize app e HACS juntos: 0.4.3 / 0.7.3. Serviço LLM 0.1.3.

# 0.4.2 / HACS 0.7.2

- Corrige o campo Limite da resposta vazio ao reabrir Configurar no LLM: o filtro de credenciais removia `max_tokens` por conter a palavra token.
- A API do painel preserva `max_tokens` somente quando inteiro; credenciais e valores inesperados continuam ocultos. Não modifica valores salvos nem requer migração.
- Regressões verificam salvar 256, recarregar a configuração e consultar o formulário repetidamente, mantendo as credenciais ocultas. A correção está no app; a integração HACS recebe apenas atualização de versão.

# 0.4.1 / HACS 0.7.1

- LLM verifica `finish_reason` antes de interpretar o JSON. Geração encerrada por limite de tokens é recusada mesmo se o fragmento formar JSON válido; nenhuma ação parcial é autorizada.
- Diagnósticos distinguem `llm_output_truncated`, `llm_invalid_json`, `llm_response_invalid` e `llm_response_incomplete`, preservando os códigos até o AI Lab sem expor a saída bruta.
- Opção max_tokens explica o risco de limites baixos e recomenda 192 ou 256. O valor salvo continua respeitado, sem aumento silencioso nem nova tentativa automática.
- Erros de saída não descarregam o servidor saudável, evitando uma nova carga de pesos no próximo teste. Falhas de processo/transporte continuam encerrando o servidor.
- Prompt solicita JSON compacto. Serviço interno LLM 0.1.2; correção do adaptador exige atualizar o app, além da integração para as mensagens do Lab.

# 0.4.0 / HACS 0.7.0

- Toggles independentes de Decision e LLM nas opções HACS, desligados por padrão e aplicados sem recriar o agente.
- Fallback semântico no runtime contextual, com catálogo autorizado, revalidação de identidade/política/catálogo e executor HA existente.
- Decision mantém calibração obrigatória, confiança exclusiva acima de 0,50 e margem mínima de 0,08. Estado de calibração consultável sem carregar pesos.
- LLM-only dispensa Decision e valida os candidatos da recuperação própria; produção exige evidência de alvo único e recusa empates.
- AI Lab separa hipótese, proposta, validação e execução; mostra dispositivos, áreas, motivos, estágios expansíveis e falhas de áudio separadas.
- Gateway preserva códigos HTTP de falhas internas sem expor corpos brutos. TTS do laboratório usa uma ponte interna para o servidor de síntese em loopback.
- LLM reutiliza um servidor por processo do módulo até unload/stop; Decision mede recuperação separadamente de inferência e carga.
- Serviços internos Decision/LLM 0.1.1. Compatibilidade HACS/Rust diagnosticada por protocolo; versões dos componentes continuam independentes.

# 0.3.5

- App anuncia a integração Arandu Assistant (`local_nlu`) ao Supervisor quando o NLU responde à saúde, inclusive com áudio desativado.
- Integração HACS 0.6.5 recebe a descoberta e oferece confirmação com endpoints preenchidos.
- Entradas existentes não são duplicadas, recriadas ou reconfiguradas pelo anúncio.
- Descoberta Wyoming permanece independente da descoberta do agente NLU.

# 0.3.4

- STT, TTS e Wake compartilham descoberta automática por uma integração Wyoming na porta 10212.
- Gateway reúne Describe/Info dos módulos ativos e encaminha áudio ao motor correspondente.
- Endereço TTS existente preservado; recarregue a integração para criar também a entidade STT.
- TTS ignora a mensagem completa de compatibilidade recebida durante streaming, evitando falar a mesma frase duas vezes.
- Testes de regressão com transportes fake adicionados, sem executar modelos ou testes.

# 0.3.3

- Inclui os seis datasets obrigatórios do corretor lexical STT e o manifesto original.
- Construção da imagem falha se algum dataset obrigatório estiver ausente ou vazio.
- Controlador anuncia TTS ao Supervisor após resposta Wyoming Describe válida.
- STT e Wake mostram conexão manual nos logs, sem sobrescrever o único anúncio Wyoming permitido por app.
- Descoberta tem nova tentativa automática e diagnóstico nos logs do módulo, sem passar credenciais aos motores.
- Configure os serviços descobertos em Dispositivos e serviços antes de selecioná-los no Assist.

# 0.3.2

- TTS: remove o argumento interno do dispatcher antes do parser do servidor Wyoming.
- STT: baixa pesos adaptados da release pública do Stack, sem exigir acesso ao repositório privado.
- Preserva os hashes SHA256 do modelo e informa a URL em falhas HTTP.
- Regressões manuais adicionadas; testes e inferências não executados.

# 0.3.1

- Página Logs reúne aplicativo e todos os módulos, com filtro e atualização automática.
- Atalho Ver erro/Logs nos cartões e download dos trechos exibidos.
- Tracebacks, saída do llama-server, falhas de inicialização e códigos de saída visíveis.
- Credenciais conhecidas ocultadas; leitura limitada a arquivos de log fixos.
- Falha de um módulo na inicialização não impede a abertura da interface.
- Arquivos de logs existentes preservados durante a atualização.

# 0.3.0

- Formulários tipados para todos os módulos, com switches, campos e seletores.
- Palavra de ativação global: Alexa, Hey Jarvis, Hey Mycroft, Hey Rhasspy e Okay Nabu.
- Voz Piper Arandu treinada, exportada e incluída como padrão; Faber Medium também incluída.
- Controles efetivos de Decision, LLM e políticas NLU; proteção dos tokens ao salvar.
- Migração do padrão TTS preserva escolhas posteriores do usuário e guarda backup das opções antigas.

# 0.2.0

- Um único app Arandu Assistant inclui os seis motores.
- Controle de processos internos, ambientes Python isolados e configuração persistente.
- Sem permissão manager do Supervisor; nenhum app de motor precisa ser instalado separadamente.
- Slug do painel e frases existentes preservados.

# 0.1.0

- Painel Ingress central com controles independentes de módulos.
- Configuração de opções e atalhos de frases persistentes.
- Integração com o NLU mantém validação e identidade do chamador.
