"""Explicit, pinned individual installs. No network from any inference path."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import urllib.request
import uuid
from .contracts import MODELS, selection, digest


def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def verify(directory,key):
    info=MODELS[key]
    for name,meta in info['files'].items():
        path=directory/name
        if not path.is_file() or path.stat().st_size!=meta['size'] or file_hash(path)!=meta['sha256']:
            raise ValueError('Integridade Decision divergente: '+name)
    return directory


def installed(root,key):
    return all((Path(root)/key/name).is_file() and (Path(root)/key/name).stat().st_size==meta['size']
        for name,meta in MODELS[key]['files'].items())


def signature(key,backend):
    selection(key,backend)
    from .backends import backend_signature
    return digest({'model':key,'manifest':MODELS[key],'implementation':'embedding-multimodel-v1',
        'backend':backend_signature(backend)})


def install(root,key='e5-small'):
    selection(key);root=Path(root);root.mkdir(parents=True,exist_ok=True);info=MODELS[key]
    destination=root/key
    if installed(root,key):
        try:
            verify(destination,key)
            return {'installed':True,'verified':True,'model':key,'disk_bytes':info['disk_bytes']}
        except (ValueError,OSError):pass
    if shutil.disk_usage(root).free<info['disk_bytes']+64*1024*1024:raise ValueError('Espaço insuficiente para staging')
    deadline=time.monotonic()+900
    with tempfile.TemporaryDirectory(prefix='decision-staging-',dir=root) as tmp:
        stage=Path(tmp)
        for name,meta in info['files'].items():
            path=stage/name;path.parent.mkdir(parents=True,exist_ok=True)
            url=f"https://huggingface.co/{info['repo']}/resolve/{info['revision']}/{name}"
            with urllib.request.urlopen(url,timeout=30) as response,path.open('wb') as output:
                size=0
                while block:=response.read(1024*1024):
                    if time.monotonic()>deadline:raise TimeoutError('Instalação Decision excedeu 900 s')
                    size+=len(block)
                    if size>meta['size']:raise ValueError('Arquivo excedeu tamanho pinado')
                    output.write(block)
                output.flush();os.fsync(output.fileno())
        verify(stage,key)
        (stage/'arandu-model.json').write_text(json.dumps({'version':2,'repo':info['repo'],'revision':info['revision'],
            'license':info['license'],'manifest_fingerprint':digest(info)}),encoding='utf-8')
        backup=root/(key+'-previous-'+uuid.uuid4().hex)
        if destination.exists():destination.rename(backup)
        try:stage.rename(destination)
        except BaseException:
            if backup.exists():backup.rename(destination)
            raise
        if backup.exists():shutil.rmtree(backup)
    return {'installed':True,'verified':True,'model':key,'disk_bytes':info['disk_bytes']}
