"""Import supplied datasets once; runtime uses only the packaged canonical versions."""
import csv
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'arandu_stt'
SOURCE = ROOT.parent / 'New artifacts'
DEST = APP / 'datasets'


def main():
    records = {}
    for group, name in [('household', 'arandu_household_entities_ptbr.csv'),
                        ('commands', 'arandu_comandos_comuns_ptbr_v2.csv'),
                        ('lexicon', 'arandu_lexico_generico_palavras_ptbr.csv')]:
        target = DEST / group / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, target)
        records[group] = {'file': str(target.relative_to(DEST)).replace('\\', '/'),
                          'origin': 'user_supplied', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    names = DEST / 'names' / 'arandu_nomes_comuns_brasil.csv'
    names.parent.mkdir(parents=True, exist_ok=True)
    supplied = SOURCE / names.name
    if supplied.is_file():
        shutil.copyfile(supplied, names)
        origin = 'user_supplied'
    else:
        with names.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['nome_canonico', 'variantes_graficas', 'variantes_componentes', 'apelidos_comuns'])
            seen = set()
            for name in (APP / 'resources' / 'common_names_ptbr.txt').read_text(encoding='utf-8').splitlines():
                if name.strip() and name.strip() not in seen:
                    seen.add(name.strip())
                    writer.writerow([name.strip(), '', '', ''])
        origin = 'fallback_from_resources/common_names_ptbr.txt; requested CSV not supplied'
    records['names'] = {'file': str(names.relative_to(DEST)).replace('\\', '/'), 'origin': origin,
                        'sha256': hashlib.sha256(names.read_bytes()).hexdigest()}
    (DEST / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
