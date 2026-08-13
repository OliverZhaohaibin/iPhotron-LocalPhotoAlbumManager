# Scan Hotspot Optimization Rebaseline

> Status: **Active profiling requirement.** Historical scanner call graphs and
> timing estimates are not current evidence.

Before selecting C/C++ or another compiled rewrite target, profile the current
production scan path through `RuntimeContext`, `LibrarySession`, application
services/ports, scanner adapters, and the current index store.

Measure filesystem discovery, metadata handling, hashing, Live Photo pairing,
thumbnail work, repository commit, and visible post-commit publishing
separately on representative small, medium, and large libraries.

A native optimization is justified only when current measurements show material
CPU cost, the boundary is narrow/stable, conversion overhead does not erase the
gain, and reference-vs-optimized equivalence can be tested. I/O- or
subprocess-dominated stages should first be improved through batching, fewer
round trips, scheduling, or cache policy.

Any optimization must preserve scan idempotence, durable user state, Live Photo
roles, independent `face_status`/`pet_status`, cancellation/generation safety,
and post-commit `ScanBatchCommitted` publishing. It must not bypass
application/session ownership or repository boundaries.

For each candidate record the current call site, CPU share, wall-time share,
proposed boundary, before/after result, regression coverage, and packaging
impact. `src/iPhoto/core/pairing.py` is valid to profile, but it is not assumed
to be a hotspot merely because an older document identified date parsing there.

Move this requirement to `docs/finished/requirements/` after the current scanner
has been profiled and every material compiled-code candidate has a measured
decision.
