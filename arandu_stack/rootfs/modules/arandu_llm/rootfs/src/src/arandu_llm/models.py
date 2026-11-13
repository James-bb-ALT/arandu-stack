"""Pinned public GGUF registry. Explicit staging install; inference is offline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import math
import urllib.request

MODELS = {
    'tucano':{'name':'Tucano 2 Qwen 0.5B Instruct Q4_K_M','kind':'llm','license':'Apache-2.0',
        'repo':'2DTi/tucano2-0.5b-instruct-gguf','revision':'cbb5318b66a49e20967623e946365bcc8594c29a',
        'file':'tucano2-0.5b-instruct-Q4_K_M.gguf','size':305603296,
        'sha256':'49f16e9d27db128a39a3d976271a297f1ef183b763c62ca9a0c0ba7c72c55c91',
        'runtime':'llama.cpp / CPU','thinking':False,'experimental':False},
    'qwen3':{'name':'Qwen3 0.6B Q8_0 · experimental','kind':'llm','license':'Apache-2.0',
        'repo':'Qwen/Qwen3-0.6B-GGUF','revision':'23749fefcc72300e3a2ad315e1317431b06b590a',
        'file':'Qwen3-0.6B-Q8_0.gguf','size':639446688,
        'sha256':'9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031',
        'runtime':'llama.cpp / CPU','thinking':True,'experimental':True},
    'lfm2.5-1.2b':{'name':'LFM2.5-1.2B-Instruct QAD-Q4_0','kind':'llm','license':'LFM Open License',
        'repo':'LiquidAI/LFM2.5-1.2B-Instruct-GGUF','revision':'8ed288026e23958ad9dfa92d53ed773a8eee7125',
        'file':'LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf','size':695755488,
        'sha256':'bb741ebb106d543e9de114b843a3d3d73d51c74b5801e69da2abde821a0cb3e1',
        'runtime':'llama.cpp / CPU','thinking':False,'experimental':False},
    'lfm3-1.7b':{'name':'LFM3-1.7B QAD-Q4_0','kind':'llm','license':'LFM Open License',
        'repo':'LiquidAI/LFM3-1.7B-GGUF','revision':'cd0c74c575b5c416f3011b5b4c298077f26ca8c2',
        'file':'LFM3-1.7B-QAD-Q4_0.gguf','size':1049615680,
        'sha256':'43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452',
        'runtime':'llama.cpp / CPU','thinking':False,'experimental':False},
    'qwen3-1.7b':{'name':'Qwen3-1.7B Q4_K_M','kind':'llm','license':'Apache-2.0',
        'repo':'bartowski/Qwen_Qwen3-1.7B-GGUF','revision':'dcb19155b962dbb6389f4691a982043a8e651022',
        'file':'Qwen_Qwen3-1.7B-Q4_K_M.gguf','size':1282439584,
        'sha256':'72c5c3cb38fa32d5256e2fe30d03e7a64c6c79e668ad84057e3bd66e250b24fb',
        'runtime':'llama.cpp / CPU','thinking':True,'experimental':True},
}

ALIASES = {'qwen3-0.6b': 'qwen3'}


def resolve_model_key(key):
    return ALIASES.get(key, key)


def validate_generation(value):
    if type(value) is not dict or not set(value)<={'threads','context_size','max_tokens','temperature'}:
        raise ValueError('Configuração de geração inválida')
    for k,v in value.items():
        if k=='temperature':
            if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1:raise ValueError('Temperatura inválida')
        elif type(v) is not int or (k=='threads' and not 1<=v<=4) or (k=='context_size' and v not in (2048,4096,8192)) or (k=='max_tokens' and not 64<=v<=512):
            raise ValueError('Limite de geração inválido')
    return dict(value)


def verify(path, info):
    if path.stat().st_size!=info['size']:raise ValueError('Tamanho GGUF divergente')
    h=hashlib.sha256()
    with path.open('rb') as f:
        if f.read(4)!=b'GGUF':raise ValueError('Arquivo não é GGUF')
        f.seek(0)
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    if h.hexdigest()!=info['sha256']:raise ValueError('Integridade GGUF divergente')
    return path


def installed(root,key):
    key=resolve_model_key(key)
    info=MODELS[key];path=Path(root)/key/info['file']
    return path.is_file() and path.stat().st_size==info['size']


def model_path(root,key):
    key=resolve_model_key(key)
    if key not in MODELS:raise ValueError('Modelo LLM desconhecido')
    return verify(Path(root)/key/MODELS[key]['file'],MODELS[key])


def install(root,key='tucano'):
    key=resolve_model_key(key)
    if key not in MODELS:raise ValueError('Modelo LLM desconhecido')
    info=MODELS[key];root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if installed(root,key):
        try:
            model_path(root,key)
            return {'installed':True,'model':key,'verified':True}
        except (ValueError,OSError):pass  # Explicit install may recover corrupted weights.
    if shutil.disk_usage(root).free<info['size']+64*1024*1024:raise ValueError('Espaço insuficiente para staging GGUF')
    with tempfile.TemporaryDirectory(prefix='llm-staging-',dir=root) as temporary:
        stage=Path(temporary);path=stage/info['file']
        url=f"https://huggingface.co/{info['repo']}/resolve/{info['revision']}/{info['file']}"
        with urllib.request.urlopen(url,timeout=30) as response,path.open('wb') as file:
            size=0;deadline=time.monotonic()+300
            while block:=response.read(1024*1024):
                if time.monotonic()>deadline:raise TimeoutError('Download GGUF excedeu 300 s')
                size+=len(block)
                if size>info['size']:raise ValueError('Download excedeu tamanho pinado')
                file.write(block)
            file.flush();os.fsync(file.fileno())
        verify(path,info)
        (stage/'arandu-model.json').write_text(json.dumps({k:info[k] for k in ('repo','revision','license','sha256','size')}),encoding='utf-8')
        destination=root/key;destination.mkdir(exist_ok=True)
        os.replace(path,destination/info['file'])
        os.replace(stage/'arandu-model.json',destination/'arandu-model.json')
    return {'installed':True,'model':key,'verified':True}
