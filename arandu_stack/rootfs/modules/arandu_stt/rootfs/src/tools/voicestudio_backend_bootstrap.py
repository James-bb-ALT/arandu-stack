"""Run the installed VoiceStudio backend with CPU-compatible weight precision.

The installed app forces float16 even on CPU. Override only this dedicated
process; no installed files or global preferences are modified.
"""
import logging
import os
from pathlib import Path
import sys

runtime = Path(os.environ['APPDATA']) / 'VoiceStudio/runtime/project'
sys.path.insert(0, str(runtime))
sys.path.insert(0, str(runtime / 'backend'))

# Importing the application initializes its normal paths, routers and runtime.
# The TTS model loads later, on application startup or the first generation.
import main as application
from services import model_manager
import uvicorn

original_lazy_omnivoice = model_manager._lazy_omnivoice


def lazy_omnivoice():
    model_class = original_lazy_omnivoice()
    if not getattr(model_class, '_arandu_cpu_precision', False):
        original_from_pretrained = model_class.from_pretrained

        def from_pretrained(cls, *args, **kwargs):
            if str(kwargs.get('device_map', '')) == 'cpu':
                import torch
                kwargs['dtype'] = torch.float32
                logging.getLogger('omnivoice.model').info('Arandu batch: float32 CPU precision enabled.')
            return original_from_pretrained(*args, **kwargs)

        model_class.from_pretrained = classmethod(from_pretrained)
        model_class._arandu_cpu_precision = True
    return model_class


model_manager._lazy_omnivoice = lazy_omnivoice
if __name__ == '__main__':
    uvicorn.run(application.app, host='127.0.0.1', port=int(os.environ['OMNIVOICE_PORT']))
