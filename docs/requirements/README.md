# Requirements Document Lifecycle

`docs/requirements/` is for active requirements, acceptance runbooks, and work
that still carries unresolved implementation or validation obligations.

Completed work belongs under `docs/finished/`. Historical design inputs that
must stay near an active requirement must be explicitly labelled Historical or
Superseded and link to the current canonical contract.

## Status rules

- **Active**: implementation or acceptance work remains.
- **Closed with residual debt**: the completed phase is documented, but the
  directory remains here because named follow-up obligations are still active.
- **Historical / Superseded**: retained for design history only; it is not a
  production contract.
- **Finished**: no active requirement remains; archive under `docs/finished/`.

A document under `docs/requirements/` must not use unqualified wording such as
"current implementation" when it is only describing the design state at the
time it was written.

## Current classifications

- `DETAIL_OPEN_BENCHMARK_RUNBOOK.md`: Active acceptance/runbook.
- `GALLERY_SCROLL_PERFORMANCE_REARCHITECTURE.md`: Active validation context;
  implementation is substantially complete but platform validation remains.
- `startup-chain-optimization/`: Closed v1 with residual debt. Keep here while
  the explicitly documented startup diagnostics/deferred work remains active.
- `pets-cluster/`: Historical design snapshots. Current runtime contract is
  `../misc/PETS_RECOGNITION_RUNTIME.md`.
- `gallery-detail-gpu-first/`: Completed implementation material. New changes
  should use the current architecture/runbook rather than treating phase design
  notes as normative.
- `scan_c_hotspot_optimization.md`: Re-baselining requirement; historical call
  graphs/profiling numbers are not current evidence.

When a remaining obligation is closed, move its completed planning/design
material to `docs/finished/requirements/` rather than leaving a silently stale
"current" document in this directory.
