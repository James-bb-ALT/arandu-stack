"""Isolated module entry points inside Arandu Assistant."""
from pathlib import Path
import json
import os
import sys

key = sys.argv[1]
data = Path(os.environ['ARANDU_MODULE_DATA'])
options = json.loads((data / 'options.json').read_text())
sources = Path('/opt/modules')

if key in {'decision', 'llm'}:
    from aiohttp import web
    import importlib
    service = importlib.import_module('arandu_' + key + '.service')
    root = data / 'models'
    root.mkdir(exist_ok=True)
    if options.get('auto_download'):
        service.install(root)
    web.run_app(service.create_app(root, '/usr/local/bin/llama-server' if key == 'llm' else None, options['threads']), host='127.0.0.1', port=service.PORT, access_log=None)
elif key == 'tts':
    from arandu_tts.addon_main import configure
    from arandu_tts.server import main
    configure(options)
    os.environ.update(ARANDU_WEB_ENABLED='0', ARANDU_VOICES_DIR=str(data / 'voices'), HF_HOME=str(data / 'hub'), XDG_CACHE_HOME=str(data / 'cache'))
    main()
elif key == 'stt':
    root = sources / 'arandu_stt/rootfs/src/arandu_stt'
    sys.path.insert(0, str(root))
    import launcher
    if not options.get('auto_download'):
        variant = 'int8' if options['model_variant'] == 'original' else launcher.ADAPTED_VERSION
        if not list((data / 'models' / variant).glob('*.onnx')):
            raise SystemExit('Pesos STT ausentes: habilite auto_download nas opções e reinicie o módulo.')
    launcher.main()
elif key == 'wake':
    os.execv(sys.executable, [sys.executable, '-m', 'wyoming_openwakeword', '--uri', 'tcp://0.0.0.0:10400',
        '--threshold', str(options['threshold']), '--trigger-level', str(options['trigger_level'])])
else:
    raise SystemExit('Módulo desconhecido')
