# Arandu Assistant — uso

## Onde ver os erros

Abra a interface do Arandu Assistant e selecione **Logs**. A página reúne
aplicativo, Wake, STT, NLU, Decision, LLM e TTS. Filtre uma fonte ou use Todos.
O botão **Ver erro** do cartão abre o log do módulo diretamente.

Ative/desative a atualização automática e escolha 100, 300 ou 1.000 linhas finais.
Use **Baixar logs exibidos** para compartilhar o erro. Credenciais conhecidas
são ocultadas. Arquivos antigos dos módulos são preservados na atualização.
O limite por fonte é 128 KiB, para manter a interface responsiva.

Se o aplicativo inteiro não abrir, consulte o registro principal do app no HA;
os eventos e erros do controlador também são enviados para lá.

Instale apenas este app. Abra a WebUI para ativar, parar, reiniciar e configurar
os módulos internos. A configuração e as frases ficam no armazenamento persistente.
Desativar encerra o grupo de processos do módulo sem remover modelos baixados.

NLU: porta 11555. Painel: 8099. Wyoming conjunto STT/TTS/Wake: 10212.
STT 10350 e Wake 10400 continuam acessíveis diretamente para compatibilidade;
o backend TTS atende apenas em loopback na porta 10213. Decision e LLM atendem
somente em loopback e são acessados pelo gateway do painel.

## Selecionar STT e TTS no Assist

1. Ative os módulos desejados e aguarde o log **Wyoming anunciado ao Home Assistant**.
2. Abra **Configurações → Dispositivos e serviços** e configure o Arandu Wyoming descoberto. A mesma integração oferece STT, TTS e Wake ativos.
3. Edite seu assistente e selecione o reconhecimento STT e a voz TTS Arandu. O satélite deve usar esse assistente.

Um processo rodando ainda não é uma integração configurada. O anúncio só ocorre
após o serviço responder ao protocolo Wyoming, incluindo o carregamento inicial.
Se não houver descoberta, adicione **Wyoming Protocol** manualmente com o hostname
informado no log e porta 10212 para todos os módulos.
No HA OS, essa conexão usa a rede interna; não é necessário publicar portas no host.
Fora do Supervisor, configure endereço e portas acessíveis manualmente.
O Supervisor mantém um anúncio por app/serviço; o gateway combina os módulos
nesse anúncio. Na atualização de 0.3.3 ou anterior, **recarregue a integração
Wyoming existente da porta 10212** depois que STT e TTS estiverem prontos. O
endereço foi preservado, portanto não é necessário apagar a configuração TTS.
Ao ativar um novo tipo de módulo mais tarde, recarregue a integração para criar
a plataforma correspondente; o HA consulta as plataformas ao carregar a entrada.
Entradas antigas da porta 10350 continuam funcionando; evite selecionar duas
entradas STT diferentes para o mesmo motor.

As vozes Piper Arandu e Faber Medium já estão incluídas. Arandu é o padrão e pode
ser trocada pelo dropdown do TTS, sem download. Outros modelos podem exigir
auto_download habilitado para baixar seus pesos. O NLU não exige modelo externo.

Todas as opções são campos, switches ou seletores. Mudanças exigem reiniciar o
módulo; Salvar e reiniciar aplica imediatamente quando ele já está ativo. Um módulo que
encerra com erro aparece como Falha; não há ciclo de reinício automático.

Instale a integração HACS Arandu Assistant para usar o agente e os atalhos de frases.
Informe o endpoint do NLU e o endpoint do painel nas opções da integração.
Atualize a integração HACS para 0.6.2 ou posterior para aplicar as políticas NLU
editadas no painel. No Wake, a palavra selecionada vale para todas as conexões.
Frases não executam serviços arbitrários: a expansão passa pelo mesmo NLU,
com a identidade original e as permissões reais do usuário.

O app usa Ingress. Controle de processos não exige mais acesso manager ao
Supervisor. STT pode sincronizar inventário contextual usando a API HA quando
refresh_inventory for habilitado. As demais inferências não recebem credenciais.

Se instalou a versão com apps separados, mantenha os antigos parados durante
a migração e atualize os hostnames nas integrações. Os apps antigos não são
removidos automaticamente e seus dados não são copiados silenciosamente.
