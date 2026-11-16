"""Host/cgroup-aware memory budget calculation and OOM failure classification for Arandu STT."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MEMORY_POLICIES = frozenset({'auto', 'custom', 'none'})


def read_cgroup_memory_info() -> dict[str, float | None]:
    """Read container cgroup v2 or v1 memory limit and current usage in MiB."""
    limit_mb: float | None = None
    usage_mb: float | None = None
    oom_killed = False

    # cgroup v2
    v2_max = Path('/sys/fs/cgroup/memory.max')
    v2_cur = Path('/sys/fs/cgroup/memory.current')
    v2_events = Path('/sys/fs/cgroup/memory.events')
    try:
        if v2_max.is_file():
            raw = v2_max.read_text(encoding='utf-8').strip()
            if raw != 'max':
                val = int(raw)
                if 0 < val < (1 << 60):
                    limit_mb = round(val / 1_048_576, 2)
        if v2_cur.is_file():
            usage_mb = round(int(v2_cur.read_text(encoding='utf-8').strip()) / 1_048_576, 2)
        if v2_events.is_file():
            for line in v2_events.read_text(encoding='utf-8').splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] in ('oom', 'oom_kill') and int(parts[1]) > 0:
                    oom_killed = True
    except (OSError, ValueError):
        pass

    # cgroup v1 fallback
    if limit_mb is None:
        v1_lim = Path('/sys/fs/cgroup/memory/memory.limit_in_bytes')
        v1_use = Path('/sys/fs/cgroup/memory/memory.usage_in_bytes')
        try:
            if v1_lim.is_file():
                val = int(v1_lim.read_text(encoding='utf-8').strip())
                if 0 < val < (1 << 58):
                    limit_mb = round(val / 1_048_576, 2)
            if v1_use.is_file() and usage_mb is None:
                usage_mb = round(int(v1_use.read_text(encoding='utf-8').strip()) / 1_048_576, 2)
        except (OSError, ValueError):
            pass

    return {
        'cgroup_limit_mb': limit_mb,
        'cgroup_usage_mb': usage_mb,
        'cgroup_oom_detected': oom_killed,
    }


def read_host_memory_info() -> dict[str, float]:
    """Read host total and available RAM in MiB via psutil."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            'host_total_mb': round(vm.total / 1_048_576, 2),
            'host_available_mb': round(vm.available / 1_048_576, 2),
        }
    except Exception:
        return {'host_total_mb': 0.0, 'host_available_mb': 0.0}


def compute_memory_budget(
    *,
    policy: str = 'auto',
    custom_limit_mb: int | float | None = None,
    catalog_limit_mb: int | float | None = None,
    estimated_ram_mb: int | float | None = None,
    production_stt_rss_mb: float | None = None,
) -> dict[str, Any]:
    """Compute effective AI Lab STT memory limit and pre-load safety headroom.

    Policies:
    - 'auto': uses 80% of truly available memory (min of host available and cgroup headroom),
      with a minimum floor of 512 MiB so small models run while protecting 8 GB / 16 GB hosts.
    - 'custom': applies user-supplied custom_limit_mb (capped safely only for pre-load warning/block).
    - 'none': disables the per-model internal Python RSS cap while keeping real OOM pre-check.
    """
    norm_policy = str(policy or 'auto').strip().lower()
    if norm_policy not in MEMORY_POLICIES:
        raise ValueError(f'Política de memória inválida: {policy}')

    host = read_host_memory_info()
    cg = read_cgroup_memory_info()

    host_avail = float(host['host_available_mb'])
    cg_limit = cg['cgroup_limit_mb']
    cg_usage = cg['cgroup_usage_mb']

    if cg_limit is not None and cg_usage is not None:
        cg_headroom = max(0.0, cg_limit - cg_usage)
        effective_avail = min(host_avail, cg_headroom) if host_avail > 0 else cg_headroom
    else:
        effective_avail = host_avail

    effective_avail = round(max(0.0, effective_avail), 2)
    est_ram = float(estimated_ram_mb or 0.0)
    prod_rss = round(float(production_stt_rss_mb or 0.0), 2)

    applied_limit_mb: int | None
    source: str

    if norm_policy == 'none':
        applied_limit_mb = None
        source = 'sem_limite_interno (protegido apenas por cgroup/SO)'
    elif norm_policy == 'custom':
        if custom_limit_mb is None or float(custom_limit_mb) < 256:
            raise ValueError('Limite de memória personalizado deve ser >= 256 MiB')
        applied_limit_mb = int(round(float(custom_limit_mb)))
        source = f'personalizado ({applied_limit_mb} MiB)'
    else:
        # Auto mode: respect real free RAM without artificially blocking 16 GB hosts at 4608 MiB.
        if effective_avail > 0:
            auto_mb = max(512, int(effective_avail * 0.80))
        elif catalog_limit_mb:
            auto_mb = int(catalog_limit_mb)
        else:
            auto_mb = 3072
        applied_limit_mb = auto_mb
        source = f'automático (80% da RAM disponível: {effective_avail:.0f} MiB)'

    # Pre-load safety guard against guaranteed OOM even when policy is 'none' or 'custom'.
    min_required = max(256.0, est_ram * 0.85) if est_ram > 0 else 256.0
    sufficient_for_load = True
    insufficient_reason: str | None = None
    if effective_avail > 0 and effective_avail < min_required:
        sufficient_for_load = False
        insufficient_reason = (
            f'RAM disponível insuficiente ({effective_avail:.0f} MiB livres; '
            f'estimativa mínima do modelo ~{min_required:.0f} MiB).'
        )
    elif applied_limit_mb is not None and est_ram > 0 and applied_limit_mb < est_ram * 0.65:
        sufficient_for_load = False
        insufficient_reason = (
            f'Limite configurado ({applied_limit_mb} MiB) inferior ao mínimo estimado do modelo '
            f'(~{est_ram:.0f} MiB).'
        )

    return {
        'memory_policy': norm_policy,
        'memory_limit_mb': applied_limit_mb,
        'memory_source': source,
        'host_total_mb': host['host_total_mb'],
        'host_available_mb': host_avail,
        'cgroup_limit_mb': cg_limit,
        'cgroup_usage_mb': cg_usage,
        'effective_available_mb': effective_avail,
        'production_stt_rss_mb': prod_rss,
        'estimated_ram_mb': est_ram,
        'sufficient_for_load': sufficient_for_load,
        'insufficient_reason': insufficient_reason,
    }


def classify_memory_failure(
    *,
    exc: BaseException | None = None,
    returncode: int | None = None,
    stderr_lines: Any = (),
    observed_rss_mb: float | None = None,
    internal_limit_mb: int | None = None,
    effective_available_mb: float | None = None,
    stage: str = 'load',
) -> dict[str, Any]:
    """Distinguish internal limit, cgroup OOM, kernel OOM, runtime allocation failure, and non-memory failure."""
    cg = read_cgroup_memory_info()
    joined = ' '.join(str(x) for x in stderr_lines).lower()
    msg_low = str(exc or '').lower()

    if (
        internal_limit_mb is not None
        and observed_rss_mb is not None
        and observed_rss_mb > float(internal_limit_mb)
    ) or 'excedeu limite' in msg_low:
        origin = 'internal_limit_exceeded'
        error_code = 'oom'
        reason = (
            f'Limite interno do modelo excedido ({observed_rss_mb:.1f} MiB > {internal_limit_mb} MiB).'
            if observed_rss_mb is not None and internal_limit_mb is not None
            else f'Limite interno do modelo excedido ({internal_limit_mb} MiB).'
        )
    elif cg['cgroup_oom_detected'] or (
        cg['cgroup_limit_mb'] is not None
        and cg['cgroup_usage_mb'] is not None
        and cg['cgroup_usage_mb'] >= cg['cgroup_limit_mb'] * 0.96
    ):
        origin = 'cgroup_limit_exceeded'
        error_code = 'oom'
        reason = (
            f'Limite de memória do container/cgroup excedido '
            f'({cg["cgroup_usage_mb"]} / {cg["cgroup_limit_mb"]} MiB).'
        )
    elif returncode in (-9, 137) or 'killed' in joined or 'sigkill' in msg_low:
        origin = 'kernel_oom'
        error_code = 'oom'
        reason = f'Processo encerrado pelo kernel/SIGKILL (exit={returncode}), provável OOM do sistema.'
    elif isinstance(exc, MemoryError) or any(
        tok in joined or tok in msg_low
        for tok in ('memoryerror', 'std::bad_alloc', 'cannot allocate memory', 'not enough memory', 'out of memory', 'oom')
    ):
        origin = 'runtime_alloc_error'
        error_code = 'oom'
        reason = f'Falha de alocação de memória no runtime durante {stage}: {exc or "out of memory"}'
    else:
        origin = 'model_load_failed'
        error_code = 'execution_error'
        reason = f'Falha não relacionada à memória durante {stage}: {exc or f"exit={returncode}"}'

    return {
        'failure_origin': origin,
        'error_code': error_code,
        'stage': stage,
        'reason': reason,
        'applied_limit_mb': internal_limit_mb,
        'observed_rss_mb': observed_rss_mb,
        'effective_available_mb': effective_available_mb,
        'cgroup_limit_mb': cg['cgroup_limit_mb'],
        'cgroup_usage_mb': cg['cgroup_usage_mb'],
    }
