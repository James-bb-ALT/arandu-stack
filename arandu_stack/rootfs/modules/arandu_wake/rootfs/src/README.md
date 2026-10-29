# Arandu Wake

MVP de configuração e diagnóstico para usar **Alexa** com o Assist e os motores Arandu existentes. Criado em uma pasta independente da stack; STT, NLU e TTS não foram modificados.

**Estado: implementação local testada, implantação nos dispositivos pendente.** O teste real do motor detectou Alexa numa amostra pública via Wyoming. Isso não comprova o fluxo completo no Home Assistant ou nos aparelhos. Veja [verificações](reports/verification.md).

## Começar no Windows

Na pasta `arandu-wake`, execute:

```powershell
.\scripts\windows\Setup.ps1 -WithDetector
.\scripts\windows\Test-Local.ps1 -WithDetector
```

O primeiro comando cria `.venv` e instala ferramentas e o detector upstream. O segundo executa testes locais, incluindo áudio público com checksum e testes simulados do instalador Android. Nenhum desses comandos instala firmware ou escreve no HA.

Os 9 testes simulados do instalador HAOS exigem GNU Bash e jq; se não estiverem no PATH, informe `ARANDU_BASH` e `ARANDU_JQ` conforme o [relatório](reports/verification.md). Na ausência dessas ferramentas, esses testes mostram skip explícito. A execução final desta entrega teve 37 testes Python e 5 verificações PowerShell aprovados, além da compilação ESPHome completa.

Para configurar seu inventário:

```powershell
Copy-Item config/satellites.example.json config/local.json
.\.venv\Scripts\python.exe -m arandu_wake validate --config config/local.json
.\.venv\Scripts\python.exe -m arandu_wake doctor --config config/local.json
```

Edite `config/local.json` com os nomes, cômodos e entidades reais. `sensitivity` é inventário: o valor precisa ser aplicado no detector correspondente. Estado de conexão/escuta vem de entidades HA explicitamente mapeadas; `null` aparece como `unknown`, nunca como conectado. Logs e latência vêm dos diagnósticos, não de valores fabricados no inventário.

## Implantação por plataforma

| Plataforma | Caminho implementado | Validação que ainda falta |
|---|---|---|
| HAOS amd64 | Aplicativo oficial openWakeWord + Wyoming + pipeline existente | Autenticação, modelo anunciado na instalação, seleção Alexa e teste completo |
| ReSpeaker Lite | YAML completo candidato XIAO ESP32S3, modo central padrão e microWakeWord Alexa alternativo | Identificar placa, obter YAML real, comparar recursos, validar áudio e recuperação antes de flash |
| Echo Dot 2 / EchoMuse | Perfil e procedimento para recursos já existentes | Conferir versão instalada, pipeline, mute e TTS |
| Echo Show / LineageOS | APK Ava fixado, instalador ADB e início opcional por Termux:Boot | ROM, microfone, boot, tela apagada, Wi-Fi e permissões |
| Celular Android | Ava opcional em LAN; Companion por gesto como fallback | Alexa no Companion não documentada; bateria/chamadas exigem avaliação |
| Linux / Wyoming | Instalador legado fixado, serviço systemd e reconexão | Projeto arquivado; instalação e áudio reais pendentes |

Instruções: [Home Assistant](docs/home-assistant.md), [ReSpeaker](docs/respeaker-lite.md), [EchoMuse](docs/echomuse.md), [LineageOS](docs/lineageos.md), [celular](docs/android-phone.md), [Linux](echomuse/README.md).

## Diagnóstico

```powershell
# Detector local opcional, primeiro terminal:
.\scripts\windows\Start-Detector.ps1
# Segundo terminal: o modelo Alexa precisa constar da resposta:
.\.venv\Scripts\python.exe -m arandu_wake wyoming --uri tcp://127.0.0.1:10400
# WAV fornecido explicitamente: PCM16, mono, 16 kHz, até 120 segundos:
.\.venv\Scripts\python.exe -m arandu_wake wyoming --wav C:\audio\alexa.wav --expect detection
```

Para consultar o HA, forneça `HA_TOKEN` somente pelo ambiente da sessão e execute:

```powershell
.\.venv\Scripts\python.exe -m arandu_wake ha-check --config config/local.json
```

A consulta usa apenas `assist_pipeline/pipeline/list` e `get_states`. Preencha `expected_engines` com os IDs reais de STT, agente e TTS após conferência; um pipeline chamado Arandu sozinho não comprova os motores. Não grava configurações nem executa comandos de voz. Retornos: `0` verificação satisfeita; `1` erro; `2` requisito ainda não confirmado. Resultado `ready` restringe-se à configuração consultada.

Para uma interação exportada do debug Assist (lista JSON de eventos com `type`, `timestamp`, `data`), execute `python -m arandu_wake metrics arquivo.json`. O resumo descarta falas, tokens e URLs. Não confunde término do pipeline com término da reprodução. Não publique o arquivo de entrada, que pode conter conteúdo sensível.

## Decisões e limites

Não foi criado um novo motor de wake word ou uma integração HA própria. O projeto reúne configurações, instaladores e ferramentas pequenas. [Arquitetura](docs/architecture.md) e [evolução](docs/future-arandu-wake.md) explicam as escolhas.

O HA possui proteção nativa contra ativações duplicadas pela mesma frase. A interoperabilidade depende do que cada cliente envia; `Alexa` e `alexa` podem resultar em chaves diferentes. Arbitragem universal e escolha do microfone mais próximo não foram comprovadas. Não coloque dois satélites próximos em produção antes do teste G.

O Ava selecionado expõe API ESPHome sem autenticação: exige LAN/VLAN confiável. Não encaminhe portas Wyoming/ESPHome para a internet. Nenhum áudio é registrado por padrão por estas ferramentas; a amostra de teste é pública e temporária. Confira também as opções de debug e retenção já existentes no HA.

Antes de aplicar: backup, conferência do estado real e plano de [rollback](docs/rollback.md). Problemas comuns: [diagnóstico](docs/troubleshooting.md). Aceitação física A–J: [procedimento](tests/hardware.md).
