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
