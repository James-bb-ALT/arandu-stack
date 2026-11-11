"""Real offline ONNX/IR export and numeric CPU check. Never claims GPU execution."""
import json
import os
from pathlib import Path
import numpy as np
import openvino as ov
import torch
from arandu_decision.backends import prepare_ir,verify_ir,CPUBackend,CONVERTER,gpu_probe


def main():
    root=Path(os.environ['ARANDU_DECISION_MODELS']);key=os.environ.get('ARANDU_DECISION_IR_MODEL','e5-small')
    torch.set_num_threads(2);result=prepare_ir(root,key);directory=verify_ir(root,key)
    reference=CPUBackend(root/key,key,2)
    texts=['query: Liga o ventilador da sala','query: Desliga a iluminação']
    expected=reference.encode(texts)
    tokens=reference.model.tokenize(texts)
    inputs={n:tokens[n].numpy() for n in ['input_ids','attention_mask']}
    compiled=ov.Core().compile_model(str(directory/'model.xml'),'CPU',{'INFERENCE_PRECISION_HINT':'f32','INFERENCE_NUM_THREADS':2})
    hidden=np.asarray(compiled(inputs)[compiled.output(0)],dtype=np.float32)
    mask=inputs['attention_mask'][...,None].astype(np.float32)
    actual=(hidden*mask).sum(1)/np.maximum(mask.sum(1),1e-9);actual/=np.maximum(np.linalg.norm(actual,axis=1,keepdims=True),1e-12)
    np.testing.assert_allclose(actual,expected,rtol=2e-4,atol=2e-5)
    result.update(model=key,converter=CONVERTER,numeric_cpu_equivalence=True,max_abs_error=float(np.abs(actual-expected).max()),
        execution_devices=list(compiled.get_property('EXECUTION_DEVICES')),gpu_probe=gpu_probe(),gpu_execution_validated=False,
        source_overrides=os.environ.get('ARANDU_SOURCE_OVERRIDES')=='1')
    Path(os.environ['ARANDU_DECISION_IR_OUTPUT']).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
