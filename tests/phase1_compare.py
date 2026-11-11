"""Technical before/after HTTP microbenchmark; approved requirements, not speech gold."""
import argparse
import json
import math
from pathlib import Path
import platform
import statistics
import urllib.request

PHRASES = [
    'Liga a sala', 'Liga todos os dispositivos da sala', 'Pode acender a luz da sala?',
    'Queria a luz da sala acesa', 'Dá pra desligar o ventilador?',
    'Liga a luz da sala e desliga o ventilador do quarto',
    'Apaga a luz da sala e do quarto', 'Liga a TV e depois apaga a luz',
    'Jamais ligue o ventilador', 'Nem pense em abrir o portão',
    'Desliga tudo, menos a TV', 'Sem desligar a TV, apaga a luz',
    'Se estiver quente, liga o ar', 'Liga a luz e não desliga o ventilador',
]


def post(endpoint, path, body):
    request = urllib.request.Request(endpoint + path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def measure(endpoint, catalog, repetitions):
    post(endpoint, '/v4/catalog', catalog)
    result = []
    for text in PHRASES:
        payload = dict(version=4, generation=catalog['generation'], text=text,
                       origin_area='quarto', origin_area_source='satellite')
        post(endpoint, '/v4/interpret', payload)
        responses = [post(endpoint, '/v4/interpret', payload) for _ in range(repetitions)]
        timings = {}
        for key in responses[-1].get('timings', {}):
            values = sorted(row['timings'].get(key, 0) for row in responses)
            timings[key] = {'p50_ms': statistics.median(values),
                            'p95_ms': values[math.ceil(len(values) * .95) - 1]}
        response = responses[-1]
        result.append({'text': text, 'status': response['status'], 'reason': response.get('reason'),
                       'operations': response.get('operations', []), 'timings': timings})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--current', required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.repetitions <= 100:
        parser.error('repetitions must be 5..100')
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    report = {'provenance': 'User-approved requirements / FIXTURE_TECNICA, not a linguistic accuracy corpus',
              'hardware': 'AMD Ryzen 7 5700X; Docker Linux on Windows, not i5-6500T',
              'host': platform.system(), 'entities': len(catalog['entities']),
              'repetitions_per_phrase': args.repetitions,
              'baseline': measure(args.baseline, catalog, args.repetitions),
              'current': measure(args.current, catalog, args.repetitions)}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
