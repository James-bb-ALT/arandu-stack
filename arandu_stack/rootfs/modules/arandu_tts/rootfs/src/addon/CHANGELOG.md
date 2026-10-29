## 0.2.0
- Pocket TTS 3.3.0 em CPU/português, perfis `.safetensors` com gravação.
- Laboratório Web acessível por Home Assistant Ingress, métricas e players.
- Preserva Wyoming, Piper, OmniVoice remoto e fallback.
- Pesos gated no Hugging Face com opção `hf_token`.

# Changelog

## 0.1.0 (2026-10-14)

- Servidor Wyoming local, Piper em CPU e streaming de áudio em chunks.
- Importação de modelos ONNX e seleção de vozes.
- Proxy HTTP para voz OmniVoice clonada em GPU externa.
- Fallback local quando o worker GPU estiver indisponível.
- App HAOS, Docker Compose, scripts de dataset, perfil de voz e fine-tuning.
