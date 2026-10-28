"""Verify that continued training changed CTC weights and preserved the backbone."""
import argparse
import hashlib
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stt_pipeline.common import digest, write_json


def read_checkpoint(path):
    import torch
    with tarfile.open(path) as archive:
        weights = next(member for member in archive.getmembers() if member.name.endswith('model_weights.ckpt'))
        with archive.extractfile(weights) as source:
            state = torch.load(source, map_location='cpu', weights_only=True)
        if 'state_dict' in state:
            state = state['state_dict']
        tokenizer = {}
        for suffix in ('tokenizer.model', 'vocab.txt', 'tokenizer.vocab'):
            member = next(member for member in archive.getmembers() if member.name.endswith(suffix))
            tokenizer[suffix] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    return state, tokenizer


def verify(parent, candidate, output):
    import torch
    before, previous_tokenizer = read_checkpoint(parent)
    after, current_tokenizer = read_checkpoint(candidate)
    if before.keys() != after.keys():
        raise ValueError('Checkpoint parameter names differ.')
    changed = [name for name in before if not torch.equal(before[name], after[name])]
    unrelated = [name for name in changed if not name.startswith('ctc_decoder.')]
    result = {'parent_sha256': digest(parent), 'candidate_sha256': digest(candidate),
              'changed_tensors': changed, 'ctc_weights_changed': bool(changed),
              'encoder_and_rnnt_unchanged': not unrelated,
              'tokenizer_unchanged': previous_tokenizer == current_tokenizer,
              'tokenizer_sha256': current_tokenizer}
    write_json(output, result)
    if unrelated or not changed or previous_tokenizer != current_tokenizer:
        raise ValueError('Continued training invariants failed; inspect verification report.')
    print(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.parent, args.candidate, args.output)
