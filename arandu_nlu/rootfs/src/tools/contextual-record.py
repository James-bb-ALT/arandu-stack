#!/usr/bin/env python3
"""Record measured evidence and assemble a whitelisted local release package."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ['mlp-check', 'mlp-smoke.py', 'mlp-dev.ps1', 'generate-mlp-corpus', 'generate-mlp-corpus.rb', 'check-mlp-package', 'check-mlp-package.py', 'materialize-mlp-vendor.py', 'import-stt-corpus.py', 'contextual-evaluate.py', 'contextual-benchmark.py', 'contextual-image-smoke.py', 'contextual-record.py', 'distribution.py', 'release-package.py', 'make-store-repo.py']


def digest():
    files = sorted([*ROOT.joinpath('addon/engine/src').rglob('*.rs'), *ROOT.joinpath('addon/engine/tests').rglob('*.rs'), *ROOT.joinpath('addon/engine/examples').rglob('*.rs'), *ROOT.joinpath('custom_components/local_nlu').rglob('*.py'), *ROOT.joinpath('tests/mlp').rglob('*.py'), *[ROOT / 'tools' / name for name in TOOLS if name.endswith('.py')], ROOT / 'tools/dev/run.py', ROOT / 'data/contextual/conformance-spec.json', ROOT / 'addon/engine/data/grammar.json', ROOT / 'addon/engine/Cargo.toml', ROOT / 'Cargo.lock'], key=lambda path: path.relative_to(ROOT).as_posix())
    value = hashlib.sha256()
    for path in files:
        value.update(path.relative_to(ROOT).as_posix().encode() + b'\0' + path.read_bytes().replace(b'\r\n', b'\n') + b'\0')
    return value.hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


CATEGORIES = {
    'continuation_required': '1. Exige continuação de conversa',
    'missing_context': '2. Exige contexto ausente',
    'hardware_or_integration': '3. Exige hardware/integração/configuração',
    'confirmation_required': '4. Exige confirmação',
    'inherent_ambiguity': '5. Ambíguo por natureza',
    'safety_refusal': '6. Recusa por segurança',
    'interpretation_failure': '7. Falha de interpretação',
    'planning_failure': '8. Falha de planejamento',
    'executor_failure': '9. Falha do executor',
    'test_limitation': '10. Falha/limitação do teste ou rótulo do corpus',
}


def result_category(case):
    if case['operational_simulated']:
        return 'completed_simulated'
    if case['reason'] in ('missing_calendar_duration', 'missing_media_query'):
        return 'continuation_required'
    if case['intent'] in ('assistant.confirm', 'satellite.device_here_off'):
        return 'missing_context'
    if case['runtime_status'] == 'confirmation_required':
        return 'confirmation_required'
    if case['nlu_status'] == 'cancel':
        return 'safety_refusal'
    if case['intent'] in ('clock_alarm.dismiss', 'media.stop') and case['nlu_status'] == 'no_match':
        return 'inherent_ambiguity'
    if case['reason'] == 'forecast_date_unavailable' or (case['intent'] == 'presence.person_area.query' and case['reason'] == 'no_compatible_capability'):
        return 'test_limitation'
    if case['nlu_status'] == 'no_match' or not case['slots_correct'] or not case.get('targets_correct', True):
        return 'interpretation_failure'
    if case['runtime_status'] in ('execution_failed', 'partial_failure'):
        return 'executor_failure'
    if case['nlu_status'] == 'invalid_request':
        return 'planning_failure'
    return 'hardware_or_integration'


def record(args):
    coverage = json.loads((ROOT / 'target/contextual-coverage.json').read_text(encoding='utf-8'))
    core = json.loads((ROOT / 'target/contextual-benchmark.json').read_text(encoding='utf-8'))
    http = json.loads((ROOT / 'target/contextual-http-benchmark.json').read_text(encoding='utf-8'))
    inventory = json.loads((ROOT / 'data/contextual/stt-inventory.json').read_text(encoding='utf-8'))
    source_hash = digest()
    categories = Counter()
    for case in coverage['cases_detail']:
        case['result_category'] = result_category(case)
        categories[case['result_category']] += 1
    coverage['outcome_categories'] = dict(sorted(categories.items()))
    for intent, row in coverage['intents'].items():
        row['outcome_categories'] = dict(sorted(Counter(case['result_category'] for case in coverage['cases_detail'] if case['intent'] == intent).items()))
    summary = {
        'inventoried': len(inventory['intents']),
        'recognized_at_least_one_case': sum(row['linguistic'] > 0 for row in coverage['intents'].values()),
        'planned_at_least_one_case': sum(row.get('planning', 0) > 0 for row in coverage['intents'].values()),
        'completed_at_least_one_simulated_case': sum(row['operational_simulated'] > 0 for row in coverage['intents'].values()),
        'explicit_conditional_dependencies': sum(bool(item.get('external_dependency')) for item in inventory['intents']),
        'all_cases_without_simulated_completion': sum(row['operational_simulated'] == 0 for row in coverage['intents'].values()),
        'with_real_interpretation_failures': sum(row['outcome_categories'].get('interpretation_failure', 0) > 0 for row in coverage['intents'].values()),
    }
    coverage['intent_summary'] = summary
    for data in (coverage, core, http):
        data['source_digest'] = source_hash
    for name, data in [('coverage.json', coverage), ('benchmark-core.json', core), ('benchmark-http.json', http)]:
        write_json(ROOT / 'data/contextual' / name, data)
    lines = []
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(['intent', 'family', 'action', 'domains', 'expected_parameters', 'dependency', 'adapter', 'cases', 'linguistic', 'semantic_with_checked_slots_targets', 'valid_plans', 'operational_simulated', 'status', 'reasons', 'outcome_categories'])
    for item in inventory['intents']:
        row = coverage['intents'][item['intent_id']]
        status = 'configured_adapter; simulated' if item['adapter'] == 'configured_binding' else 'complete internal conformance; live HA pending' if row['operational_simulated'] == row['cases'] else 'partial/conditional; see reasons and dialogue tests'
        writer.writerow([item['intent_id'], item['family'], item['semantic']['operation'], '|'.join(item['semantic']['domains']), '|'.join(item['expected_parameters']), item.get('external_dependency') or '', item['adapter'], row['cases'], row['linguistic'], row['semantic'], row.get('planning', 0), row['operational_simulated'], status, '|'.join(row['reasons']), json.dumps(row['outcome_categories'], sort_keys=True)])
        lines.append(f"| {item['intent_id']} | {row['cases']} | {row['linguistic']} | {row['semantic']} | {row.get('planning', 0)} | {row['operational_simulated']} |")
    (ROOT / 'data/contextual/coverage.csv').write_text(buffer.getvalue(), encoding='utf-8', newline='')
    gate = (ROOT / 'target/nlu-2.0-check.log').read_text(encoding='utf-8', errors='replace') if (ROOT / 'target/nlu-2.0-check.log').exists() else ''
    gate_pass = args.gate_pass and 'MLP CHECK PASS' in gate and f'SOURCE DIGEST {source_hash}' in gate
    if args.gate_pass and not gate_pass:
        raise SystemExit('A completed standard gate log is required')
    validations = {}
    for name in ('image', 'package'):
        path = ROOT / 'data/contextual' / f'{name}-validation.json'
        value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        validations[name] = value.get('source_digest') == source_hash and value.get('status') == 'PASS'
    ready = gate_pass and all(validations.values())
    python_tests = max((int(value) for value in re.findall(r'Ran (\d+) tests', gate)), default=0)
    rust_tests = sum(int(value) for value in re.findall(r'test result: ok\. (\d+) passed', gate))
    counts = coverage['totals']
    category_rows = '\n'.join(f"| {label} | {categories[key]} |" for key, label in CATEGORIES.items())
    e2e_tests = len(re.findall(r'^    async def test_', (ROOT / 'tests/mlp/test_contextual.py').read_text(encoding='utf-8').split('class EndToEndTests', 1)[1], re.MULTILINE))
    bench_rows = []
    for native, transport in zip(core['catalogs'], http['catalogs'], strict=True):
        nt = native['phases']['total_ms']
        ht = transport['phases']['observed_rust_http_executor_ms']
        bench_rows.append(f"| {native['entities']} | {nt['p50_ms']:.4f} | {nt['p95_ms']:.4f} | {nt.get('p99_ms', 0):.4f} | {ht['p50_ms']:.4f} | {ht['p95_ms']:.4f} | {ht.get('p99_ms', 0):.4f} | {native['catalog_compile_p50_ms']:.2f} | {native['process_rss']} |")
    imperfect = [f"- `{name}`: {row['operational_simulated']}/{row['cases']} execuções simuladas; motivos: {', '.join(row['reasons']) or 'abstenção/ambiguidade/checagem semântica; veja cases_detail'}." for name, row in coverage['intents'].items() if row['operational_simulated'] != row['cases']]
    report = f"""# Relatório NLU 0.3.2 — motor contextual 2.0

Implementação contextual aditiva em Rust/Python, versões anteriores preservadas e STT inalterado. Fingerprint normalizado das fontes executáveis: `{source_hash}`. Trabalho no checkout local; nenhum commit, push ou implantação numa residência foi efetuado.

## A. Implementação

Motor contextual determinístico, aliases semânticos e capacidades dinâmicas; números PT-BR, cores, modos, parâmetros relativos, planos compostos, consultas e agregações. Sessões limitadas, esclarecimentos, confirmação e cancelamento. Adaptadores fechados para os domínios HA inventariados; Music Assistant, remotes e scripts exigem backend/configuração reais.

## B. Arquitetura

STT/texto → normalização e intenção/slots Rust → contexto → resolução entidade/capacidade → plano leitura/controle → executor Python HA → resposta. O catálogo autorizado é compilado no Python e indexado no Rust; a validação e execução ficam exclusivamente no Python. v4 é aditivo a v1/v2/v3; `contextual_enabled=false` restaura a seleção anterior.

## D. Qualidade

- Baseline: 67 testes Python e 83 testes Rust passaram antes da extensão. O gate completo inicial encontrou CRLF e catálogos Phase B ausentes no staging; ambos foram corrigidos.
- Gate final: {'PASS' if gate_pass else 'aguardando registro final'}. {'Testes únicos Python: ' + str(python_tests) + '; testes Rust: ' + str(rust_tests) + '.' if gate_pass else 'Consulte o log após concluir tools/mlp-check.'} O segundo passe contextual usa Rust release real via HTTP e o executor Python. Os 20 exemplos congelados da missão são exercitados no Rust, além das contraprovas de segurança e diálogo.
- O pacote inclui build offline e fontes vendorizadas. Imagem scratch amd64 é verificada separadamente. ARM64 não foi executado neste host.
- APIs: fonte pública HA Core `2ee7b99063feb4b48e4faa0a9dece14998d0876f` e documentação oficial. Nenhuma instância residencial HA estava disponível: execução física, frontend e integrações reais continuam pendentes de ensaio no destino.

| Verificação | Resultado |
|---|---|
| Python tests | {python_tests if gate_pass else 'registro final pendente'} únicos |
| Rust tests | {rust_tests if gate_pass else 'registro final pendente'} |
| Integration tests | {e2e_tests} E2E Rust HTTP + Python, incluídos na suíte |
| Security tests | Contraprovas/regressões incluídas; ver REVIEWS.md |
| Corpus evaluation | {coverage['cases']} casos reavaliados |
| Formatting / Lint | {'PASS: cargo fmt / clippy -D warnings' if gate_pass else 'registro final pendente'} |
| Docker build / smoke | {'PASS; amd64 real' if validations['image'] else 'verificação final pendente'}; ver image-validation.json |
| ZIP validation | {'PASS; extraído e testes executados' if validations['package'] else 'verificação final pendente'}; ver package-validation.json |

## C. Cobertura


Fonte MIT STT `{inventory['provenance']['commit']}`: 1.946 registros CSV, 165 intenções e {inventory['provenance']['families']} valores de família (`dominio`), em vez da estimativa de 45. As quatro entradas CSV têm SHA-256 no arquivo de proveniência. Templates são parte do motor; esta avaliação é conformidade interna, não acurácia independente.

| Camada | Casos | Percentual |
|---|---:|---:|
| Reconhecimento linguístico/abstenção tipada | {counts['linguistic']} / {coverage['cases']} | {counts['linguistic'] / coverage['cases'] * 100:.2f}% |
| Semântica, slots/alvos conferidos quando há oracle independente | {counts['semantic']} / {coverage['cases']} | {counts['semantic'] / coverage['cases'] * 100:.2f}% |
| Planos v4 tipados válidos | {counts.get('planning', 0)} / {coverage['cases']} | {counts.get('planning', 0) / coverage['cases'] * 100:.2f}% |
| Execução com executor real e HA simulado | {counts['operational_simulated']} / {coverage['cases']} | {counts['operational_simulated'] / coverage['cases'] * 100:.2f}% |

`coverage.json` guarda resultados por caso e intenção, ações, parâmetros, slots verificados e motivos. `coverage.csv` conecta todas as 165 intenções a família, parâmetros, domínios, dependência e estado medido. Não se considera esclarecimento uma execução. O cenário tem entidades relevantes e recursos simulados; catálogos mistos/ambiguidade/permissões são testados separadamente. Bindings usam script simulado com variáveis explicitamente mapeadas. Transferência usa origem distinta recente; localização interna usa tracker explícito. Confirmação isolada sem pendência e pronome sem contexto abstêm.

Calendário e Spotify possuem testes de múltiplos turnos que executam após obter duração ou conteúdo; suas frases incompletas do corpus continuam marcadas sem execução. Previsões ausentes no retorno simulado não são fabricadas. Rótulos do corpus também podem conflitar com a semântica: “alguém” não identifica uma pessoa para localização individual.

Resumo por intenção: {summary['inventoried']} inventariadas; {summary['recognized_at_least_one_case']} reconhecidas em ao menos um caso; {summary['planned_at_least_one_case']} com plano em ao menos um caso; {summary['completed_at_least_one_simulated_case']} com fluxo concluído em simulação; {summary['explicit_conditional_dependencies']} com dependência condicional explícita; {summary['all_cases_without_simulated_completion']} sem conclusão isolada; {summary['with_real_interpretation_failures']} com falha real de interpretação. Esses conjuntos se sobrepõem, não são categorias mutuamente exclusivas. Todos exigem entidades/serviços disponíveis e autorização no destino.

Conformidade operacional inclui cancelamentos corretos, que não são chamadas de controle. Semântica também inclui controles de conversa sem plano; por isso sua contagem pode exceder a de planos. O código não considera reconhecimento textual suficiente para execução.

| Categoria dos casos restantes | Casos |
|---|---:|
{category_rows}

As duas consultas afirmativas `device.status` não reconhecidas são falhas de interpretação registradas, não recusas esperadas. Os três casos com “alguém” rotulados como localização individual usam fixture incompatível; os quinze forecasts futuros não existem no backend simulado: são limitações do teste, não demonstrações de funcionamento real. “Para de tocar” é ambíguo entre mídia e alarme. CSV/JSON preservam classificação por caso e intenção.

## E. Performance

CPU observada: {http['environment']['cpuinfo']}. Ambiente: {http['environment']['platform']}; Python {http['environment']['python']}; Rust 1.98.0 release/musl. Sem downloads na execução. Números não incluem rede doméstica ou latência física de dispositivos.

| Entidades | Rust P50 ms | Rust P95 ms | Rust P99 ms | HTTP + executor P50 ms | HTTP + executor P95 ms | HTTP + executor P99 ms | Catálogo Rust P50 ms | RSS observado |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
{chr(10).join(bench_rows)}

Rust: 100 aquecimentos + 1.000 medições por tamanho; comandos genéricos com preferência explícita. HTTP/executor: 20 aquecimentos + 200 medições, alvo nominal exato e consultas, serviço HA instantâneo simulado. Os workloads são diferentes e não devem ser subtraídos entre si. RSS é do processo ao final de cada tamanho, com catálogos anteriores presentes; não é pico nem previsão para Raspberry Pi. JSONs preservam P50/P95 por normalização, contexto, intenção/slots, resolução, planejamento, validação e execução simulada, além de compilação dos snapshots. Inicialização da gramática/indexação é amortizada; a primeira compilação pode ser mais lenta.

As medições HTTP incluem `rust_http_roundtrip_ms`, que mede a chamada completa com transporte e interpretação, e `validation_executor_response_ms`, separado do total observado. Intenção/slots e execução/resposta são etapas instrumentadas juntas; não se inventa uma separação de custo dentro dessas etapas. P99 também está nos JSONs. STT não está incluído.

## F. Segurança

Revisões e severidades: [REVIEWS.md](REVIEWS.md). Problemas de escopo global, respostas ausentes, opção revogada, parâmetros relativos multialvo e repetição após efeito incerto foram corrigidos com regressões. Entidades indisponíveis permanecem desconhecidas. Avisos musl/biblioteca padrão Rust estão na imagem/pacote. Nenhum CRITICAL/HIGH ou MEDIUM de execução conhecido pendente após as verificações finais; a revisão secundária foi interrompida por limite de uso depois da última lacuna de licença, encerrada pelo executor.

## G. Implantação

[DEPLOYMENT.md](DEPLOYMENT.md) contém pré-requisitos, instalação local, integração, agente Assist, opções, adaptadores, validação, logs e rollback. Nenhum deploy/push foi feito.

## H. Limitações

Não se declara 100% de compreensão de linguagem livre ou execução real das 165 intenções. Condições e exceções abstêm; PIN/código nunca é contornado. Nomes iguais pedem esclarecimento. Não há endpoint genérico para cloud providers, chamada telefônica, canal, impressão 3D ou remoção de calendário. Esses casos exigem adaptadores configurados e contratos reais. Expressões ambíguas como “para de tocar” podem precisar de nome/alias/contexto explícito. Unidades de energia incompatíveis abstêm; conversões implementadas incluem porcentagem/volume, brilho, duração e Kelvin, sem conversão geral de unidades físicas.

Usuário ativo autenticado é obrigatório; contexto sem user_id é negado, inclusive em pipelines que não o forneçam. Origem espacial é obtida dos campos reais ConversationInput; metadados STT adicionais não atravessam o Wyoming inspecionado. Vínculo a tracker interno não é inferido da área cadastral da pessoa.

Casos que ainda não completam a execução simulada isolada:

{chr(10).join(imperfect)}

## I. Alterações

Rust: `addon/engine/src/contextual`, rotas em server/lib e testes/exemplo de benchmark. Python: catálogo/protocolo/executor contextual, capabilities/queries, runtime/conversation/config_flow/init e traduções. Build: versão 0.3.2, Docker, avisos de licença e staging LF. Dados: inventário/proveniência/cobertura/benchmarks. Ferramentas: importação, avaliação, medição, smoke e pacote. Documentos: arquitetura/contratos/implantação/revisões/relatório/checkpoint. Modificações locais anteriores foram preservadas.

## J. Pronto para implantação?

{'READY WITH KNOWN LIMITATIONS' if ready else 'NOT READY'}

{'Gate, imagem e pacote extraído aprovados; implantação deve ser controlada. HA residencial, hardware físico, frontend HA e ARM64 não foram validados; resultados operacionais são de simulador. Recursos condicionais precisam de configuração e identidade do usuário.' if ready else 'Aguardando registro final do gate, imagem e pacote extraído.'}

## Matriz por intenção

L = linguístico; S = semântica/slots/alvos; P = plano válido; E = executor simulado. Adaptador, dependências e categorias estão no CSV/JSON.

| Intenção | Casos | L | S | P | E |
|---|---:|---:|---:|---:|---:|
{chr(10).join(lines)}

Reprodução e rollback: [DEPLOYMENT.md](DEPLOYMENT.md). Contratos e fontes: [API-CONTRACTS.md](API-CONTRACTS.md).
"""
    prefix, quality_and_rest = report.split('## D. Qualidade\n', 1)
    quality, coverage_and_rest = quality_and_rest.split('## C. Cobertura\n', 1)
    coverage_section, performance_and_rest = coverage_and_rest.split('## E. Performance\n', 1)
    report = prefix + '## C. Cobertura\n' + coverage_section + '## D. Qualidade\n' + quality + '## E. Performance\n' + performance_and_rest
    (ROOT / 'docs/nlu-2.0/REPORT.md').write_text(report, encoding='utf-8')
    print(json.dumps({'source_digest': source_hash, 'coverage': counts, 'gate_pass': gate_pass, 'python_tests': python_tests, 'rust_tests': rust_tests}))


def package(destination):
    roots = ['addon', 'custom_components/local_nlu', 'data', 'tests/mlp', 'tools/dev', 'docs/nlu-2.0']
    paths = {path for root in roots for path in (ROOT / root).rglob('*') if path.is_file()}
    paths |= {ROOT / 'tools' / name for name in TOOLS}
    # Existing Rust regression tests read these six frozen, synthetic fixtures.
    for folder, names in {
        'entity-resolution': ['catalog-v1.json', 'corpus-v1.jsonl'],
        'mention-extraction': ['snapshot-v1.json', 'corpus-v1.jsonl'],
        'v2-interpret': ['snapshot-v1.json', 'corpus-v1.jsonl'],
    }.items():
        paths |= {ROOT / 'evaluation/ptbr-independent' / folder / name for name in names}
    paths |= {ROOT / name for name in ['Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml', 'LICENSE', 'README.md', 'INSTALL.md', 'repository.yaml', 'release.json', '.cargo/config.toml', 'docs/adr/0056-contextual-nlu-2.md', 'docs/NLU_2_0_EXECUTION_CHECKPOINT.md']}
    paths = {path for path in paths if not set(path.relative_to(ROOT).parts) & {'target', '__pycache__', '.git', '.storage'} and path.suffix not in ('.pyc', '.log')}
    manifest = {}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(paths):
            name = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            # Rust inner attributes (#![no_std]) are not interpreter shebangs.
            executable = content.startswith((b'#!/', b'#! /'))
            if executable:
                content = content.replace(b'\r\n', b'\n')
            manifest[name] = hashlib.sha256(content).hexdigest()
            info = zipfile.ZipInfo(name, (2026, 10, 6, 0, 0, 0))
            info.create_system = 3  # Preserve Unix executable modes from Windows.
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
        archive.writestr('RELEASE-MANIFEST.json', json.dumps({'version': '0.3.2', 'source_digest': digest(), 'files': manifest}, sort_keys=True, indent=2))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name, expected in manifest.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
    sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(f'{sha}  {destination.name}\n', encoding='ascii')
    print(json.dumps({'package': destination.name, 'files': len(manifest), 'bytes': destination.stat().st_size, 'sha256': sha}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gate-pass', action='store_true')
    parser.add_argument('--fingerprint', action='store_true')
    parser.add_argument('--package', type=Path)
    args = parser.parse_args()
    if args.fingerprint:
        print('SOURCE DIGEST ' + digest())
        raise SystemExit(0)
    record(args)
    if args.package:
        package(args.package)
