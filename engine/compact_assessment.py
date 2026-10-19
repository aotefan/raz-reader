"""Memory-bounded execution of the unchanged, locally exported phone model.

Only convolutional features are tiled, with the exact receptive-field overlap.
Normalization uses the whole recording and attention still sees every frame.
Upstream decoding, phonemization, alignment and scoring are not changed.
"""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types


def phone_module():
    """Load the pinned phones code without unrelated ASR/TTS/sklearn imports."""
    name = '_raz_phone_backend'
    if name not in sys.modules:
        spec = importlib.util.find_spec('openpronounce')
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError('The pinned OpenPronounce package is not installed')
        package = types.ModuleType(name)
        package.__path__ = list(spec.submodule_search_locations)
        package.__package__ = name
        sys.modules[name] = package
    return importlib.import_module(name + '.phones')


class CompactRecognizer:
    def __init__(self, directory):
        import torch
        self.torch = torch
        directory = Path(directory)
        self.metadata = json.loads((directory / 'compact.json').read_text())
        if self.metadata['format'] != 'lossless-linear-fp16-storage-fp32-compute-v1':
            raise ValueError('Unsupported compact model')
        self.features = torch.jit.load(str(directory / 'features.pt'), map_location='cpu').eval()
        self.phones = torch.jit.load(str(directory / 'phones.pt'), map_location='cpu').eval()
        self.decoder = phone_module()
        if sys.platform.startswith('linux'):
            import ctypes
            ctypes.CDLL(None).malloc_trim(0)
        self.locked_bytes = 0
        if sys.platform.startswith('linux') and os.environ.get('RAZ_PIN_COMPACT_WEIGHTS') == '1':
            self.locked_bytes = self._pin_weights()

    def _pin_weights(self):
        """Keep only the bounded model weights resident while OCR/translation run.

        Linux permits mlock for this unprivileged process within RLIMIT_MEMLOCK.
        No extra capability is required. Locks disappear when the worker exits.
        """
        import ctypes
        import resource
        page = os.sysconf('SC_PAGE_SIZE')
        ranges = []
        for module in (self.features, self.phones):
            for tensor in list(module.parameters()) + list(module.buffers()):
                if not tensor.is_contiguous():
                    raise ValueError('Only contiguous exported weight storage may be pinned')
                first = tensor.data_ptr()
                last = first + tensor.numel() * tensor.element_size()
                ranges.append((first // page * page, (last + page - 1) // page * page))
        merged = []
        for first, last in sorted(ranges):
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        size = sum(last - first for first, last in merged)
        limit = resource.getrlimit(resource.RLIMIT_MEMLOCK)[0]
        if size > 700 * 1024 * 1024 or (limit != resource.RLIM_INFINITY and size > limit):
            raise RuntimeError('Compact model exceeds the bounded memory-lock allowance')
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        libc.mlock.restype = ctypes.c_int
        for first, last in merged:
            if libc.mlock(first, last - first) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
        return size

    def log_posteriors(self, samples):
        import numpy as np
        torch = self.torch
        # Identical to Wav2Vec2FeatureExtractor for one unpadded recording.
        normalized = (samples - samples.mean()) / np.sqrt(samples.var() + 1e-7)
        values = torch.from_numpy(np.asarray(normalized, dtype=np.float32)).reshape(1, -1)
        stride = self.metadata['stride']
        receptive = self.metadata['receptive_field']
        total = (values.shape[1] - receptive) // stride + 1
        chunk = self.metadata['chunk_frames']
        # Profiling optimizations may prepack expanded FP32 weights on x86.
        # Execute the stored graph, preserving the per-layer temporary expansion.
        with torch.no_grad(), torch.jit.optimized_execution(False):
            # This bounds early convolution activations for recordings up to 60 s.
            # Each output frame has exactly the same source samples as an untiled run.
            features = []
            for start in range(0, total, chunk):
                count = min(chunk, total - start)
                first = start * stride
                last = first + (count - 1) * stride + receptive
                features.append(self.features(values[:, first:last]))
            merged = torch.cat(features, dim=2).transpose(1, 2)
            logits = self.phones(merged)[0]
            return torch.log_softmax(logits.float(), dim=-1).cpu().numpy()

    def recognize(self, samples, lang='en'):
        return self.decoder.decode_ctc(self.log_posteriors(samples), self.metadata['vocab'],
                                       self.metadata['blank_id'], lang=lang)
