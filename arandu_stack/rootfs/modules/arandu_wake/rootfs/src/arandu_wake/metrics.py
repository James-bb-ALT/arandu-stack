"""Resume eventos Assist exportados sem preservar conteúdo das falas."""

from datetime import datetime
import math


def summarize(events: list[dict]) -> dict:
    times = {}
    errors = 0
    previous = None
    for event in events:
        kind = event["type"]
        timestamp = event["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            raise ValueError("timestamp deve ser segundos numéricos ou ISO 8601")
        if previous is not None and timestamp < previous:
            raise ValueError("Eventos fora de ordem temporal")
        previous = timestamp
        if kind == "run-start" and kind in times:
            raise ValueError("Forneça somente uma interação por arquivo")
        times.setdefault(kind, timestamp)
        if kind == "error":
            errors += 1

    def duration(start, end):
        if start not in times or end not in times:
            return None
        return round((times[end] - times[start]) * 1000, 2)

    return {
        "wake_stage_ms": duration("wake_word-start", "wake_word-end"),
        "capture_start_after_wake_ms": duration("wake_word-end", "stt-start"),
        "stt_ms": duration("stt-start", "stt-end"),
        "tts_generation_ms": duration("tts-start", "tts-end"),
        "pipeline_ms": duration("run-start", "run-end"),
        "errors": errors,
        "playback_completed": "unknown",
        "scope": "Eventos do pipeline. run-end não comprova fim da reprodução, CPU, RAM ou retorno à escuta.",
    }
