# Arandu Assistant — uso

Instale apenas este app. Abra a WebUI para ativar, parar, reiniciar e configurar
os módulos internos. A configuração e as frases ficam no armazenamento persistente.
Desativar encerra o grupo de processos do módulo sem remover modelos baixados.

NLU: porta 11555. Painel: 8099. Wyoming STT: 10350; TTS: 10212; Wake: 10400.
Use o mesmo hostname do app em todas as integrações. Decision e LLM atendem
somente em loopback e são acessados pelo gateway do painel.

Na primeira configuração, habilite auto_download dos módulos desejados para baixar
os pesos e reinicie esses módulos. Depois pode desabilitar auto_download para
reutilizar apenas os arquivos locais. O NLU não precisa de download de modelo.

As opções são validadas pelo controlador. enabled deve ser alterado pelos botões
Ativar/Desativar. Mudanças de opções exigem reiniciar o módulo. Um módulo que
encerra com erro aparece como Falha; não há ciclo de reinício automático.

Instale a integração HACS Arandu Assistant para usar o agente e os atalhos de frases.
Informe o endpoint do NLU e o endpoint do painel nas opções da integração.
Frases não executam serviços arbitrários: a expansão passa pelo mesmo NLU,
com a identidade original e as permissões reais do usuário.

O app usa Ingress. Controle de processos não exige mais acesso manager ao
Supervisor. STT pode sincronizar inventário contextual usando a API HA quando
refresh_inventory for habilitado. As demais inferências não recebem credenciais.

Se instalou a versão com apps separados, mantenha os antigos parados durante
a migração e atualize os hostnames nas integrações. Os apps antigos não são
removidos automaticamente e seus dados não são copiados silenciosamente.
