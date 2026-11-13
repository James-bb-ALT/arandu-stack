"""One OS-backed inference slot shared with Decision; no CPU affinity/reservation."""
import asyncio
from contextlib import asynccontextmanager
import errno
import os
from pathlib import Path
import tempfile
import time


@asynccontextmanager
async def inference_slot(timings=None):
    path = Path(os.environ.get('ARANDU_INFERENCE_LOCK', str(Path(tempfile.gettempdir()) / 'arandu-intelligence.lock')))
    # Both independent services use the same file; the OS releases it on exit.
    before = time.perf_counter()
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'\0')
                handle.flush()
            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                await asyncio.sleep(.05)
        wait_ms = (time.perf_counter() - before) * 1000
        if isinstance(timings, dict):
            timings['inference_slot_wait_ms'] = wait_ms
        try:
            yield wait_ms
        finally:
            release()
