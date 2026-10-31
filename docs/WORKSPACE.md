# Onde está cada parte do Arandu

Abra **Arandu.code-workspace** para trabalhar no produto. O Explorer mostra uma
única raiz chamada Arandu, independentemente do nome da pasta externa.

## Três nomes, três funções

- `E:/Pycharm Projects/Arandu Stack`: diretório de trabalho da máquina, fora do Git.
- `arandu-stack/`: repositório publicado, com histórico próprio, app e integração.
- `arandu-stack/arandu_stack/`: pasta do app Home Assistant; `arandu_stack` é o slug
  usado pelo Supervisor, Docker e rotas de instalação. Não é outro checkout.

Não juntar essas três pastas por movimentação cega: os scripts, o Supervisor e os
ambientes locais têm referências reais a esses caminhos. O workspace fornece uma
entrada única sem mudar essas referências.

## Fonte oficial do produto

| Local no repositório | Conteúdo |
| --- | --- |
| `arandu_stack/app/` | WebUI e gestão dos processos |
| `custom_components/local_nlu/` | Integração HACS e AI Lab |
| `arandu_stack/rootfs/modules/` | Fontes dos seis motores distribuídos no app |
| `examples/esphome/` | Pacotes e exemplos para satélites |
| `docs/design/` | Brief, identidade, relatório e capturas |
| `tools/` | Ferramentas de desenvolvimento, incluindo sincronização da marca |

## Pastas irmãs já existentes nesta máquina

`Arandu NLU` tem seu próprio `.git`, contratos de desenvolvimento e ambiente Python
usado pelas checagens locais. `Arandu Decision` e `Arandu LLM` contêm distribuições
independentes antigas; não são a fonte ativa do app. `Arandu STT`, `Arandu TTS` e
`Arandu Wake` incluem projetos de treinamento/experimentação e artefatos locais.
`Arandu_Wake_Dataset_Generation` contém dados, caches de modelos, estado e um venv;
a parte distribuída do gerador já está em
`arandu_stack/rootfs/modules/arandu_wake/rootfs/dataset_generation/`.

Essas pastas não foram apagadas nem fundidas no repositório: contêm dados e
ambientes que não são duplicatas descartáveis. Mover um venv no Windows pode
invalidar caminhos absolutos. Novas alterações do produto devem acontecer na
fonte oficial acima, evitando manter mais cópias manuais.

O brief visual foi retirado de `examples/esphome/` e consolidado em
`docs/design/BRIEF.md`. O PNG fornecido foi incorporado aos ativos oficiais em
`arandu_stack/app/static/assets/brand/`, com original preservado e derivados
reproduzíveis por `tools/sync_brand_assets.py`. As cópias de marca da integração
HACS são geradas a partir dessa fonte: não editar as duas manualmente.

Os launchers externos antigos continuam compatíveis com seus projetos locais;
não são instaladores nem fazem parte do runtime publicado.
