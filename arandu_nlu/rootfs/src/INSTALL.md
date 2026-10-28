# Arandu NLU — instalação e atualização

Versão atual: `0.5.1`.

As correções contextuais e os passos de atualização sem perda de configuração
estão em [auditoria espacial 0.4.4](docs/nlu-2.0/CONTEXTUAL-SPATIAL-AUDIT.md).

O motor Rust é atualizado pela **Add-on Store**; a integração Python, pelo **HACS**.
Atualize os dois componentes e reinicie o Home Assistant após atualizar pelo HACS.
Baixe os diagnósticos da integração em Configurações → Dispositivos e serviços
e confira `integration_version`, `service_version`, `protocol=4`,
`contextual_enabled=true`, `route=/v4/interpret` e `compatible=true`.
Uma etiqueta do Supervisor sozinha não comprova qual protocolo o Assist usa.

Configuração de exclusões, associações de aparelhos e localização com timestamp:
[guia contextual](docs/nlu-2.0/DEPLOYMENT.md#capacidades-contextuais-040).

São dois componentes: o add-on Rust em `addon/` e a integração Python em
`custom_components/local_nlu/`. A loja instala e atualiza somente o add-on.
Instalar o repositório não instala a integração. Ambos compartilham a versão do produto.

## Instalação pelo HACS

Requer HA 2025.3.0+ e HACS 2.0.5+. São mínimos de APIs/distribuição declarados;
a checklist abaixo deve ser executada na sua instalação. Instale o HACS seguindo
o [guia oficial](https://www.hacs.xyz/docs/use/).

1. Em Configurações → Apps/Add-ons → Loja → ⋮ → Repositórios, adicione
   `https://github.com/jaimevictor/Arandu-NLU`. Instale **ARANDU NLU** e inicie.
   O Supervisor compila diretamente `addon/Dockerfile`, sem imagem remota.
2. No HACS, abra ⋮ → Repositórios personalizados (Custom repositories). Informe
   `https://github.com/jaimevictor/Arandu-NLU` e selecione **Integration**.
   Procure **ARANDU NLU**, abra sua página e escolha **Baixar/Download**.
   Antes da primeira release, selecione `master`; depois selecione a versão
   estável desejada. Confira a versão instalada no diagnóstico após reiniciar.
3. Reinicie o Home Assistant. Adicione **ARANDU NLU** em Dispositivos e serviços.
   Configure o endpoint privado
   indicado pelo Supervisor e selecione ARANDU NLU no pipeline Assist.

Somente `addon/` é publicado. Nome **ARANDU NLU**, slug `ptbr_nlu` e URL do
repositório permanecem. A identidade usa hash do repositório + slug, não o nome da pasta.
Assim, a mudança de pasta não exige reinstalar ou apagar configurações.
[Implementação do Supervisor](https://github.com/home-assistant/supervisor/blob/main/supervisor/store/data.py).

O hostname de loja para a URL acima é `http://18b0d50a-ptbr-nlu:11555`;
confirme o hostname na sua instalação. Para instalação local, copie apenas
`addon/` para `/addons/arandu_nlu`: o hostname é `http://local-ptbr-nlu:11555`.
Não crie uma segunda cópia com o mesmo slug no mesmo repositório local.
Trocar uma instalação de loja por local altera o hash da identidade; esse caminho
é para novas instalações, não é o procedimento de migração abaixo.
Sem Supervisor, compile `addon/` e conecte o container à rede privada do HA.
Nenhuma porta é publicada no host pela configuração do add-on.

### Migrar uma instalação Python manual

1. Faça backup completo do HA, incluindo `.storage`, opções e arquivos atuais.
2. Adicione o repositório no HACS como **Integration** e baixe ARANDU NLU.
   O HACS grava no mesmo `/config/custom_components/local_nlu/`; não é necessário
   remover a entrada de Dispositivos e serviços nem apagar os arquivos antes.
3. Reinicie o HA. Abra a entrada que já existia, confira opções e diagnóstico,
   e mantenha o agente selecionado no Assist.

O domínio `local_nlu`, VERSION do config flow e unique_id da entidade continuam
iguais. O HACS substitui arquivos do componente; as config entries, options e
registros do HA ficam em `.storage`, fora desse diretório. Na primeira instalação
ele pode deixar arquivos extras de uma cópia antiga: não personalize arquivos de
runtime. Se houver uma cópia divergente, após backup remova **somente a pasta**
`/config/custom_components/local_nlu/`, instale pelo HACS e reinicie. Isso jamais
significa excluir a entrada da integração ou `.storage`.
[Código de instalação/backup HACS](https://github.com/hacs/integration/blob/adb7d83e33d24325535fb43b8226572405143757/custom_components/hacs/repositories/base.py).

### Atualizar a integração Python

Abra ARANDU NLU no HACS → Atualizar/Baixar → versão desejada → reinicie HA.
Antes de existir release, atualizações acompanham commits de `master` e o HACS
mostra um hash; o manifesto/diagnóstico informa a versão do produto. Quando houver
releases publicadas, escolha a versão estável: uma tag sozinha não é uma release.
Se estiver seguindo `master`, use Baixar novamente para mudar para a versão
estável quando ela for publicada. HACS não atualiza o add-on automaticamente.
Mantenha ambos em 0.4.3; a atualização do motor segue o procedimento abaixo.

## Migrar do add-on antigo / atualizar

Se a instalação existente for **local**, substitua o conteúdo da pasta já
usada em `/addons/` pelo conteúdo de `addon/`, mesmo que a pasta antiga se
chame `ptbr_nlu`. Não mantenha dois manifestos locais com esse slug. Preserve
os dados/configurações do app e siga refresh, atualização/rebuild e restart
abaixo; não troque para o repositório GitHub durante essa migração.

1. Faça backup completo do HA e guarde as opções da integração e do add-on.
2. Na loja, use ⋮ → Verificar atualizações (Check for updates). Espere o catálogo
   atualizar. Confira que **ARANDU NLU** oferece **0.4.3**, mantendo o mesmo identificador.
   Não remova e adicione o repositório a cada atualização.
3. Na página do add-on, execute **Atualizar**. Como não existe campo `image`,
   o Supervisor reconstrói a imagem a partir da pasta publicada. Espere o build terminar;
   confira os logs do Supervisor e a versão instalada, não apenas a versão disponível.
4. Inicie/reinicie o add-on e confira seus logs. `GET /health` deve responder
   `{"status":"ok","version":1}`. Esse 1 é a versão do protocolo de saúde,
   não a versão do release.
5. Atualize **ARANDU NLU** pelo HACS. Preserve `/config/.storage/` e as opções
   existentes; não apague nem recrie a entrada da integração.
6. Reinicie o Home Assistant. Confira `manifest.json` com versão **0.4.3**,
   o endpoint e a opção `contextual_enabled=true`. Verifique exposição ao Assist,
   permissões do usuário e área do satélite.
7. Teste uma consulta e uma ação contextual com entidades reais configuradas,
   por exemplo “Qual é a temperatura aqui?” e “Liga o ar”.
   Confira resposta e histórico dos serviços. Esse passo exige a instalação real.

Publicação em GitHub, atualização do catálogo, instalação/build e reinicialização
são passos separados. Um push não altera o container que já está rodando.
Se o catálogo continuar antigo, confira URL/branch padrão `master`, conexão e logs
do Supervisor. Se a versão disponível estiver correta mas a instalada estiver antiga,
resolva o erro do build antes de tentar novamente. Não apague configurações.
[Atualização do repositório no Supervisor](https://github.com/home-assistant/supervisor/blob/main/supervisor/store/repository.py).

## Diagnóstico pós-atualização

### Pipelines e satélites sem usuário — correção 0.4.3

Em Configurações → Dispositivos e serviços → ARANDU NLU → Configurar, escolha
**Usuário para pipelines sem identidade**, selecione seu usuário ativo e salve.
Mantenha contextual_enabled=true. Não edite JSON/YAML/.storage. A mudança é
aplicada às próximas requisições e invalida diálogos antigos, sem reiniciar HA;
a atualização de arquivos pelo HACS exige reinício do HA antes dessa configuração.
Escolha — para desativar o fallback. Não há seleção automática de admin/owner.

Se necessário, marque **Configurar associações por dispositivo/satélite**:
escolha um satélite OU dispositivo e seu usuário. Satélite usa entity_id do
assist_satellite; dispositivo usa ID do device registry, por seletores oficiais.
Desmarque Salvar e concluir para incluir outra associação. A lista de associações
existentes permite remover inclusive uma origem indisponível. Não confunda essas
associações de autorização com person_device_bindings (aliases de aparelhos).

Precedência: contexto autenticado, satélite associado, dispositivo associado,
fallback, missing_user. Usuário inexistente/inativo em uma fonte selecionada
nunca cai para outra fonte. Exposição e permissões HA continuam obrigatórias.
Sem identidade/configuração, a fala informa que o dispositivo ainda não está
associado a um usuário. O Rust não recebe IDs de usuários nem credenciais.

Após testar um comando sem context.user_id, o diagnóstico deve registrar
identity.last_source=fallback e last_outcome.reason diferente de missing_user.
Para uma origem associada, a fonte será satellite_binding ou device_binding;
WebSocket autenticado normalmente registra context. Mantenha ambos os componentes
em 0.4.3 e consulte a [checklist específica](docs/nlu-2.0/MISSING_USER_RESIDENTIAL_FIX.md).

Em Configurações → Dispositivos e serviços → ARANDU NLU → Baixar diagnósticos,
confira os valores efetivamente carregados:

```json
{
  "integration_version": "0.4.3",
  "service_version": "0.4.3",
  "contextual_enabled": true,
  "protocol": 4,
  "route": "/v4/interpret",
  "compatible": true
}
```

`integration_addon_version_mismatch` indica versões/protocolos incompatíveis:
atualize o componente atrasado pelo seu canal e reinicie. Se o manifesto em disco
for novo mas o HA continuar usando versão antiga, confirme o diretório e reinicie.
`backend_diagnostics_unavailable` indica backend inacessível; confira o endpoint
privado, estado/logs do add-on e conexão. Não exponha a porta para resolver isso.

## Rollback

Restaure o backup do add-on e da configuração HA anterior à atualização,
ou reinstale explicitamente o pacote/imagem anterior junto com a integração
da mesma versão e reinicie ambos. Desativar `contextual_enabled` retorna ao
fluxo anterior de interpretação. A integração mantém suas configurações em
HA; o Rust não mantém um banco persistente. Não reinstale o Home Assistant.

## Build e pacotes reproduzíveis

Em Windows com Docker Linux, execute `./tools/mlp-dev.ps1 -Task check`.
Em Linux com as ferramentas pinadas, execute `./tools/mlp-check`.
Para a imagem amd64: `./tools/mlp-dev.ps1 -Task image`.
Build manual: `docker build --network none --platform linux/amd64 --build-arg
BUILD_ARCH=amd64 --build-arg BUILD_VERSION=0.4.3 -t local-nlu:0.4.3-amd64 addon`.
Para ARM64 use `--platform linux/arm64` e `BUILD_ARCH=aarch64`.
O builder usa digest OCI multi-arquitetura pinado e crates vendorizados.

Com Python 3.11+ e Ruby instalados:
`python3 tools/release-package.py` produz em `target/dist/0.4.3/`:

- `arandu-nlu-addon-0.4.3.zip`: `repository.yaml` e a pasta `addon/` completa.
- `arandu-nlu-integration-0.4.3.zip`: somente `custom_components/local_nlu/`.
- `arandu-nlu-integration.zip`: conteúdo plano da integração, manifesto na raiz,
  formato do consumidor ZIP HACS. A configuração HACS atual instala por fonte;
  este asset serve à release/verificação e à instalação avançada.
- Checksums SHA-256 e `packages.json`.

O empacotador verifica CRC, lista de arquivos, bytes extraídos e versões.
Datas ZIP, ordem, modos Unix e conteúdo textual são normalizados.
`python3 tools/make-store-repo.py --output target/dist/store-0.4.3`
gera uma estrutura de loja isolada com `addon/`, incluindo avisos de licença;
recusa sobrescrever saída não vazia. O GitHub principal já tem essa estrutura.

## Procedimento de versionamento

Atualize os manifestos do add-on/integração, Cargo.toml e ambas as entradas
`local-nlu` dos lockfiles, README e a linha “Versão atual” deste guia.
Após preparar uma versão maior que a registrada, execute
`python3 tools/distribution.py --record`. Isso registra em `release.json`
o hash dos arquivos distribuídos e dos scripts de empacotamento.
Mudanças nesses arquivos sem nova versão falham no gate. Dependências não são
atualizadas por esse comando. Relatórios e números históricos ficam preservados.

Execute gate completo, build e smoke HTTP contextual, gere/verifique os ZIPs,
revise o diff e abra PR para `master`. O workflow de release somente anexa ZIP/hash
a uma release estável **já publicada**; não cria release ou tag, nem faz deploy.
Confira [RELEASES.md](docs/nlu-2.0/RELEASES.md) antes de publicar.

## Instalação manual — avançada/desenvolvimento

Para desenvolvimento offline, extraia `arandu-nlu-integration-0.4.3.zip` no
diretório de configuração HA e reinicie. O manifesto deve ficar em
`/config/custom_components/local_nlu/manifest.json`. O ZIP fixo plano deve ser
extraído **dentro** de `/config/custom_components/local_nlu/`, nunca em `/config`.
Preserve entrada/opções/.storage. Usuários normais instalam e atualizam pelo HACS.

## Checklist de validação em HA real

Ainda não executada nesta entrega. Requer uma instalação HA e entidades reais.

- [ ] Adicionar custom repository no HACS e reconhecer categoria Integration.
- [ ] ARANDU NLU aparece instalável; baixar `master` ou release estável disponível.
- [ ] Verificar `/config/custom_components/local_nlu/manifest.json`, domínio
  `local_nlu`, versão 0.4.3 e ausência de um diretório `custom_components` aninhado.
- [ ] Reiniciar HA sem erro de importação/setup nos logs.
- [ ] Entrada antiga, entry_id, options e seleção no Assist permanecem válidos.
- [ ] Diagnóstico mostra integration_version=service_version=0.4.3 e compatible=true.
- [ ] contextual_enabled=true, protocol=4 e route=/v4/interpret.
- [ ] Consulta simples de entidade exposta/autorizada responde e não chama serviço.
- [ ] Após uma próxima release/commit autorizado, atualização aparece no HACS;
  instalar, reiniciar e conferir nova versão efetiva.
- [ ] Motor permanece atualizável pela Add-on Store independentemente.
