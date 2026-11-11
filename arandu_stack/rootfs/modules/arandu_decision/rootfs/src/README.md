## Atualização 1.0.1

Política semântica compartilhada: restrições operacionais recusadas antes da carga/inferência; compostos não viram proposta parcial. Nomes registrados, consultas e conversa são tratados pelo contexto. Modelos, embeddings e calibração preservados.

## Atualização 1.0.0

Ação explícita + alvo único são resolvidos sem carregar E5, sem inventar confiança numérica. Erro de grafia inequívoco é hipótese que exige confirmação; nenhuma ação é enviada antes de “sim”. O HA revalida ação, alvo, catálogo e permissões. Empates genuínos continuam sem execução.

O score semântico passou de mínimo para média dos cossenos, evitando platôs que empatavam intenções distintas. Usa um lote de até três consultas e cache limitado de 128 textos; descritores e modelo continuam residentes. Calibração anterior pertence a outro algoritmo e não autoriza execução: `arandu-joint-v2` / `embedding-v2` exigem nova calibração rotulada para aceitação semântica automática >=80%. Resolução explícita e confirmação funcionam sem essa calibração. Não há limiar reduzido nem confiança fabricada.

Com pesos instalados, o serviço pré-carrega E5 antes de escutar HTTP e expõe `preload_ms`; importação/carga ainda têm custo no início do módulo. Resolução explícita não exige esse modelo. Veja medições e limites em `docs/INTELLIGENCE-v6.0.0.md` na raiz. Pesos não são incluídos no pacote.

# Arandu Decision

Pacote Python independente, versão 1.0.1. Não importa o NLU nem o outro pacote. Serviço passivo: recebe catálogo autorizado completo do Home Assistant e devolve proposta; credenciais, autorização e execução ficam na integração HA.

Modelo padrão: intfloat/multilingual-e5-small (MIT), CPU, vetores normalizados com prefixos query/passage. Downloads somente com comando explícito, revisão fixada no código. Pesos não acompanham o pacote.

```powershell
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/arandu-decision install
.venv/Scripts/arandu-decision serve
```

Para CPU, instale torch==2.9.1 pelo índice https://download.pytorch.org/whl/cpu antes do pacote. Não há calibração de fábrica: similaridade não é acurácia ou confiança. Sem calibração válida o serviço se abstém de aceitar ações.

Com confiança conjunta calibrada de pelo menos 80%, margem mínima 0,08 e alvo inequívoco, a integração executa a ação inferida sem exigir reconhecimento literal do verbo. O limite é inclusivo; configurações antigas abaixo de 0,80 usam 0,80 efetivo. Negação, ação literal conflitante, permissões e validação final continuam protegidas. O LLM mantém a confirmação de hipóteses incertas.

HTTP loopback :11557: GET /status e POST /infer, cabeçalho X-Arandu-Lab: 1, sem Origin. Inferência: JSON {action: "decision", text: "...", catalog: {areas: [...], entities: [...]}}. Campos de entidade: entity_id, name, domain, actions, aliases, area_id, attributes.available; áreas: area_id, names. Limites: 2 MiB, 8192 entidades, 2048 áreas, texto 512 caracteres. Catálogo excedente é recusado inteiro. Cada resposta inclui fingerprint e quantidade de entidades, além de tempos e uso de memória quando disponíveis.

Ações adicionais: load, unload, calibrate (train/validation disjuntos; regras do protocolo Arandu v1).

O AI Lab detecta o serviço pela porta. Pacote ausente, serviço parado ou pesos ausentes: componente indisponível, sem fallback escondido. Os demais modelos experimentais permanecem no Lab. Nenhum serviço consulta a base HA diretamente ou recebe token HA.

Contratos wire v1 extraídos do Lab, mantidos localmente para independência de instalação. Código Apache-2.0; licenças dos pesos preservadas. Testes de contrato em tests/; execute com `python -m unittest discover -s tests`. Benchmarks reais continuam nos botões do AI Lab. A alteração de execução automática foi validada com testes de contrato e integração simulada; não representa calibração do modelo instalado nem teste no Home Assistant real.
