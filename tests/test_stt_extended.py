"""Eight-model contracts without downloading weights. Real inference is opt-in separately."""
import asyncio
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace, ModuleType
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'arandu_stack/app'))
from modules import ModuleManager
from stt_lab import SOURCE
import model_catalog as catalog
from backends import create_backend

IDS = ['fastconformer','whisper_tiny','whisper_base','whisper_small','parakeet_ptbr_30s','parakeet_tagarela','orukeet_int8','granite_turboctc_q4']


def test_catalog_and_factory_cover_eight_without_importing_native_libraries():
    assert list(catalog.CATALOG) == IDS
    expected = ['FastConformerBackend']+['WhisperBackend']*3+['ParakeetBackend']*2+['OrukeetBackend','GraniteBackend']
    for model, name in zip(IDS, expected):
        assert type(create_backend(model, '/unused', 2)).__name__ == name
        row = catalog.describe('/unused', model)
        assert row['state'] == 'not_installed' and not row['ready']
        assert row['required_files'] and row['architecture'] and row['license']
    for model in IDS[4:]:
        assert len(catalog.CATALOG[model]['revision']) == 40
        assert catalog.CATALOG[model]['download_bytes'] > 200_000_000
    for model in IDS[4:6]:
        assert 'encoder-model.onnx.data' in catalog.files(model)


def test_each_model_selection_persists_without_changing_other_options(tmp_path):
    manager = ModuleManager(tmp_path, local=True)
    before = dict(manager.config['stt'])
    for model in IDS:
        asyncio.run(manager.configure('stt', {'stt_model': model}))
        loaded = ModuleManager(tmp_path, local=True)
        assert loaded.config['stt'] == {**before, 'stt_model': model}


def package(path, entries):
    with tarfile.open(path, 'w:bz2') as bundle:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))


@pytest.mark.parametrize('name,kind', [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('C:/escape',tarfile.REGTYPE),('folder/link',tarfile.SYMTYPE),('folder/link',tarfile.LNKTYPE),('folder\\escape',tarfile.REGTYPE)])
def test_tar_rejects_traversal_absolute_and_links(tmp_path, name, kind):
    archive = tmp_path/'bad.tar.bz2'
    package(archive, [(name,b'x',kind)])
    with pytest.raises(ValueError):
        catalog.extract_archive(archive,tmp_path,('model',),100)


def test_tar_flattening_required_files_duplicates_and_size_budget(tmp_path):
    archive = tmp_path/'model.tar.bz2'
    package(archive,[('export/model',b'1234',tarfile.REGTYPE)])
    catalog.extract_archive(archive,tmp_path,('model',),4)
    assert (tmp_path/'model').read_bytes()==b'1234'
    with pytest.raises(ValueError):catalog.extract_archive(archive,tmp_path,('missing',),4)
    with pytest.raises(ValueError):catalog.extract_archive(archive,tmp_path,('model',),3)
    package(archive,[('a/model',b'1',tarfile.REGTYPE),('b/model',b'2',tarfile.REGTYPE)])
    with pytest.raises(ValueError):catalog.extract_archive(archive,tmp_path,('model',),4)


def test_disk_preflight_before_network(tmp_path):
    with patch.object(catalog.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
        with pytest.raises(ValueError,match='Espaço'):catalog.install(tmp_path,'orukeet_int8')
    assert not list(tmp_path.glob('.staging-*'))


def test_pinned_snapshot_external_weights_and_failed_load_preserve_other_models(tmp_path):
    model='parakeet_ptbr_30s'
    entry=catalog.CATALOG[model]
    fake=ModuleType('huggingface_hub')
    fake.HfApi=lambda **kw:SimpleNamespace(model_info=lambda **args:SimpleNamespace(sha=entry['revision']))
    def download(**kwargs):
        assert kwargs['revision']==entry['revision'] and kwargs['token'] is False
        assert 'encoder-model.onnx.data' in kwargs['allow_patterns']
        for name in kwargs['allow_patterns']:(Path(kwargs['local_dir'])/name).write_bytes(name.encode())
    fake.snapshot_download=download
    other=tmp_path/'whisper_base';other.mkdir();(other/'marker').write_text('preserve')
    states=[]
    # Fixtures cannot use the real upstream hashes.
    with patch.dict(entry,sha256={}),patch.dict(sys.modules,{'huggingface_hub':fake}):
        with pytest.raises(RuntimeError):
            catalog.install(tmp_path,model,progress=states.append,validator=Mock(side_effect=RuntimeError('native load failed')))
        assert not catalog.location(tmp_path,model).exists()
        installed=catalog.install(tmp_path,model,progress=states.append,validator=Mock())
        assert installed['load_validated'] and states[-1]=='verifying'
        assert catalog.verify(tmp_path,model)['revision']==entry['revision']
    assert (other/'marker').read_text()=='preserve'


def test_orukeet_uses_transducer_and_never_ctc(tmp_path):
    native=SimpleNamespace(create_stream=Mock(return_value=SimpleNamespace(accept_waveform=Mock(),result=SimpleNamespace(text='  liga a luz  '))),decode_stream=Mock())
    fake=SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_transducer=Mock(return_value=native)))
    with patch.dict(sys.modules,{'sherpa_onnx':fake}):
        engine=create_backend('orukeet_int8',tmp_path,4).load()
        assert engine.recognize([.1])[0]=='liga a luz'
        kw=fake.OfflineRecognizer.from_transducer.call_args.kwargs
        assert kw['model_type']=='nemo_transducer' and kw['feature_dim']==128 and kw['num_threads']==4
        engine.close();assert engine._model is None


def test_granite_is_persistent_and_rejects_english_only(tmp_path):
    model=SimpleNamespace(capabilities=SimpleNamespace(languages=('en','pt')),session=Mock(return_value=SimpleNamespace(run=Mock(return_value=SimpleNamespace(text='liga')),close=Mock())),close=Mock())
    fake=SimpleNamespace(Model=Mock(return_value=model))
    with patch.dict(sys.modules,{'transcribe_cpp':fake}):
        engine=create_backend('granite_turboctc_q4',tmp_path,2).load()
        assert engine.recognize([.1])[0]=='liga'
        engine.recognize([.2])
        assert fake.Model.call_count==1 and model.session.call_count==1
        assert model.session.call_args.kwargs=={'n_threads':2}
        engine.close();model.close.assert_called_once()
        model.capabilities.languages=('en',)
        with pytest.raises(ValueError,match='português'):create_backend('granite_turboctc_q4',tmp_path).load()


def test_ui_allowlists_contain_every_model_and_no_legacy_endpoint():
    for file in ['arandu_stack/app/settings.py','custom_components/local_nlu/ai_lab.py','custom_components/local_nlu/stt_lab_panel.js','custom_components/local_nlu/ai_lab_panel.js']:
        content=(ROOT/file).read_text(encoding='utf-8')
        assert all(model in content for model in IDS)
        assert '8765/api/compare-upload' not in content


def test_parakeet_offline_cpu_threads_and_plain_tdt_options(tmp_path):
    native=SimpleNamespace(recognize=Mock(return_value='  olá  '))
    manager=SimpleNamespace(create_asr=Mock(return_value=native))
    loader=SimpleNamespace(Manager=Mock(return_value=manager))
    ort=SimpleNamespace(SessionOptions=lambda:SimpleNamespace(),GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_BASIC=1))
    with patch.dict(sys.modules,{'onnx_asr':ModuleType('onnx_asr'),'onnx_asr.loader':loader,'onnxruntime':ort}):
        engine=create_backend('parakeet_tagarela',tmp_path,2).load()
        args=loader.Manager.call_args.kwargs
        assert args['providers']==['CPUExecutionProvider']
        assert args['sess_options'].intra_op_num_threads==2
        assert args['sess_options'].inter_op_num_threads==1
        assert manager.create_asr.call_args.kwargs['offline'] is True
        assert engine.recognize([.1])[0]=='olá'
        assert native.recognize.call_args.kwargs=={'sample_rate':16000}
        engine.close()


def test_orukeet_bad_archive_hash_is_rejected_before_extraction(tmp_path):
    archive=tmp_path/'bad.tar.bz2';archive.write_bytes(b'not an archive')
    fake=ModuleType('huggingface_hub')
    fake.HfApi=lambda **kw:SimpleNamespace(model_info=lambda **args:SimpleNamespace(sha=catalog.CATALOG['orukeet_int8']['revision']))
    fake.snapshot_download=Mock(side_effect=AssertionError('no full snapshot'))
    fake.hf_hub_download=Mock(return_value=str(archive))
    with patch.dict(sys.modules,{'huggingface_hub':fake}),patch.object(catalog,'extract_archive') as extract:
        with pytest.raises(ValueError,match='SHA256'):catalog.install(tmp_path/'models','orukeet_int8')
        extract.assert_not_called()
    assert fake.hf_hub_download.call_args.kwargs['revision']=='55a984d46f68323301837194ce647c702f55facc'
    assert not catalog.location(tmp_path/'models','orukeet_int8').exists()


@pytest.mark.parametrize('models', [IDS+['whisper_base'],[['nested']],['whisper_base','whisper_base'],[{'model':'whisper_base'}]])
def test_compare_rejects_oversized_duplicate_or_nested_model_selection(tmp_path, models):
    from stt_lab import STTLab
    lab=STTLab(ModuleManager(tmp_path,local=True))
    with pytest.raises(ValueError,match='modelos distintos'):
        asyncio.run(lab.test({'models':models},compare=True))
