# Arandu Decision

Pacote Python independente, versão 0.1.0. Não importa o NLU nem o outro pacote. Serviço passivo: recebe catálogo autorizado completo do Home Assistant e devolve proposta; credenciais, autorização e execução ficam na integração HA.

Modelo padrão: intfloat/multilingual-e5-small (MIT), CPU, vetores normalizados com prefixos query/passage. Downloads somente com comando explícito, revisão fixada no código. Pesos não acompanham o pacote.

```powershell
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/arandu-decision install
.venv/Scripts/arandu-decision serve
```

Para CPU, instale torch==2.9.1 pelo índice https://download.pytorch.org/whl/cpu antes do pacote. Não há calibração de fábrica: similaridade não é acurácia ou confiança. Sem calibração válida o serviço se abstém de aceitar ações.

HTTP loopback :11557: GET /status e POST /infer, cabeçalho X-Arandu-Lab: 1, sem Origin. Inferência: JSON {action: "decision", text: "...", catalog: {areas: [...], entities: [...]}}. Campos de entidade: entity_id, name, domain, actions, aliases, area_id, attributes.available; áreas: area_id, names. Limites: 2 MiB, 8192 entidades, 2048 áreas, texto 512 caracteres. Catálogo excedente é recusado inteiro. Cada resposta inclui fingerprint e quantidade de entidades, além de tempos e uso de memória quando disponíveis.

Ações adicionais: load, unload, calibrate (train/validation disjuntos; regras do protocolo Arandu v1).

O AI Lab detecta o serviço pela porta. Pacote ausente, serviço parado ou pesos ausentes: componente indisponível, sem fallback escondido. Os demais modelos experimentais permanecem no Lab. Nenhum serviço consulta a base HA diretamente ou recebe token HA.

Contratos wire v1 extraídos do Lab, mantidos localmente para independência de instalação. Tuning e expansão de ações ficam para próximas versões. Código Apache-2.0; licenças dos pesos preservadas. Testes de contrato em tests/; execute manualmente com `python -m unittest discover -s tests`. Benchmarks reais continuam nos botões do AI Lab. Testes não executados nesta alteração, por solicitação do usuário.
