# 1.0.1 / HACS 2.0.1

- Remove a moldura branca da marca exibida: cabeçalhos WebUI/AI Lab usam SVG; ícone PNG do app é renderizado do vetor em canvas transparente, com canal alfa. Mantém o bloco verde-petróleo, preservando o PNG original apenas como referência.
- Ferramenta de ativos reproduz o PNG com `--render-png`; não copia mais o PNG original opaco sobre o ícone do app. Sem mudanças nos motores ou no perfil ESPHome.
- Validação: PNG RGBA 512×512, cantos com alfa zero e centro opaco; browser check nos dois temas; 137 testes Python e 6 JavaScript aprovados, 1 Python ignorado.

# 1.0.0 / HACS 2.0.0

- Nova etapa de release por redesign, conforme a classificação do usuário. Identidade Arandu com PNG original, vetor simplificado, aplicações horizontal/compacta/monocromática e seis SVGs próprios. Sem CDN ou framework novo.
- WebUI com visão geral, navegação, temas claro/escuro/sistema, módulos, fluxo condicional explicitamente explicativo, frases, logs e ajuda. Estado vem da API; processo ligado não é confundido com política Assist ou prontidão.
- AI Lab com Testar, Comparar e Avançado, variáveis nativas HA, controles preservados e badge persistente de simulação/revisão. Sem inferência/download automático ao trocar abas. Texto novo invalida token; token não é persistido no histórico.
- ReSpeaker: perfil ESPHome 2026.10.1 remove componentes BLE de fato (`active: false` era varredura/proxy passivos), mantém tarefas em PSRAM, adiciona fonte Sendspin em PSRAM, buffers HTTP persistentes de 32 KiB e sensores de RAM interna. Script de stop word reinicia em vez de rejeitar nova execução. Não usa a opção de timeout da futura versão 2026.11 nem esconde falha de áudio desligando LEDs.
- Configuração e C++ do perfil validados em ESPHome 2026.10.1 com base pública fixada no relatório. Sem firmware binário, upload, reset XMOS ou teste de áudio físico. A captura disponível ainda mostra a falha antiga de reader; cortes posteriores exigem validação no aparelho.
- Brief e arte consolidados em docs/ativos, aplicações HACS geradas por ferramenta única e workspace com uma raiz. Projetos independentes, venvs e dados de treinamento preservados.
- Validação: 137 testes Python aprovados, 1 ignorado; 6 testes JavaScript aprovados. Playwright: ambas as interfaces, três larguras, temas, controles/segredos, simulação, token inválido após edição e ausência de inferência implícita. Capturas marcadas FIXTURE_TECNICA; sem HAOS real. NLU 0.6.0, Decision 0.3.0 e LLM 0.2.3 preservados.

# 0.7.1 / HACS 1.3.1

- LLM 0.2.3 recebe resumo limitado do NLU no último fallback: status, motivo, ação, menção e área disponíveis. Preserva fala original e permite discordar das hipóteses anteriores.
- Contexto do Decision inclui status, motivo e confiança disponível; rankings auxiliares limitados a duas hipóteses. Diagnósticos e operações do NLU não entram no prompt. Ordem NLU → Decision → LLM preservada.
- Validação: 137 testes Python passaram, 1 ignorado. Ganho de acurácia/latência não medido no modelo real. Decision 0.3.0 e NLU 0.6.0 preservados.

# 0.7.0 / HACS 1.3.0

- Decision 0.3.0 passa a executar automaticamente a ação inferida quando a confiança conjunta calibrada de intenção e alvo é pelo menos 80%, a margem é suficiente e o alvo é inequívoco. Verbo transcrito desconhecido deixa de obrigar confirmação após a aceitação do Decision. A intenção vira operação tipada; o texto original não é adulterado.
- Limite de 80% é inclusivo e validado também na integração. Similaridade bruta alta sem calibração continua sem aceitação. Configurações antigas abaixo de 0,80 usam esse mínimo efetivo; padrão novo 0,80. Políticas mais restritivas continuam respeitadas.
- Negação, condição, alvo ambíguo, falta de permissão e intenção literal conflitante continuam sem execução automática. LLM não herda a autorização do Decision; hipóteses incertas do LLM mantêm confirmação.
- AI Lab reflete a mesma política de aceitação e corrige o rótulo de threshold. Interface, descrições, opções padrão e versões atualizadas. NLU 0.6.0 e LLM 0.2.2 preservados.
- Validação: 135 testes Python passaram, 1 ignorado; 6 testes JavaScript passaram. Inclui limite exato de 80%, rejeição abaixo do limite, margem insuficiente, negação, conflito de ação e distinção entre confiança calibrada e similaridade. Sem inferência/calibração com os dados reais do usuário nesta atualização.

# 0.6.0 / HACS 1.2.0

- NLU 0.6.0 separa tipo físico de aparelho do domínio que o controla. TV/televisão/televisor e ar/ar-condicionado/split podem usar switches IR ou outros backends de energia quando o catálogo oferece turn_on/turn_off e identifica o tipo por nome, alias, classe ou prefixo de ID registrado.
- Famílias compartilhadas também abrangem iluminação, ventiladores/ventoinhas, cortinas/cortinados, persianas/estores, caixas de som/alto-falantes e outras categorias suportadas. A gramática e o índice usam as mesmas famílias. Referências incidentais, como “Câmera da TV” ou “Luz da TV”, não transformam esses aparelhos em uma TV.
- Capacidade continua obrigatória. Um media_player somente de leitura não esconde o switch de energia; um botão Power não vira ligar/desligar idempotente. Para tipos genéricos de ligar/desligar/abrir/fechar, o cômodo de origem é mantido; falta de capacidade local não seleciona outro cômodo. Área explícita continua prevalecendo e ambiguidades permanecem sem ação.
- Diagnóstico do ReSpeaker: URL TTS entregue seguida de falha md_reader/error 12 identifica alocação de memória no firmware, não falha de geração do TTS. Inclui guia e fragmento ESPHome para colocar stacks compatíveis em PSRAM, preservando FLAC, pinos e pipelines. O fragmento precisa ser mesclado ao YAML completo, validado, compilado e instalado no aparelho; não foi aplicado remotamente.
- Atualize app 0.6.0 e HACS 1.2.0. NLU interno 0.6.0; Decision/LLM 0.2.2 e servidor TTS mantêm versões anteriores. Metadados de runtime e locks do NLU sincronizados.
- Validação: 84 testes Rust selecionados de unidade, interpretação contextual e HTTP; 133 testes Python passaram e 1 ignorado; 6 testes JavaScript passaram. Clippy e formatação verificados. Gate completo legado indisponível: o pacote importado não contém os corpus/snapshots congelados exigidos pelos testes antigos. Fragmento ESPHome analisado como YAML e opções conferidas nas fontes oficiais; firmware completo não compilado nem validado no hardware.

# HACS 1.1.0 / app 0.5.2 (sem alteração)

- Configurar agora abre um menu direto: Saídas de áudio, Identidade de voz, Camadas de inteligência e Outras configurações. Remove as checkboxes de navegação.
- Áudio e identidade mostram as associações existentes por nome e ID. O formulário permanece aberto após cada inclusão, substituição ou remoção; “Salvar e concluir” começa desmarcado. Enviar vazio salva todas as alterações da sessão e fecha. Nenhuma associação antiga é apagada ou salva parcialmente.
- A lista de áudio informa saída indisponível, não exposta ao Assist ou sem controle de volume. O cadastro recusa novas associações sem esses requisitos e orienta a correção, em vez de salvar uma saída inutilizável. Permissões do usuário da voz continuam sendo verificadas na execução.
- Corrige a correspondência entre associação por dispositivo e requisição que informa apenas o satélite registrado, e vice-versa. Associação exata do satélite prevalece; vínculos conflitantes no mesmo dispositivo não são adivinhados.
- Erros de execução distinguem origem ausente, associação conflitante, saída não exposta, indisponível, sem volume e usuário sem permissão. Mantém autorização HA e não recorre a outra saída quando a associada falha.
- HACS 1.1.0 pela mudança relevante no fluxo de configuração; app 0.5.2 e serviços Decision/LLM 0.2.2 preservados. Validação: 133 testes Python passaram, 1 ignorado; sem validação visual no HA do usuário.

# HACS 1.0.1 / app 0.5.2 (sem alteração)

- Rótulo da opção de áudio passa a “Saída de áudio do satélite”, com descrição que diferencia saída de som de identidade/permissões. Acrescenta tradução `pt` além de `pt-BR`, mantendo os mesmos campos e configurações.
- Se o frontend ainda exibir a chave `configure_voice_output`, reinicie o Home Assistant após atualizar e reabra a página para renovar as traduções em cache.

# HACS 1.0.0 / app 0.5.2 (sem alteração)

- Novo controle da saída do satélite por voz: volume sem unidade usa escala 0–10 (3 = 30%); porcentagens explícitas preservam 0–100%, inclusive 5%. Aceita números falados. Valores fora da escala pedem reformulação, sem alteração de volume.
- Associação automática exige uma única saída compatível no dispositivo de origem. Nova configuração nativa permite associar satélite/dispositivo a um media player de outra integração, sem depender de ESPHome, Fully Kiosk ou Wyoming. Sem origem, capacidade, exposição ou permissão, não escolhe uma TV ou caixa do cômodo por aproximação.
- “Cancela”, “para”, “pare”, “deixa pra lá”, “esquece” e “nada”, isolados ou ao final da fala abandonada, cancelam a interação. Cancelamento autenticado não espera a inferência: descarta processamento ativo e comandos na fila do mesmo usuário/origem, remove diálogos pendentes e evita execução tardia. Não desfaz ações já enviadas ao HA.
- Interrompe áudio com media_stop da saída de origem, ou media_pause se só houver pausa. Não gera nova resposta falada. Se a saída não oferece controle compatível, o diálogo ainda é cancelado e o resultado informa a indisponibilidade da parada do áudio. Captar a voz durante reprodução depende do microfone/firmware do satélite; esta integração não habilita escuta simultânea em aparelhos que não a oferecem.
- Cancelamento não consulta o NLU/Stack nem expande frases personalizadas. Volume e cancelamento locais não usam Decision/LLM. Associações de saída não substituem identidade, exposição ou permissões do HA.
- Novas features recebem 1.0.0 conforme regra de versionamento do projeto. App 0.5.2 e Decision/LLM 0.2.2 preservados. Validação: 126 testes Python passaram, 1 ignorado; 6 testes JavaScript passaram. Sem validação física nos satélites do usuário.

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
