"""Pinned Wyoming backend with one selected wake word enforced for every client."""
import asyncio
from functools import partial
from pyopen_wakeword import Model
from wyoming.server import AsyncServer
from wyoming.wake import Detect
from wyoming_openwakeword.handler import OpenWakeWordEventHandler
from wyoming_openwakeword.state import State

def run(options):
    selected = Model(options['wake_word']).value
    class SelectedWakeHandler(OpenWakeWordEventHandler):
        def _get_info(self):
            info = super()._get_info()
            for program in info.wake:
                program.models = [model for model in program.models if model.name == selected]
            return info

        async def handle_event(self, event):
            if Detect.is_type(event.type):
                event = Detect(names=[selected]).event()
            return await super().handle_event(event)

    async def serve():
        server = AsyncServer.from_uri('tcp://0.0.0.0:10400')
        await server.run(partial(SelectedWakeHandler, options['threshold'], options['trigger_level'], options['refractory_seconds'], State()))
    asyncio.run(serve())
