# Benchmark HTTP PT-BR independente

- Freeze: `ptbr-independent-v2`
- Casos por rodada: 144
- Warm-ups descartados: 3
- Rodadas medidas: 7
- Falhas steady-state: 0
- Falhas cold start: 0
- RSS máximo: 786432 bytes (Linux /proc/<pid>/status VmRSS)
- HWM máximo: 1347584 bytes (Linux /proc/<pid>/status VmHWM)

## Latência por request

| Métrica | Nanosegundos |
| --- | ---: |
| `min_ns` | 564353 |
| `p50_ns` | 732709 |
| `p95_ns` | 974043 |
| `p99_ns` | 1134107 |
| `max_ns` | 1453687 |
| `mean_ns` | 755018.2162698413 |

## Duração por rodada

| Métrica | Nanosegundos |
| --- | ---: |
| `min_ns` | 105551249 |
| `p50_ns` | 108529759 |
| `p95_ns` | 109083167 |
| `p99_ns` | 109083167 |
| `max_ns` | 115546273 |
| `mean_ns` | 108831795.57142857 |

- Throughput: 1323.1427382404052 requests/s
- Amostras cold start: 3
