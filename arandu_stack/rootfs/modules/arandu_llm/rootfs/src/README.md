# Arandu LLM

Pacote Python independente, versão 0.1.0. Não importa o NLU nem o outro pacote. Serviço passivo: recebe catálogo autorizado completo do Home Assistant e devolve proposta; credenciais, autorização e execução ficam na integração HA.

Modelo padrão: Polygl0t/Tucano2-qwen-0.5B-Instruct (Apache-2.0), quantização comunitária 2DTi Q4_K_M, llama.cpp CPU. Downloads somente com comando explícito, revisão fixada no código. Pesos não acompanham o pacote.

```powershell
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/arandu-llm install
$env:ARANDU_LLAMA_SERVER = "C:\caminho\llama-server.exe"
.venv/Scripts/arandu-llm serve
```

llama-server é um executável externo obrigatório. Sem Decision, a seleção lexical percorre todos os pares elegíveis do catálogo e envia até oito candidatos ao prompt. Quando o chamador fornece ranking do Decision, ele é usado. O modelo recebe os dispositivos recuperados; o serviço recebe e valida o catálogo inteiro. Não há truncamento dos primeiros dispositivos.

HTTP loopback :11558: GET /status e POST /infer, cabeçalho X-Arandu-Lab: 1, sem Origin. Inferência: JSON {action: "llm", text: "...", catalog: {areas: [...], entities: [...]}}. Campos de entidade: entity_id, name, domain, actions, aliases, area_id, attributes.available; áreas: area_id, names. Limites: 2 MiB, 8192 entidades, 2048 áreas, texto 512 caracteres. Catálogo excedente é recusado inteiro. Cada resposta inclui fingerprint e quantidade de entidades, além de tempos e uso de memória quando disponíveis.

Ação adicional: unload. Processo llama.cpp é encerrado após cada solicitação.

O AI Lab detecta o serviço pela porta. Pacote ausente, serviço parado ou pesos ausentes: componente indisponível, sem fallback escondido. Os demais modelos experimentais permanecem no Lab. Nenhum serviço consulta a base HA diretamente ou recebe token HA.

Contratos wire v1 extraídos do Lab, mantidos localmente para independência de instalação. Tuning e expansão de ações ficam para próximas versões. Código Apache-2.0; licenças dos pesos preservadas. Testes de contrato em tests/; execute manualmente com `python -m unittest discover -s tests`. Benchmarks reais continuam nos botões do AI Lab. Testes não executados nesta alteração, por solicitação do usuário.
