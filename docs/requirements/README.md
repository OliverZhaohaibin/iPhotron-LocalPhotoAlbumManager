# Active Requirements And Validation Protocols

This directory contains only work that is not yet implemented and long-lived
validation protocols that remain applicable to the current production
architecture.

- Read [`../architecture.md`](../architecture.md) for how production works now.
- Read [`../development.md`](../development.md) and the focused documents under
  [`../misc/`](../misc/) before changing an existing subsystem.
- Move completed or superseded designs to
  [`../finished/requirements/`](../finished/requirements/); archived documents
  are historical evidence, not implementation instructions.
- Track confirmed but unresolved engineering debt under
  [`../technical-debt/`](../technical-debt/).

Current entries:

- [`ocr/OCR_REQUIREMENTS.md`](ocr/OCR_REQUIREMENTS.md): future, product-level
  local OCR and text-search requirements.
- [`MOVE_DELETE_OPTIMIZATION_PLAN.md`](MOVE_DELETE_OPTIMIZATION_PLAN.md): future
  native optimization work after the completed Python phase.
- [`DETAIL_OPEN_BENCHMARK_RUNBOOK.md`](DETAIL_OPEN_BENCHMARK_RUNBOOK.md): current
  packaged Detail performance protocol.
- [`STARTUP_BENCHMARK_RUNBOOK.md`](STARTUP_BENCHMARK_RUNBOOK.md): current startup
  performance and regression protocol.
- [`STARTUP_MANUAL_VALIDATION_MATRIX.md`](STARTUP_MANUAL_VALIDATION_MATRIX.md):
  current cross-platform packaged startup evidence matrix.
- [`scan_c_hotspot_optimization.md`](scan_c_hotspot_optimization.md): future
  optimization hypotheses based on historical profiling; re-measurement is a
  mandatory implementation gate.
