# Arandu Stack — pacotes opcionais

| Pacote | Modelo padrão | Serviço |
| --- | --- | --- |
| Arandu NLU | interpretador Rust | 127.0.0.1:11555 |
| Arandu Decision 0.1.0 | Multilingual E5 Small | 127.0.0.1:11557 |
| Arandu LLM 0.1.0 | Tucano 2 Qwen 0.5B Instruct, GGUF Q4_K_M comunitário | 127.0.0.1:11558 |

Decision e LLM são distribuições Python independentes, com pyproject, CLI, código,
licença e wheel próprios. Nenhum importa Home Assistant, NLU ou o outro pacote.
Neste PC foram instalados no ambiente Python do Lab para reutilizar dependências
e pesos existentes. Também aceitam ambientes virtuais separados; consulte cada README.

## Ligação atual

A integração HA monta o snapshot autorizado de entidades, devices, áreas, aliases,
disponibilidade e capacidades. O AI Lab encaminha o catálogo inteiro ao serviço
escolhido. Não há conexão direta dos modelos ao banco HA, nem cópia de credenciais.
O limite de catálogo é 8192 entidades/2048 áreas/2 MiB; exceder gera recusa,
sem selecionar os primeiros dispositivos.

O Decision pontua todos os pares elegíveis. O LLM recebe o catálogo inteiro no
serviço, mas usa uma recuperação de candidatos antes do prompt de 4096 tokens.
Pode consumir o ranking do Decision; sozinho usa seleção lexical sobre o catálogo.
O resultado é sempre uma proposta, revalidada pela integração antes de execução.

A ligação já disponível é o pipeline e os benchmarks do AI Lab, inclusive o painel
nativo do HA. A conversa de produção do NLU permanece em seu interpretador Rust;
esta versão não promove modelos ainda não afinados para execução automática.

O gateway do Lab detecta os serviços em /status. Ausência, processo parado ou
pesos ausentes deixam o modelo indisponível; não há dependência Python obrigatória
do NLU nesses pacotes nem substituição silenciosa do modelo.
Saracura, JATOBÁ, BrazEmbed e Qwen continuam disponíveis para comparação no worker
experimental, separados dos padrões destes dois novos pacotes.

## Neste PC

Execute start-ai-lab.ps1 na raiz e abra http://127.0.0.1:8766.
O script inicia somente pacotes instalados. Não instala serviços do Windows
nem muda a inicialização do computador. Logs e PIDs ficam em Arandu NLU/target.
Pesos existentes em ~/.cache/arandu-ai-lab são reutilizados, sem download.
Não há HA conectado neste PC: catálogo real vazio e ações em dry-run.

Os wheels ficam em Arandu Decision/dist e Arandu LLM/dist.
Para instalar em outro ambiente: pip install <arquivo.whl>, depois o comando
arandu-decision install ou arandu-llm install baixa os pesos fixados.
O LLM exige llama-server externo configurado por --llama-server ou
ARANDU_LLAMA_SERVER.

## Verificação manual

Cada pasta tem Testar contratos.cmd e testes sem inferência.
No AI Lab, Testes prontos executa casos sintéticos com modelos reais e mostra
tempo de carga, tempo de inferência, invocações, acurácia, abstinência e erros.
Os rótulos esperados são fixtures técnicas, não métricas linguísticas reais.

Nesta alteração foram construídos os wheels, instalados os pacotes, iniciados
os serviços e consultados somente status/HTTP da página. Nenhum teste, benchmark,
calibração ou inferência foi executado, conforme pedido.

## Dependências e licenças

Python >=3.11, código dos pacotes Apache-2.0. Dependências diretas fixadas em
pyproject.toml: aiohttp 3.13.3 (Apache-2.0/MIT), psutil 7.2.1 (BSD-3-Clause),
huggingface-hub 0.36.0 (Apache-2.0). Decision acrescenta torch 2.9.1
(BSD-3-Clause), sentence-transformers 5.2.0 e transformers 4.57.3 (Apache-2.0).
O ambiente existente usa torch CPU; os pacotes não incluem binários ou pesos.
Os contratos wire v1 foram extraídos do AI Lab e são mantidos em cada pacote,
sem acoplamento de importação. Alterações futuras exigem compatibilidade do protocolo.
