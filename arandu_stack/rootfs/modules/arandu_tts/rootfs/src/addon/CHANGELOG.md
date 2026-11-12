## 0.2.2

- Fila ajustável e buffer PCM limitado com backpressure, ordem preservada e cancelamento sem misturar vozes.
- Limite manual de threads ONNX Runtime disponível como opção experimental desligada por padrão: reduziu CPU, mas piorou a latência de frases longas no Ryzen. Primeira saída do protocolo é medida separadamente de playback físico, que não é observado.
- Mantém síntese integral no Assist; não anuncia streaming físico validado nem correção comprovada de cortes no satélite.

## 0.2.1

- Home Assistant recebe a fala completa antes da reprodução. Desativa o anúncio de entrada incremental para evitar que a geração das próximas frases esvazie o buffer do satélite. O protocolo incremental continua aceito por clientes existentes.
- Pode aumentar a espera inicial; recarregue a integração Wyoming após atualizar para renovar as capacidades do motor. Testes de contrato aprovados; validação de falas longas no aparelho ainda necessária.

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
