# Android / LineageOS

Escolha do MVP: [brownard/Ava v0.6.2](https://github.com/brownard/Ava/releases/tag/v0.6.2), Apache-2.0, com Alexa local via microWakeWord e transporte ESPHome. Nenhum app novo ou motor de wake word foi criado. O APK nao e redistribuido neste projeto.

1. No Windows, execute `./android/Install-Ava.ps1` para baixar/verificar o APK. Nao acessa aparelhos. Inspecao opcional: `./android/Install-Ava.ps1 -Action Inspect -Serial SEU_SERIAL`.
2. Com USB debugging autorizado na tela, instale: `./android/Install-Ava.ps1 -Action Install -Serial SEU_SERIAL`. Reinstalacao preserva dados; conflito de assinatura causa erro, nunca desinstalacao automatica.
3. Siga [LineageOS](../docs/lineageos.md) para escolher Alexa/pipeline e preparar boot opcional. Celular: [estrategia e limitacoes](../docs/android-phone.md).

Verificacao local: `./android/Test-Installer.ps1` (fixtures ADB, nenhum aparelho). Resultados: [VALIDATION.md](VALIDATION.md).

`ava-release.json` fixa URL, tamanho e SHA256 publicado pela API de releases do autor. Esse checksum confirma integridade relativa a essa fonte; nao e auditoria independente do binario ou assinatura do desenvolvedor. Atualizacao e deliberada: reveja release/codigo e altere o manifesto. Nao troca silenciosamente para `latest`.

**Limite de seguranca:** Ava original abre TCP 6053 sem autenticacao/Noise. Use somente LAN/VLAN confiavel, permitindo conexao ao satelite apenas pelo HA, e alcance satelite → HA para TTS. Nao exponha na Internet ou Wi-Fi publico. Nao e padrao recomendado para telefone.
