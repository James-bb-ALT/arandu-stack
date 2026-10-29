# Verificacoes Android — 2026-10-14

| Verificacao executada | Resultado | Alcance |
|---|---|---|
| `android/Test-Installer.ps1` | 5 checks passaram | Parser PowerShell; ADB simulado API 33; recusa serial ausente; recusa API 25; recusa APK corrompido. Nenhum aparelho. |
| `android/Install-Ava.ps1` sem argumentos | Passou | Download HTTPS real de Ava v0.6.2, tamanho 34.314.878 bytes e SHA256 `28faf6b32c79d67e5bde909245294bca9fab54687438c771412f090b239cea83` conferidos. Cache em TEMP do Windows. |
| ZIP do APK baixado | Passou | `assets/wakeWords/alexa.json` e `alexa.tflite` presentes; `libmicrofeatures.so` para arm64-v8a, armeabi-v7a, x86 e x86_64. Nao executa o modelo. |
| `bash -n scripts/android/ava-boot.sh` | Passou | Sintaxe via Git Bash; nao iniciou hook. |
| `bash -n scripts/android/install-ava-boot.sh` | Passou | Sintaxe via Git Bash; nao instalou no Termux. |
| Auditoria de fonte pinada Ava v0.6.2 | Executada | Manifest, AudioRecord, VoiceInput, VoicePipeline, VoiceAssistant, autoStart e locks. Evidencia estatica; nao certificacao de estabilidade. |

Os testes podem ser repetidos com `./android/Test-Installer.ps1`. Download requer Internet; testes de seguranca usam fixtures em TEMP e nao acessam a rede nem ADB real. Nao houve build Android, emulador, instalacao real, gravacao, inferencia Alexa, chamada telefonica, TTS, reconexao, boot ou medicao CPU/RAM. Esses casos permanecem pendentes de hardware em `docs/lineageos.md`.
