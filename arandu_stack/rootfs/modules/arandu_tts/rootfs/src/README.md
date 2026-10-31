# Arandu TTS 0.2.1 — Piper + Pocket TTS + Laboratório Web

Home Assistant aguarda a fala completa antes de reproduzir para evitar pausas
de geração durante respostas longas. Após atualizar, recarregue a integração
Wyoming para renovar as capacidades. Pode aumentar a espera inicial.

Servidor **Wyoming** para Home Assistant, preservando a versão 0.1.0 e adicionando:

- **Piper TTS** em CPU: vozes ONNX, resposta rápida e fallback.
- **Pocket TTS 3.3.0** em CPU: modelo PT, voz `rafael` e vozes clonadas de gravações de áudio.
- **Web app**: texto compartilhado, dois players de áudio, comparação de TTFB/tempo total/RTF, gravação no navegador ou upload e criação de perfil `.safetensors`.
- Mantém **worker GPU OmniVoice** opcional da versão 0.1.0, sem modificá-lo.

> **Status de validação**: código Python e testes automatizados com motores simulados, conversão com FFmpeg. A instalação completa dos pesos reais e a execução em HAOS/i5 físico ainda **não foram verificadas**. Não foram medidos benchmark real, qualidade da voz ou RAM. Nenhum peso, gravação ou voz clonada foi incluído nos ZIPs.

## Arquitetura

```text
Home Assistant Assist ─── Wyoming :10212 ───┬── Piper (.onnx)
                                           ├── Pocket TTS (modelo PT, voz Rafael, .safetensors)
                                           └── OmniVoice remoto (GPU opcional, legado 0.1.0)

Navegador ── HTTP 8099 / HA Ingress ────────┤
                   laboratório de voz        ├── compara Piper x Pocket / arquivos WAV
                                           └── grava / envia referência → FFmpeg → perfil Pocket
```

## Instalação Home Assistant OS

1. Abra `Arandu_TTS_Addon_HAOS_v0.2.0.zip` e copie o **conteúdo da pasta `addon/`** para `/addons/arandu_tts/`. O resultado deverá conter `/addons/arandu_tts/config.yaml`, `/addons/arandu_tts/Dockerfile`, `/addons/arandu_tts/run.sh` e `/addons/arandu_tts/app/arandu_tts/`.
2. Em **Configurações → Aplicativos (Complementos) → Loja → ⋮ → Recarregar**, procure **Arandu TTS** e instale/atualize. A imagem contém PyTorch CPU e FFmpeg, portanto o **build inicial é significativamente maior e mais lento** que o app Piper isolado.
3. Se for usar Pocket TTS, abra https://huggingface.co/kyutai/pocket-tts , aceite as condições de acesso do modelo e gere um token de leitura. Coloque-o na opção **`hf_token`** do app (campo de senha). Veja [`docs/POCKET_TTS.md`](docs/POCKET_TTS.md).
4. Inicie o aplicativo. Para comparar os motores, clique em **Abrir interface web** (acesso por **Ingress** dentro do Home Assistant, sem abrir porta adicional).
5. Na interface, clique **Pré-carregar modelo**, escolha uma voz Piper e **pocket-rafael**, escreva uma frase e use **Gerar e comparar ambos**.
6. Para usar na automação de voz, adicione a integração **Wyoming Protocol** apontando para `IP_DO_HOME_ASSISTANT:10212`. Em **Assistentes de voz**, escolha o Arandu TTS como saída; escolha uma das vozes disponíveis. O STT/NLU existentes não mudam.

**Padrões**: Piper `pt_BR-faber-medium`, `cpu_threads: 2`, Pocket `portuguese`, `pocket_threads: 2`, `pocket_preload: false`, `pocket_fallback: true`, Web `8099` via Ingress e Wyoming `10212` na LAN. Piper é instalado/baixado automaticamente na primeira execução; Pocket baixa o modelo quando você o inicia no laboratório (cache preservado na pasta compartilhada).

**Nota**: o aplicativo pode continuar usando Piper mesmo se o token/pesos Pocket estiverem inacessíveis. Caso use a voz Pocket como padrão do Wyoming, se o Pocket falhar **antes do áudio começar**, haverá tentativa de fallback para Piper configurado. O Piper precisa estar baixado para o fallback funcionar.

## Docker Compose / Linux

Requer Docker Compose, conexão à Internet no primeiro build e pesos disponíveis para download.

```bash
unzip Arandu_TTS_Repositorio_v0.2.0.zip
cd arandu-tts
cp .env.example .env
# Preencha HF_TOKEN no .env após aceitar acesso aos pesos no Hugging Face
mkdir -p voices hf-cache
docker compose up -d --build
```

- Web local: **http://localhost:8099** (por segurança mapeado apenas em `127.0.0.1`).
- Wyoming: `IP_DO_SERVIDOR:10212`.
- Para acessar o Web app em outro computador, prefira um túnel SSH (`ssh -L 8099:127.0.0.1:8099 usuario@servidor`) ou proxy autenticado. Não exponha um endpoint que salva áudios pessoais sem autenticação.
- Pode ativar token adicional definindo `ARANDU_WEB_TOKEN` no `.env` (a UI tem um campo para fornecê-lo). Sem token, mantenha a ligação HTTP restrita ao localhost.

## Clonar voz pela interface

1. Grave um clipe de **5–15 segundos** com voz limpa, preferencialmente em PT-BR.
2. Abra a seção **Adicionar voz personalizada** e defina identificador, por exemplo `arandu_custom`.
3. Grave pela opção de microfone (em HTTPS/localhost) ou envie WAV/MP3/M4A/OGG/WebM/FLAC.
4. Confirme autorização para usar essa voz e clique **Salvar perfil**.
5. O sistema converte para WAV PCM16 mono a 24kHz, extrai o estado do Pocket TTS e salva `pocket/arandu_custom.safetensors` e `pocket/arandu_custom.wav` sob o diretório persistente das vozes.
6. Selecione **pocket-arandu_custom** e gere um texto. Para que ela apareça no Home Assistant, pode ser necessário recarregar Wyoming.

Clonar pelo Pocket **não treina nem produz** `.onnx` Piper. Para criar modelo Piper em sua voz, mantenha o pipeline de dataset e treinamento descrito em [`docs/TRAINING.md`](docs/TRAINING.md). Os perfis `.pt` do Voice Studio OmniVoice **também não podem ser importados diretamente** no Pocket.

## Medições e limitações

- O botão **Comparar ambos** gera **Piper e Pocket em sequência**, evitando concorrência direta durante o teste.
- `primeiro chunk`: tempo no servidor até o primeiro fragmento PCM, incluindo carregamento se frio.
- `total`: tempo no servidor até o WAV completo.
- `RTF`: tempo total / duração do áudio; menor é melhor.
- O navegador recebe um arquivo WAV completo; a reprodução **não é streaming em tempo real**, embora o backend Wyoming transmita chunks PCM progressivos.
- Gravação direta exige contexto seguro (`https://` ou `localhost` no navegador). Em acesso simples `http://IP:8123`, envie um arquivo gravado externamente.
- O Pocket possui português, mas não há confirmação de qualidade especificamente para **PT-BR no seu servidor**. A voz inicial Rafael pode ter sotaque diferente do desejado.
- A RAM adicional (PyTorch + pesos Pocket) compete com Home Assistant, STT e NLU no seu i5/8GB; desative Pocket se afetar os serviços existentes.

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `arandu_tts/engine.py` | Dispatcher Piper/Pocket/remote e PCM chunks |
| `arandu_tts/pocket.py` | Modelo PT, streaming e exportação de perfil |
| `arandu_tts/web.py` | REST + WAV + upload, segurança, preparação da voz |
| `arandu_tts/static/index.html` | Interface HTML/CSS/JS sem build de frontend |
| `arandu_tts/server.py` | Wyoming + Web em um único serviço |
| `addon/` | App HAOS com Ingress + Dockerfile |
| `docs/POCKET_TTS.md` | Tutorial Pocket, Hugging Face, microfone e endpoints |
| `docs/VOICE_STUDIO.md` | Alternativa OmniVoice na RTX 3080 |
| `tools/` | Preparação de dataset, treinamento, benchmark e sonda Wyoming |
| `tests/` | Testes que não exigem baixar pesos |

### Teste local sem baixar modelos

```bash
python -m pip install pytest pytest-asyncio aiohttp pyyaml numpy
python -m pytest -q
python tools/sync_addon.py
```

### Fontes

- [Pocket TTS oficial](https://github.com/kyutai-labs/pocket-tts)
- [Pocket TTS API oficial](https://github.com/kyutai-labs/pocket-tts/blob/main/docs/API%20Reference/python-api.md)
- [Modelo português](https://raw.githubusercontent.com/kyutai-labs/pocket-tts/main/pocket_tts/config/portuguese.yaml)
- [Home Assistant Ingress](https://developers.home-assistant.io/docs/apps/presentation/)
- [Home Assistant Wyoming](https://www.home-assistant.io/integrations/wyoming)
- [Piper](https://github.com/OHF-Voice/piper1-gpl)

## Licenças e consentimento

O código Arandu está sob GPL-3.0-or-later. Os **pesos Pocket TTS** constam sob CC-BY-4.0 e condições de uso do Kyutai, conforme página do Hugging Face. Pesos, perfis de voz e bibliotecas de terceiros têm condições independentes. Clonagem exige autorização explícita do titular da voz.
