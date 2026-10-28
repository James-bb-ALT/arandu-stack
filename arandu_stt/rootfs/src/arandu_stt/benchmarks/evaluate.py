"""Paired lexical WER/CER evaluation; no acoustic or hardware claims."""
import argparse
import csv
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
from rapidfuzz.distance import Levenshtein

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from contextual_lexical import ContextualResolver


def normalized(text):
    return ' '.join(text.casefold().split())


def errors(expected, actual):
    reference = normalized(expected)
    predicted = normalized(actual)
    return {'word_errors': Levenshtein.distance(reference.split(), predicted.split()),
            'words': len(reference.split()), 'char_errors': Levenshtein.distance(reference, predicted),
            'chars': len(reference)}


def evaluate(resolver, cases, repeats=20):
    rows = []
    latencies = []
    before = {'word_errors': 0, 'words': 0, 'char_errors': 0, 'chars': 0}
    after = dict(before)
    for case in cases:
        raw, expected = case['raw'], case['expected']
        result = resolver.apply(raw)
        timings = []
        for _ in range(repeats):
            start = time.perf_counter()
            resolver.apply(raw)
            timings.append((time.perf_counter()-start)*1000)
        latencies.extend(timings)
        original = errors(expected, raw)
        corrected = errors(expected, result['text'])
        for k in before:
            before[k] += original[k]
            after[k] += corrected[k]
        rows.append({**case, 'corrected': result['text'],
                     'wer_original': original['word_errors']/max(1, original['words']),
                     'wer_corrected': corrected['word_errors']/max(1, corrected['words']),
                     'cer_original': original['char_errors']/max(1, original['chars']),
                     'cer_corrected': corrected['char_errors']/max(1, corrected['chars']),
                     'latency_ms_median': statistics.median(timings),
                     'first_pass_ms': result['correction_ms'],
                     'confidence': result['confidence'], 'low_confidence': result['low_confidence']})
    if not rows:
        raise ValueError('Evaluation corpus is empty')
    ordered = sorted(latencies)
    first_pass = sorted(r['first_pass_ms'] for r in rows)
    return {'environment': {'platform': platform.platform(), 'python': platform.python_version()},
            'scope': 'text post-processing only; fixture corpus; not real audio accuracy',
            'normalization': 'casefold + whitespace; accents and punctuation remain significant',
            'cases': len(rows), 'repeats': repeats, 'stats': resolver.stats(),
            'wer_original': before['word_errors']/max(1, before['words']),
            'wer_corrected': after['word_errors']/max(1, after['words']),
            'cer_original': before['char_errors']/max(1, before['chars']),
            'cer_corrected': after['char_errors']/max(1, after['chars']),
            'regressions': sum(r['wer_corrected'] > r['wer_original'] for r in rows),
            'clean_case_regressions': sum(r['wer_corrected'] > 0 for r in rows if r['raw'] == r['expected']),
            'first_pass_ms_median': statistics.median(first_pass),
            'first_pass_ms_p95': first_pass[math.ceil(.95*len(first_pass))-1],
            'latency_ms_median': statistics.median(latencies),
            'latency_ms_p95': ordered[math.ceil(.95*len(ordered))-1], 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).with_name('cases.jsonl'))
    parser.add_argument('--inventory', type=Path, default=HERE/'tests/fixture_inventory.json')
    parser.add_argument('--config', type=Path, default=HERE/'config/lexical.json')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('results.json'))
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    cases = [json.loads(line) for line in args.cases.read_text(encoding='utf-8').splitlines() if line.strip()]
    resolver = ContextualResolver(args.inventory, HERE/'resources', config_path=args.config)
    report = evaluate(resolver, cases, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    with args.output.with_suffix('.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report['rows'][0]))
        writer.writeheader()
        writer.writerows(report['rows'])
    print(json.dumps({k: v for k, v in report.items() if k not in ('rows', 'stats')}, indent=2))


if __name__ == '__main__':
    main()
