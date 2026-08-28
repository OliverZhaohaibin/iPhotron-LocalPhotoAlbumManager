# PR #917 Recognition Idle-Start Benchmark

## Context

- Runtime: source checkout `e91920f4`, macOS arm64, Cocoa/Metal, hot cache.
- Library: disposable `/tmp` copy restored before every sample from the same
  five-image template; the operator library was never modified.
- Policy arms: default `auto_after_metadata_idle` and the same commit with
  `IPHOTO_STARTUP_RECOGNITION_AUTO_START=0`.
- Sampling: 100 ms child-process CPU/RSS sampling. macOS psutil does not expose
  per-process I/O counters, so read/write bytes are explicitly `null`.

Two 30-sample auto batches and two 30-sample feature-scoped batches were
collected. Because whole-batch ordering showed thermal/cache bias, an additional
10-pair alternating A/B batch is the causal timing comparison below.

## Alternating A/B timing

| Metric | Feature-scoped P50/P95 | Auto P50/P95 | Auto delta P50/P95 |
|---|---:|---:|---:|
| show → interactive | 19.945 / 21.592 ms | 19.825 / 21.815 ms | -0.60% / +1.03% |
| process → first gallery | 798.130 / 1064.118 ms | 797.375 / 1057.369 ms | -0.09% / -0.63% |
| process → first usable thumbnail | 3259.572 / 3614.575 ms | 3257.639 / 3514.421 ms | -0.06% / -2.77% |
| max post-interactive GUI job | 139.608 / 186.723 ms | 140.911 / 183.887 ms | +0.93% / -1.52% |

Recognition activation occurred after the first usable thumbnail. Automatic
startup introduced no measurable first-frame/gallery regression in the
alternating comparison. The absolute post-interactive job duration is a
pre-existing coordinator construction cost present in both arms; post-
recognition GUI job stall was `0 ms` in all 30 auto samples.

## Auto-start resource envelope

The second 30-sample auto batch recorded:

| Snapshot | CPU P50/P95 | RSS P50/P95 |
|---|---:|---:|
| interactive | 358.603 / 379.743 ms | 184.4 / 186.7 MiB |
| recognition activation | 1851.815 / 1986.214 ms | 536.3 / 543.4 MiB |
| activation +1.5 s | 2593.641 / 2671.004 ms | 656.1 / 672.4 MiB |
| activation +5 s | 8510.109 / 8786.367 ms | 1395.0 / 1469.3 MiB |

These values describe intentional background AI work, not the first-frame path.
The worker threads run at `LowestPriority`; input before activation resets the
1500 ms gate.

## Failure and lifecycle scenarios

- Quick-close: 30/30 valid with `--allow-degraded`; no sample emitted
  `recognition.worker.started` and no late-QThread/fatal diagnostics appeared.
- Missing Pets models with downloads disabled: 5/5 valid; each run reported the
  missing YOLOX model, retained pending work, remained interactive, and had
  `0 ms` post-recognition GUI job stall.
- 50k backlog/index behavior remains covered by the cross-platform
  `pets-production-shape-contract`; startup generation, cancellation, and
  worker admission are covered by the three-platform startup contracts.

This is controlled source-runtime evidence on Apple Silicon. Packaged Windows,
Linux AppImage, and macOS Intel resource figures remain manual validation items.
