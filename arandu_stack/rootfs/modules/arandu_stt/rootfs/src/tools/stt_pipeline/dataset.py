from __future__ import annotations

from collections import Counter, defaultdict
import logging
import math
from pathlib import Path

from . import VERSION
from .audio import AudioTools, wav_details
from .common import (canonical, digest, identifier, natural_key, read_json, read_jsonl,
                     require_disjoint, slug, write_csv, write_json, write_jsonl)
from .scripts import parse_script
from .split import SPLITS, split_records

LOG = logging.getLogger(__name__)
NON_MEDIA = {".json", ".jsonl", ".csv", ".md", ".pdf", ".docx", ".xlsx", ".jpg", ".jpeg", ".png", ".ini", ".log"}
ERROR_FIELDS = ["speaker", "source", "script", "phrase_id", "line", "code", "message"]
VALIDATION_FIELDS = ["speaker", "source", "id", "script", "phrase_id", "status", "code", "message", "codec", "format", "duration", "cached"]


def load_mapping(path: Path | None) -> dict:
    data = read_json(path) if path else {}
    if not isinstance(data, dict) or set(data) - {"version", "scripts", "audio"}:
        raise ValueError("Mapeamento aceita somente version, scripts e audio.")
    if data.get("version", 1) != 1:
        raise ValueError("Versão de mapeamento não suportada")
    for group in ("audio", "scripts"):
        if not isinstance(data.get(group, {}), dict):
            raise ValueError(f"{group} deve ser objeto JSON")
        for key, options in data.get(group, {}).items():
            if "\\" in key or key.startswith("/") or ".." in Path(key).parts or not isinstance(options, dict):
                raise ValueError(f"Chave de mapeamento inválida: {key}; use caminho relativo com /.")
            permitted = {"encoding", "include_lines", "exclude_lines"} if group == "scripts" else {"skip", "script", "phrase_id", "text", "stream_index"}
            if set(options) - permitted:
                raise ValueError(f"Opções desconhecidas: {key}: {set(options) - permitted}")
    return data


def discover(input_dir: Path, mapping: dict, audio: AudioTools) -> tuple[list[dict], dict, list[dict]]:
    scripts, files, errors = {}, [], []
    paths = sorted((p for p in input_dir.rglob("*") if p.is_file() and not p.is_symlink()),
                   key=lambda p: natural_key(p.relative_to(input_dir).as_posix()))
    keys = {p.relative_to(input_dir).as_posix() for p in paths}
    for group in ("audio", "scripts"):
        unknown = set(mapping.get(group, {})) - keys
        if unknown:
            raise ValueError(f"Mapeamento aponta para arquivos ausentes: {sorted(unknown)}")
    for path in paths:
        if input_dir not in path.resolve().parents:
            raise ValueError(f"Caminho fora da entrada: {path}")
        rel = path.relative_to(input_dir)
        key = rel.as_posix()
        speaker = rel.parts[0] if len(rel.parts) > 1 else input_dir.name
        if path.suffix.lower() == ".txt":
            try:
                script = parse_script(path, mapping.get("scripts", {}).get(key))
                scripts[key] = script
                for issue in script.issues:
                    if issue["code"] != "header":
                        errors.append({"speaker": speaker, "script": key, **issue, "message": issue.get("text", "")})
            except (ValueError, LookupError, OSError) as exc:
                errors.append({"speaker": speaker, "script": key, "code": "script_error", "message": str(exc)})
            continue
        if path.suffix.lower() in NON_MEDIA or path.name.startswith("."):
            continue
        row = {"speaker": speaker, "source": key, "source_path": str(path), "phrase_id": identifier(path)}
        options = mapping.get("audio", {}).get(key, {})
        if options.get("skip"):
            row.update(status="skipped", code="manual_skip", message="Excluído pelo mapeamento")
        else:
            try:
                row.update(audio.probe(path, options.get("stream_index")))
                row.update(status="discovered", source_sha256=digest(path))
            except (ValueError, OSError) as exc:
                row.update(status="rejected", code="probe_error", message=str(exc))
        files.append(row)
    return files, scripts, errors


def associate(files: list[dict], scripts: dict, mapping: dict, input_dir: Path, errors: list[dict]) -> None:
    claims = defaultdict(list)
    for row in files:
        if row["status"] != "discovered":
            continue
        options = mapping.get("audio", {}).get(row["source"], {})
        path = Path(row["source_path"])
        script_key = options.get("script")
        if script_key is None:
            directory = path.parent
            while directory != input_dir:
                candidates = [key for key, script in scripts.items() if script.path.parent == directory]
                if candidates:
                    if len(candidates) == 1:
                        script_key = candidates[0]
                    break
                directory = directory.parent
            if script_key is None and path.parent == input_dir:
                candidates = [key for key, script in scripts.items() if script.path.parent == input_dir]
                if len(candidates) == 1:
                    script_key = candidates[0]
        phrase_id = options.get("phrase_id", row["phrase_id"])
        explicit_text = options.get("text")
        row.update(script=script_key or "", phrase_id=phrase_id)
        try:
            if not isinstance(phrase_id, int) or isinstance(phrase_id, bool) or phrase_id < 1:
                raise ValueError("Identificador numérico ausente ou ambíguo; forneça phrase_id no mapeamento.")
            if not script_key or script_key not in scripts:
                raise ValueError("Roteiro ausente ou múltiplos roteiros; forneça script no mapeamento.")
            script = scripts[script_key]
            if explicit_text is not None:
                if not isinstance(explicit_text, str) or not explicit_text.strip():
                    raise ValueError("Correção text deve ser uma transcrição humana não vazia.")
                text, line = explicit_text.strip(), None
            else:
                if script.ambiguous:
                    raise ValueError("Roteiro ambíguo; corrija include_lines/exclude_lines ou numeração antes de associar.")
                if phrase_id not in script.phrases:
                    raise ValueError(f"Frase {phrase_id} ausente no roteiro; nenhuma associação foi deslocada.")
                text, line = script.phrases[phrase_id]["text"], script.phrases[phrase_id]["line"]
            if not canonical(text):
                raise ValueError("Transcrição sem conteúdo lexical")
            uid = f"{slug(row['speaker'])}__{slug(script_key)}__{phrase_id:04d}"
            row.update(id=uid, text=text, line=line, transcription_origin="mapping" if explicit_text is not None else "script")
            claims[uid].append(row)
        except ValueError as exc:
            row.update(status="rejected", code="association_review", message=str(exc))
    for rows in claims.values():
        if len(rows) > 1:
            for row in rows:
                row.update(status="rejected", code="duplicate_phrase_audio", message="Mais de um áudio para participante/roteiro/frase; selecione um com skip.")
    for key, script in scripts.items():
        relevant = [r for r in files if r.get("script") == key]
        # An unnumbered script cannot anchor a missing transcript. An out-of-range
        # recording signals a possible missing line anywhere, so reject auto matches.
        if not script.numbered and any(isinstance(r.get("phrase_id"), int) and r["phrase_id"] > len(script.phrases) for r in relevant):
            for row in relevant:
                explicit = mapping.get("audio", {}).get(row["source"], {})
                if "phrase_id" not in explicit and "text" not in explicit:
                    row.update(status="rejected", code="unnumbered_count_review",
                               message="Áudio excede as frases do roteiro sem numeração; possível frase ausente. Mapeie explicitamente cada associação.")
    # Report missing recordings by explicit ID, never compact or renumber the script.
    for key, script in scripts.items():
        relevant = [r for r in files if r.get("script") == key]
        if relevant:
            present = {r.get("phrase_id") for r in relevant}
            for pid in sorted(set(script.phrases) - present):
                errors.append({"speaker": relevant[0]["speaker"], "script": key, "phrase_id": pid,
                               "code": "missing_audio", "message": "Frase sem gravação"})
        else:
            errors.append({"script": key, "code": "unmatched_script", "message": "Nenhum áudio associado ao roteiro"})


def reject_exact_sources(files: list[dict]) -> None:
    groups = defaultdict(list)
    for row in files:
        if row["status"] == "discovered":
            groups[row["source_sha256"]].append(row)
    for rows in groups.values():
        if len(rows) > 1:
            for row in rows:
                row.update(status="rejected", code="duplicate_source", message="Arquivos originais byte a byte idênticos; selecione um pelo mapeamento.")


def inspect_dataset(input_dir: Path, output_dir: Path, mapping_path=None, ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    input_dir, output_dir = input_dir.resolve(), output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Entrada não encontrada: {input_dir}")
    require_disjoint(input_dir, output_dir)
    mapping = load_mapping(mapping_path)
    files, scripts, errors = discover(input_dir, mapping, AudioTools(ffmpeg, ffprobe))
    associate(files, scripts, mapping, input_dir, errors)
    reject_exact_sources(files)
    result = {"input": str(input_dir), "files": files,
              "scripts": {k: {"numbered": v.numbered, "ambiguous": v.ambiguous, "phrases": list(v.phrases.values()),
                               "issues": v.issues} for k, v in scripts.items()}, "issues": errors}
    write_json(output_dir / "inspection.json", result)
    return result


def prepare(input_dir: Path, output_dir: Path, *, mapping_path=None, sample_rate=16000,
            min_duration=0.15, max_duration=35.0, ratios=(0.8, 0.1, 0.1), seed=42,
            split_mode="phrase", ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    input_dir, output_dir = input_dir.resolve(), output_dir.resolve()
    require_disjoint(input_dir, output_dir)
    if not input_dir.is_dir():
        raise ValueError(f"Entrada não encontrada: {input_dir}")
    if sample_rate < 1 or not 0 < min_duration < max_duration or not math.isfinite(max_duration):
        raise ValueError("Taxa/durações inválidas")
    audio = AudioTools(ffmpeg, ffprobe)
    mapping = load_mapping(mapping_path)
    # Invalidate any previous release before touching manifests.
    write_json(output_dir / "reports" / "release.json", {"ready": False, "reason": "preparation_in_progress"})
    files, scripts, errors = discover(input_dir, mapping, audio)
    associate(files, scripts, mapping, input_dir, errors)
    reject_exact_sources(files)
    cache_path = output_dir / "reports" / "conversion_cache.json"
    cache = read_json(cache_path) if cache_path.exists() else {}
    new_cache = {}
    for row in files:
        if row["status"] != "discovered":
            continue
        target = output_dir / "audio" / slug(row["speaker"]) / (row["id"] + ".wav")
        try:
            LOG.info("Decodificando %s", row["source"])
            details = audio.convert(Path(row["source_path"]), target, row, sample_rate,
                                    row["source_sha256"], cache.get(row["id"], {}))
            row.update(details, audio_filepath=str(target))
            new_cache[row["id"]] = {"signature": details["signature"], "wav_sha256": details["wav_sha256"]}
            if not min_duration <= details["duration"] <= max_duration:
                raise ValueError(f"Duração anormal: {details['duration']:.3f}s fora de [{min_duration}, {max_duration}]")
            row["status"] = "valid"
        except (ValueError, OSError, wave_error()) as exc:
            row.update(status="rejected", code="audio_error", message=str(exc))
            LOG.warning("Rejeitado %s: %s", row["source"], exc)
    duplicates = defaultdict(list)
    for row in files:
        if row["status"] == "valid":
            duplicates[row["pcm_sha256"]].append(row)
    for rows in duplicates.values():
        if len(rows) > 1:
            for row in rows:
                row.update(status="rejected", code="duplicate_pcm", message="Conteúdo PCM idêntico; selecione um arquivo pelo mapeamento.")
    valid = [r for r in files if r["status"] == "valid"]
    split_error = None
    try:
        if not valid:
            raise ValueError("Nenhum exemplo válido")
        partitions = split_records(valid, ratios, seed, split_mode)
    except ValueError as exc:
        partitions = {s: [] for s in SPLITS}
        split_error = str(exc)
        errors.append({"code": "split_error", "message": split_error})
    for split, rows in partitions.items():
        for row in rows:
            row["split"] = split
        write_jsonl(output_dir / "manifests" / f"{split}.jsonl",
                    [{k: r[k] for k in ("audio_filepath", "text", "duration", "speaker", "id")} for r in rows])
    write_jsonl(output_dir / "metadata.jsonl", valid)
    write_json(cache_path, new_cache)
    errors.extend({k: r.get(k, "") for k in ERROR_FIELDS} for r in files if r["status"] == "rejected")
    write_csv(output_dir / "reports" / "validation.csv", files, VALIDATION_FIELDS)
    write_csv(output_dir / "reports" / "errors.csv", errors, ERROR_FIELDS)
    total_duration = sum(r["duration"] for r in valid)
    stats = {"pipeline_version": VERSION, "input": str(input_dir), "sample_rate": sample_rate,
             "min_duration": min_duration, "max_duration": max_duration,
             "seed": seed, "split_mode": split_mode, "requested_ratios": list(ratios),
             "participants": len({r["speaker"] for r in files}), "discovered_recordings": len(files),
             "recordings_per_speaker": dict(Counter(r["speaker"] for r in files)),
             "valid_per_speaker": dict(Counter(r["speaker"] for r in valid)), "valid_examples": len(valid),
             "rejected_examples": sum(r["status"] == "rejected" for r in files),
             "skipped_examples": sum(r["status"] == "skipped" for r in files),
             "total_duration_seconds": total_duration, "mean_duration_seconds": total_duration / len(valid) if valid else 0,
             "splits": {s: {"examples": len(rows), "duration_seconds": sum(r["duration"] for r in rows),
                             "per_speaker": dict(Counter(r["speaker"] for r in rows))} for s, rows in partitions.items()},
             "issues": len(errors), "split_error": split_error,
             "ffmpeg_version": run_version(audio.ffmpeg), "ffprobe_version": run_version(audio.ffprobe),
             "mapping_sha256": digest(mapping_path) if mapping_path else None}
    write_json(output_dir / "reports" / "statistics.json", stats)
    if split_error:
        write_json(output_dir / "reports" / "release.json", {"ready": False, "reason": split_error})
    else:
        write_json(output_dir / "reports" / "release.json", {"ready": False, "reason": "validation_pending"})
        validate(output_dir, ffmpeg=ffmpeg, ffprobe=ffprobe)
    return stats


def wave_error():
    import wave
    return wave.Error


def run_version(executable):
    from .common import run
    return run([executable, "-version"]).splitlines()[0]


def validate(output_dir: Path, *, ffmpeg="ffmpeg", ffprobe="ffprobe", write_report=True) -> dict:
    output_dir = output_dir.resolve()
    tools = AudioTools(ffmpeg, ffprobe)
    stats = read_json(output_dir / "reports" / "statistics.json")
    metadata_rows = read_jsonl(output_dir / "metadata.jsonl")
    metadata = {r["id"]: r for r in metadata_rows}
    issues, ids, paths, hashes, phrases, speakers = [], {}, {}, {}, {}, {}
    release_path = output_dir / "reports" / "release.json"
    if release_path.exists() and read_json(release_path).get("reason") == "preparation_in_progress":
        issues.append({"code": "incomplete_preparation", "message": "Preparação interrompida; execute prepare novamente"})
    if len(metadata) != len(metadata_rows):
        issues.append({"code": "duplicate_metadata_id", "message": "IDs duplicados nos metadados"})
    count = 0
    for split in SPLITS:
        for row in read_jsonl(output_dir / "manifests" / f"{split}.jsonl"):
            count += 1
            try:
                if any(k not in row for k in ("id", "speaker", "text", "duration", "audio_filepath")):
                    raise ValueError("Campos obrigatórios ausentes")
                if not isinstance(row["text"], str) or not canonical(row["text"]):
                    raise ValueError("Transcrição vazia")
                if not isinstance(row["speaker"], str) or not row["speaker"].strip():
                    raise ValueError("Locutor vazio")
                if row["id"] in ids:
                    raise ValueError("ID duplicado")
                ids[row["id"]] = split
                path = Path(row["audio_filepath"])
                if not path.is_absolute() or (output_dir / "audio") not in path.resolve().parents:
                    raise ValueError("Caminho de áudio deve ser absoluto e estar em dataset/audio")
                if str(path.resolve()) in paths:
                    raise ValueError("Caminho de áudio duplicado")
                paths[str(path.resolve())] = split
                details = wav_details(path, stats["sample_rate"])
                tools.decode_check(path)
                if not math.isfinite(float(row["duration"])) or abs(details["duration"] - row["duration"]) > 1 / stats["sample_rate"]:
                    raise ValueError("Duração não corresponde ao WAV")
                if not stats["min_duration"] <= details["duration"] <= stats["max_duration"]:
                    raise ValueError("Duração anormal")
                if details["pcm_sha256"] in hashes:
                    raise ValueError("Conteúdo PCM duplicado")
                hashes[details["pcm_sha256"]] = split
                key = canonical(row["text"])
                if key in phrases and phrases[key] != split:
                    raise ValueError("Vazamento de frase entre conjuntos")
                phrases[key] = split
                if stats["split_mode"] == "speaker" and row["speaker"] in speakers and speakers[row["speaker"]] != split:
                    raise ValueError("Vazamento de participante")
                speakers[row["speaker"]] = split
                original = metadata.get(row["id"], {})
                if any(original.get(k) != row[k] for k in ("text", "audio_filepath", "speaker", "duration")) or original.get("split") != split:
                    raise ValueError("Manifest divergente dos metadados")
                if original.get("wav_sha256") != digest(path):
                    raise ValueError("Hash WAV divergente dos metadados")
            except (ValueError, OSError, KeyError, TypeError, EOFError, wave_error()) as exc:
                issues.append({"id": row.get("id"), "split": split, "code": "validation_error", "message": str(exc)})
    if count == 0 or count != len(metadata) or count != stats["valid_examples"]:
        issues.append({"code": "count_error", "message": "Manifest vazio ou contagens inconsistentes"})
    report = {"ready": not issues, "validated_examples": count, "issues": issues,
              "manifest_sha256": {s: digest(output_dir / "manifests" / f"{s}.jsonl") for s in SPLITS}}
    if any(issue["code"] == "incomplete_preparation" for issue in issues):
        report["reason"] = "preparation_in_progress"
    if write_report:
        write_json(output_dir / "reports" / "release.json", report)
        write_json(output_dir / "reports" / "dataset_validation.json", report)
    return report
