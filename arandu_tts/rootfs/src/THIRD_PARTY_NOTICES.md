# Avisos de terceiros

- **Piper (`piper-tts` 1.8.0)**: OHF-Voice, GPL-3.0-or-later. https://github.com/OHF-Voice/piper1-gpl
- **Wyoming (`wyoming` 1.10.2)**: biblioteca de protocolo; verifique a licença do pacote distribuído. https://github.com/OHF-Voice/wyoming
- **Voz padrão (`pt_BR-faber-medium`)**: baixada sob demanda a partir do catálogo Piper oficial, com licença específica no cartão do modelo. https://huggingface.co/rhasspy/piper-voices
- **OmniVoice**: biblioteca e pesos podem ter licenças distintas; os pesos pesquisados no checkpoint oficial são assinalados como CC-BY-NC 4.0. https://github.com/k2-fsa/OmniVoice
- **OmniVoice Studio**: software opcional, não redistribuído neste projeto. https://github.com/youfuxu/OmniVoice-Studio

Nenhum modelo de voz, checkpoint de treinamento, gravação de usuário ou perfil clonado está incluído no arquivo do repositório.

## Pocket TTS (Kyutai)
- Código e API: https://github.com/kyutai-labs/pocket-tts (ver licença no repositório).
- Modelo de voz PT e pesos: https://huggingface.co/kyutai/pocket-tts (CC-BY-4.0, controle de acesso e termos de uso).
- Python `pocket-tts==3.3.0` e PyTorch CPU são instalados na imagem, os pesos são baixados separadamente.
- `rafael` é perfil de voz fornecido no catálogo da Kyutai; verificar sua licença específica antes de redistribuir.
