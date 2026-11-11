"""Explicit CPU or Intel GPU. GPU never uses AUTO, HETERO or CPU fallback."""
from importlib import metadata
from functools import lru_cache
import json
from pathlib import Path
import time
from .contracts import MODELS, digest

OPENVINO='2024.6.0'
CONVERTER='torch-onnx14-eager-mean-pooling-v1'


@lru_cache(maxsize=16)
def version(package):
    try:return metadata.version(package)
    except metadata.PackageNotFoundError:return 'unavailable'


def gpu_probe():
    report={'id':'gpu_intel','available':False,'validated':False,'status':'unsupported','devices':[],
        'render_nodes':[str(p) for p in Path('/dev/dri').glob('renderD*')], 'runtime':version('openvino')}
    if report['runtime']!=OPENVINO:
        return {**report,'reason':'OpenVINO '+OPENVINO+' não instalado; sem fallback CPU'}
    try:
        import openvino as ov
        core=ov.Core();devices=[d for d in core.available_devices if d=='GPU' or d.startswith('GPU.')]
        report['devices']=devices
        if not devices:return {**report,'reason':'OpenVINO não detectou GPU Intel: verifique dispositivo, permissões e OpenCL legado; nenhum driver do host será alterado'}
        name=core.get_property('GPU','FULL_DEVICE_NAME')
        try:driver=core.get_property('GPU','GPU_DRIVER_VERSION')
        except RuntimeError:driver=None
        return {**report,'available':True,'status':'not_tested','device_name':name,'driver':driver,
            'reason':'Dispositivo enumerado; pronto somente após compilar e inferir modelo em GPU'}
    except Exception as exc:return {**report,'reason':'Runtime GPU indisponível: '+type(exc).__name__}


def backend_signature(backend):
    if backend=='cpu':return {'backend':'cpu','precision':'fp32','pooling':'sentence-transformers',
        'torch':version('torch'),'sentence-transformers':version('sentence-transformers'),'transformers':version('transformers'),'tokenizers':version('tokenizers')}
    probe=gpu_probe()
    return {'backend':'gpu_intel','precision':'fp32','converter':CONVERTER,'openvino':probe['runtime'],
        'device':probe.get('device_name'),'driver':probe.get('driver'),'transformers':version('transformers'),
        'tokenizers':version('tokenizers'),'torch_export':version('torch'),'onnx':version('onnx')}


class BackendUnavailable(ValueError):pass


class EmbeddingBackend:
    def encode(self,texts,**kwargs):raise NotImplementedError
    def close(self):pass


class CPUBackend(EmbeddingBackend):
    def __init__(self,directory,key,threads):
        from sentence_transformers import SentenceTransformer
        self.model=SentenceTransformer(str(directory),device='cpu',local_files_only=True,trust_remote_code=False)
        self.model.max_seq_length=MODELS[key]['max_length']
        self.model.eval()
        self.dimension=MODELS[key]['dimension'];self.metrics={};self.effective='cpu'

    def encode(self,texts,**kwargs):
        import torch
        import numpy as np
        tokenize=embed=0.;vectors=[]
        for start in range(0,len(texts),32):
            before=time.perf_counter();features=self.model.tokenize(texts[start:start+32]);tokenize+=time.perf_counter()-before
            before=time.perf_counter()
            with torch.inference_mode():
                out=self.model(features)['sentence_embedding']
                out=torch.nn.functional.normalize(out,p=2,dim=1)
                vectors.append(out.cpu().float().numpy())
            embed+=time.perf_counter()-before
        self.metrics={'tokenization_ms':tokenize*1000,'embedding_ms':embed*1000}
        return np.concatenate(vectors) if vectors else np.empty((0,self.dimension),dtype=np.float32)


def ir_path(root,key):return Path(root)/key/'openvino'/digest({'manifest':MODELS[key],'converter':CONVERTER,'runtime':OPENVINO,
    'torch':version('torch'),'onnx':version('onnx'),'transformers':version('transformers')})[:24]


def verify_ir(root,key):
    from .installation import file_hash
    directory=ir_path(root,key);manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('source')!=digest(MODELS[key]) or manifest.get('converter')!=CONVERTER or manifest.get('runtime')!=OPENVINO:
        raise ValueError('Conversão OpenVINO incompatível')
    for name in ['model.xml','model.bin']:
        if file_hash(directory/name)!=manifest['hashes'][name]:raise ValueError('Integridade IR divergente')
    return directory


def prepare_ir(root,key):
    """Explicit offline export, never called from infer/load. All artifacts hash-bound."""
    import tempfile,os,gc,uuid,shutil
    import openvino as ov
    import torch
    from transformers import AutoModel,AutoTokenizer
    from .installation import verify,file_hash
    if version('openvino')!=OPENVINO:raise BackendUnavailable('Versão OpenVINO incompatível')
    torch.set_num_threads(2)
    directory=verify(Path(root)/key,key);destination=ir_path(root,key)
    try:verify_ir(root,key);return {'prepared':True,'converter':CONVERTER}
    except (OSError,ValueError,KeyError):pass
    destination.parent.mkdir(parents=True,exist_ok=True)
    tokenizer=AutoTokenizer.from_pretrained(str(directory),local_files_only=True,trust_remote_code=False)
    model=AutoModel.from_pretrained(str(directory),local_files_only=True,trust_remote_code=False,attn_implementation='eager').eval()
    class Hidden(torch.nn.Module):
        def __init__(self,base):super().__init__();self.base=base
        def forward(self,input_ids,attention_mask):return self.base(input_ids=input_ids,attention_mask=attention_mask).last_hidden_state
    example=tokenizer(['passage: teste do Arandu'],return_tensors='pt')
    with tempfile.TemporaryDirectory(prefix='ir-staging-',dir=destination.parent) as tmp:
        stage=Path(tmp);onnx=stage/'model.onnx'
        with torch.no_grad():
            torch.onnx.export(Hidden(model),(example['input_ids'],example['attention_mask']),str(onnx),dynamo=False,
                input_names=['input_ids','attention_mask'],output_names=['last_hidden_state'],opset_version=14,
                dynamic_axes={'input_ids':{0:'batch',1:'sequence'},'attention_mask':{0:'batch',1:'sequence'},'last_hidden_state':{0:'batch',1:'sequence'}})
        converted=ov.convert_model(str(onnx));ir=stage/'ir';ir.mkdir()
        ov.save_model(converted,str(ir/'model.xml'),compress_to_fp16=False)
        receipt={'source':digest(MODELS[key]),'converter':CONVERTER,'runtime':OPENVINO,
            'torch':version('torch'),'onnx':version('onnx'),'hashes':{n:file_hash(ir/n) for n in ['model.xml','model.bin']}}
        (ir/'manifest.json').write_text(json.dumps(receipt),encoding='utf-8')
        backup=destination.with_name(destination.name+'-previous-'+uuid.uuid4().hex)
        if destination.exists():destination.rename(backup)
        try:ir.rename(destination)
        except BaseException:
            if backup.exists():backup.rename(destination)
            raise
        if backup.exists():shutil.rmtree(backup)
    del model,converted;gc.collect()
    verify_ir(root,key)
    return {'prepared':True,'converter':CONVERTER}


class OpenVINOGPUBackend(EmbeddingBackend):
    def __init__(self,root,key,threads):
        import openvino as ov
        from transformers import AutoTokenizer
        probe=gpu_probe()
        if not probe['available']:raise BackendUnavailable(probe['reason'])
        try:directory=verify_ir(root,key)
        except (OSError,ValueError,KeyError) as exc:raise BackendUnavailable('IR ausente ou inválido. Use Preparar GPU antes de aplicar; sem fallback CPU') from exc
        self.tokenizer=AutoTokenizer.from_pretrained(str(Path(root)/key),local_files_only=True,trust_remote_code=False)
        self.max_length=MODELS[key]['max_length'];self.dimension=MODELS[key]['dimension'];self.metrics={}
        core=ov.Core()
        try:self.compiled=core.compile_model(str(directory/'model.xml'),'GPU',{'INFERENCE_PRECISION_HINT':'f32','PERFORMANCE_HINT':'LATENCY','NUM_STREAMS':'1'})
        except RuntimeError as exc:
            import logging
            logging.getLogger('arandu.decision').exception('Falha de compilação GPU explícita')
            raise BackendUnavailable('OpenVINO recusou o modelo no target GPU. Driver/dispositivo/IR incompatível; consulte os logs. Sem fallback CPU') from exc
        devices=list(self.compiled.get_property('EXECUTION_DEVICES'))
        if not devices or any(not d.startswith('GPU') for d in devices):raise BackendUnavailable('Runtime não confirmou execução exclusiva em GPU')
        self.devices=devices;self.effective='gpu_intel';self.device_name=probe.get('device_name')
        self.encode(['teste do Arandu']) # numerical smoke is required before promotion/readiness

    def encode(self,texts,**kwargs):
        import numpy as np
        vectors=[];tokenize=embed=0.
        for start in range(0,len(texts),32):
            before=time.perf_counter()
            data=self.tokenizer(texts[start:start+32],padding=True,truncation=True,max_length=self.max_length,return_tensors='np')
            tokenize+=time.perf_counter()-before;before=time.perf_counter()
            outputs=self.compiled({n:data[n] for n in ['input_ids','attention_mask']})
            hidden=np.asarray(outputs[self.compiled.output(0)],dtype=np.float32)
            mask=np.asarray(data['attention_mask'],dtype=np.float32)[...,None]
            pooled=(hidden*mask).sum(axis=1)/np.maximum(mask.sum(axis=1),1e-9)
            pooled/=np.maximum(np.linalg.norm(pooled,axis=1,keepdims=True),1e-12)
            if pooled.shape!=(len(data['input_ids']),self.dimension) or not np.isfinite(pooled).all():raise ValueError('Embedding GPU inválido')
            vectors.append(pooled);embed+=time.perf_counter()-before
        self.metrics={'tokenization_ms':tokenize*1000,'embedding_ms':embed*1000}
        return np.concatenate(vectors)


def create_backend(root,key,backend,threads):
    return CPUBackend(Path(root)/key,key,threads) if backend=='cpu' else OpenVINOGPUBackend(root,key,threads)
