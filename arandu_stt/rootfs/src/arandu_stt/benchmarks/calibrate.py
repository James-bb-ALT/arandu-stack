"""Optional isotonic calibration from independent human-labelled lexical decisions.

Input JSONL: {"confidence": 0.7, "correct": true}. Use a separate held-out file
to report Brier loss; this artifact is not silently activated in production.
"""
import argparse
import json
from pathlib import Path


def fit(rows):
    if len(rows) < 20 or {bool(r['correct']) for r in rows} != {True, False}:
        raise ValueError('Need at least 20 labelled decisions, including successes and errors')
    grouped = {}
    for row in rows:
        value = float(row['confidence'])
        if not 0 <= value <= 1 or type(row['correct']) is not bool:
            raise ValueError('Labels require confidence in [0,1] and boolean correct')
        positive, total = grouped.get(value, (0, 0))
        grouped[value] = (positive + int(row['correct']), total + 1)
    blocks = []
    for value, (positive, total) in sorted(grouped.items()):
        blocks.append({'low': value, 'high': value, 'positive': positive, 'count': total})
        while len(blocks) > 1 and blocks[-2]['positive']/blocks[-2]['count'] > blocks[-1]['positive']/blocks[-1]['count']:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append({'low': left['low'], 'high': right['high'],
                           'positive': left['positive']+right['positive'], 'count': left['count']+right['count']})
    return [{'low': x['low'], 'high': x['high'], 'probability': x['positive']/x['count'], 'count': x['count']} for x in blocks]


def predict(blocks, score):
    return min(blocks, key=lambda b: max(b['low']-score, score-b['high'], 0))['probability']


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.train.resolve() == args.validation.resolve():
        parser.error('Training and validation must be separate files')
    training, validation = read(args.train), read(args.validation)
    blocks = fit(training)
    if not validation:
        parser.error('Validation set is empty')
    # Validation uses the same strict schema without training on validation labels.
    for row in validation:
        if type(row.get('correct')) is not bool or not 0 <= float(row['confidence']) <= 1:
            parser.error('Invalid validation label')
    brier_raw = sum((float(r['confidence'])-r['correct'])**2 for r in validation)/len(validation)
    brier_calibrated = sum((predict(blocks, float(r['confidence']))-r['correct'])**2 for r in validation)/len(validation)
    result = {'method': 'isotonic_PAV', 'training_count': len(training), 'validation_count': len(validation),
              'brier_raw': brier_raw, 'brier_calibrated': brier_calibrated, 'blocks': blocks,
              'scope': 'lexical decision correctness only; dataset-dependent; not acoustic confidence'}
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'blocks'}))


if __name__ == '__main__':
    main()
