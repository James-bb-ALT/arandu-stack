# Arandu TTS 0.2.1

Síntese completa antes da reprodução no Home Assistant. Recarregue a integração
Wyoming após atualizar. Maior espera inicial pode evitar pausas entre frases.

Wyoming TCP `10212` e laboratório Web usando **Ingress** (Abrir interface web).

- Piper: `voice: pt_BR-faber-medium` (modelo baixado automaticamente).
- Pocket TTS: `pocket_enabled: true`, `pocket_threads: 2`, `pocket_preload: false`.
- Para acessar o modelo Pocket sujeito a aceite: autorize o repositório `kyutai/pocket-tts` no Hugging Face, gere um token de leitura e informe `hf_token` nas opções.
- Na interface, pré-carregue Pocket, compare a voz Rafael com Piper, envie/grave uma voz própria com autorização e crie o perfil `.safetensors`.
- O microfone no navegador requer HTTPS ou localhost. Se estiver em HTTP LAN, use upload de áudio.
- Depois de instalar uma voz, recarregue Wyoming no Home Assistant para atualizar a lista.
- As vozes e cache ficam em `/share/arandu_tts/`.

Veja o README e `docs/POCKET_TTS.md` no repositório completo.
