"""Deterministic broad synthetic evaluation, stratified by supplied command domain.

References come from the same corpus used for vocabulary/context. This is a
stress test, not a held-out acoustic validation set or a claim of real WER.
"""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random
import re
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from contextual_lexical.normalizer import norm


def generate(path, count=400, seed=20261006):
    rng = random.Random(seed)
    groups = defaultdict(list)
    seen = set()
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            expected = row['frase_exemplo'].strip()
            if norm(expected) not in seen:
                groups[row['dominio']].append(expected)
                seen.add(norm(expected))
    for examples in groups.values():
        rng.shuffle(examples)
    samples = []
    domains = sorted(groups)
    while len(samples) < count and domains:
        for domain in list(domains):
            if not groups[domain]:
                domains.remove(domain)
                continue
            samples.append((domain, groups[domain].pop()))
            if len(samples) == count:
                break
    rows = []
    token = re.compile(r'[^\W\d_]+(?:-[^\W\d_]+)*', re.UNICODE)
    for index, (domain, expected) in enumerate(samples):
        options = [m for m in token.finditer(expected) if len(m.group()) >= 5]
        if not options:
            raw, operation = expected, 'unchanged_no_eligible_token'
        else:
            chosen = rng.choice(options)
            word = chosen.group()
            position = rng.randrange(1, len(word)-1)
            operation = ('delete', 'transpose', 'substitute')[index % 3]
            if operation == 'delete':
                damaged = word[:position]+word[position+1:]
            elif operation == 'transpose':
                positions = [p for p in range(1, len(word)-1) if word[p] != word[p+1]]
                position = rng.choice(positions) if positions else position
                damaged = word[:position]+word[position+1]+word[position]+word[position+2:]
                if damaged == word:
                    damaged = word[:position]+word[position+1:]
                    operation = 'delete'
            else:
                damaged = word[:position]+rng.choice([v for v in 'aeiou' if v != word[position]])+word[position+1:]
            raw = expected[:chosen.start()]+damaged+expected[chosen.end():]
        rows.append({'category': 'synthetic_' + domain + '_' + operation, 'raw': raw, 'expected': expected})
        rows.append({'category': 'clean_' + domain, 'raw': expected, 'expected': expected})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commands', type=Path, default=HERE/'datasets/commands/arandu_comandos_comuns_ptbr_v2.csv')
    parser.add_argument('--count', type=int, default=400)
    parser.add_argument('--seed', type=int, default=20261006)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('generated_cases.jsonl'))
    args = parser.parse_args()
    args.output.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in generate(args.commands, args.count, args.seed)), encoding='utf-8')


if __name__ == '__main__':
    main()
