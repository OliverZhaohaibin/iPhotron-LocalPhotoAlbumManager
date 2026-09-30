# OCR And Text Search Requirements

> Status: future product requirement; no production OCR implementation exists.
> This document defines intended capability, not a selected model, storage
> schema, dependency, or worker architecture.

## Goal

Allow users to find local photos by visible text without uploading media or
derived data. OCR must remain optional and must not reduce the availability of
the existing library, People, Pets, editing, Live Photo, or Maps workflows.

## Functional Requirements

- Detect and recognize visible text in supported still images using an
  explicitly triggered or background-capable local workflow.
- Preserve one or more recognized regions per asset, including normalized
  region geometry, recognized text, confidence, and detected or configured
  language when available.
- Support multilingual text, including at minimum the languages enabled by the
  selected OCR runtime. Unsupported languages must fail clearly rather than
  silently producing authoritative-looking results.
- Include recognized text in library search through a full-text query path.
  Metadata search must continue to work when OCR is unavailable or incomplete.
- Treat OCR output as rebuildable derived data. Rescans, model upgrades, and
  index repair must not modify source media or discard unrelated durable user
  state.
- Expose progress, cancellation, pending, unavailable, failed, and completed
  states without blocking the GUI thread.

## Architecture Constraints

- The implementation must follow the current
  [`RuntimeContext -> LibrarySession -> application ports/services`](../../architecture.md)
  boundary. GUI code must not open an OCR index or model runtime directly.
- OCR must be an optional bounded capability with explicit runtime discovery
  and graceful degradation when dependencies or models are absent.
- Recognition and search work must be generation-aware so results from a
  cancelled scan or previous library cannot publish into the active library.
- Media paths, recognized text, and model diagnostics remain local. Any future
  model acquisition flow must follow the supply-chain and privacy rules in
  [`../../security.md`](../../security.md).

## Deferred Design Decisions

The implementation proposal must separately select and validate the OCR model,
runtime dependencies, model provenance, persistence schema, application ports,
queue ownership, resource scheduling, migration strategy, and packaged-build
support. The historical Face/OCR documents under
[`../../finished/requirements/face-ocr-2026-02/`](../../finished/requirements/face-ocr-2026-02/)
are research inputs only and do not preselect OpenCV, RapidOCR, Tesseract,
`src/iPhoto/ai/`, or a particular database layout.

## Acceptance Criteria For A Future Implementation

- Search returns the expected assets for representative multilingual fixtures
  and does not expose results from another library or stale scan generation.
- Cancellation, library switching, missing models, corrupt derived state, and
  optional-dependency failures leave all existing product workflows usable.
- OCR data can be rebuilt without changing source files or unrelated durable
  choices.
- Performance, accuracy, storage, and package-size thresholds are measured on
  representative target-platform fixtures and recorded in the implementation
  proposal before release.
