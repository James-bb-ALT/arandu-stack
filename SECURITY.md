# Segurança

Arandu Assistant contém os módulos em processos separados dentro de um único app.
Somente módulos habilitados são iniciados. Desativar encerra o grupo de processos,
inclusive o llama-server que o LLM pode criar. Não há comandos de shell vindos da UI.

A UI usa Ingress, sem porta do painel publicada na LAN. Escritas exigem CSRF.
Os endpoints de execução passiva /runtime, /status e /infer são restritos à rede
do Supervisor; inferência exige cabeçalho próprio e rejeita Origin de navegador.
Decision e LLM escutam somente em loopback. Não há proxy de URL ou PID arbitrário.

O app não recebe permissão manager do Supervisor. Credenciais são removidas dos
ambientes dos processos NLU, Decision, LLM e Wake. Apenas STT pode receber o token
para sincronizar contexto quando essa opção for explicitamente habilitada.
Tokens próprios configurados no TTS são ocultados na resposta de opções.

Atalhos não concedem privilégios. A integração preserva o contexto HA e continua
sendo a fronteira de autorização e execução. Falhas ao obter a configuração do
painel interrompem o comando quando o endpoint está configurado.

Não publique inventário residencial, áudio, pesos ou credenciais em issues.

A página Logs lê somente fontes fixas dentro de /data. Caminhos arbitrários e
links que escapem do diretório são recusados. A leitura é limitada a 128 KiB por
fonte e o texto é renderizado como texto puro. Tokens configurados, credenciais
conhecidas do ambiente e formatos comuns de autenticação são ocultados antes de
enviar os registros ao navegador. O download usa os mesmos trechos tratados.
