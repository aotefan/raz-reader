"""Export the pinned phone model with lossless weight storage and FP32 arithmetic.

This checkpoint's FP32 linear weights happen to be exactly representable in FP16.
Verify that property for every replaced weight, then expand one layer at a time.
Do not freeze/optimize the traced modules: constant folding would expand all weights.
The convolutional encoder and full-context transformer remain the upstream model.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
import torch
from torch import nn
from torch.nn import functional as F
from transformers import Wav2Vec2ForCTC


class LosslessLinear(nn.Module):
    def __init__(self, source):
        super().__init__()
        weight = source.weight.detach()
        compact = weight.half()
        if not torch.equal(compact.float(), weight):
            raise ValueError('Refusing lossy weight conversion')
        self.register_buffer('weight', compact)
        self.register_buffer('bias', source.bias.detach() if source.bias is not None else None)

    def forward(self, value):
        return F.linear(value, self.weight.float(), self.bias)


class PhoneHead(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.projection = model.wav2vec2.feature_projection
        self.encoder = model.wav2vec2.encoder
        self.dropout = model.dropout
        self.head = model.lm_head

    def forward(self, features):
        hidden = self.projection(features)[0]
        hidden = self.encoder(hidden, return_dict=False)[0]
        return self.head(self.dropout(hidden))


def replace_linears(module):
    count = 0
    for name, child in list(module.named_children()):
        if isinstance(child, nn.Linear):
            setattr(module, name, LosslessLinear(child))
            count += 1
        else:
            count += replace_linears(child)
    return count


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def export(source, output):
    torch.set_num_threads(4)
    preprocessing = json.loads((source / 'preprocessor_config.json').read_text())
    if preprocessing.get('sampling_rate') != 16000 or not preprocessing.get('do_normalize'):
        raise ValueError('Unexpected waveform normalization')
    model = Wav2Vec2ForCTC.from_pretrained(str(source), local_files_only=True).eval()
    config = model.config
    if config.feat_extract_norm != 'layer' or config.add_adapter:
        raise ValueError('Only the verified layer-normalized, no-adapter checkpoint is supported')
    sample = torch.linspace(-.1, .1, 16000).reshape(1, -1)
    with torch.no_grad():
        baseline = model(sample).logits
        count = replace_linears(model)
        assert torch.equal(model(sample).logits, baseline), 'Storage change altered FP32 logits'
        feature_model = torch.jit.trace(model.wav2vec2.feature_extractor, sample, check_trace=False)
        features = feature_model(sample).transpose(1, 2)
        phone_model = torch.jit.trace(PhoneHead(model).eval(), features, check_trace=False)
        torch.testing.assert_close(phone_model(features), baseline, rtol=0, atol=0)
        # Exercise a shape absent from the trace, including the full attention context.
        variable = torch.linspace(-.2, .2, 49517).reshape(1, -1)
        torch.testing.assert_close(phone_model(feature_model(variable).transpose(1, 2)),
                                   model(variable).logits, rtol=0, atol=0)
    output.mkdir(parents=True, exist_ok=True)
    files = {'features.pt': feature_model, 'phones.pt': phone_model}
    for name, module in files.items():
        module.save(str(output / name))
    stride, receptive = 1, 1
    for kernel, step in zip(config.conv_kernel, config.conv_stride):
        receptive += (kernel - 1) * stride
        stride *= step
    vocab = json.loads((source / 'vocab.json').read_text())
    metadata = {
        'format': 'lossless-linear-fp16-storage-fp32-compute-v1',
        'model_sha256': sha256(source / 'pytorch_model.bin'),
        'torch_version': torch.__version__, 'transformers_version': '4.57.6',
        'linear_layers': count, 'weight_values_unchanged': True,
        'stride': stride, 'receptive_field': receptive, 'chunk_frames': 100,
        'vocab': [token for token, _ in sorted(vocab.items(), key=lambda item: item[1])],
        'blank_id': config.pad_token_id,
        'files': {name: {'sha256': sha256(output / name), 'bytes': (output / name).stat().st_size}
                  for name in files},
    }
    (output / 'compact.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in metadata.items() if key != 'vocab'}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'data/assessment-model')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.source, args.output)
