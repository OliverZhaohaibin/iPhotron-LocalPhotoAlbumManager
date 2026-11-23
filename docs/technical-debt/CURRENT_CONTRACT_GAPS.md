# Current Contract Implementation Gaps

Reviewed against the local checkout on 2026-11-18. These are confirmed gaps
between current contracts and implementation/tooling, not permissions to weaken
the contracts. No production changes are included in this documentation audit.
Remote CI, published binaries, and native packaged acceptance were not audited.

## CG-01: Architecture Checker Coverage

Status: open. Owner: architecture tooling and the Maps/recognition maintainers.

Evidence: [`check_architecture.py`](../../tools/check_architecture.py) passes
`src/iPhoto` to the import checker. In
[`check_layer_boundaries.py`](../../tools/check_layer_boundaries.py),
`LOWER_LAYER_GUI_FORBIDDEN_ROOTS` covers cache, core, infrastructure, IO, library,
and People, but not Pets or shared recognition. The invocation also omits
`src/maps`. This does not prove those modules contain violations; it proves
the documented import prohibition is not fully enforced by this gate.

Closure requires including these roots with correct module-name and relative
import resolution, plus positive and negative architecture tests. Preserve
existing checks and their CI invocation. Do not close this entry solely because
the current checker passes.

## CG-02: Location Write-Back Composition

Status: open. Owner: location application/session services and desktop composition.

Evidence:

- [`PlaybackCoordinator`](../../src/iPhoto/gui/coordinators/playback_coordinator.py)
  constructs `LocationAssignmentService` with the concrete
  `IndexStoreLocationAssignmentRepository` when confirming an assignment.
- [`LocationFileWriteQueue`](../../src/iPhoto/gui/services/location_file_write_queue.py)
  constructs `MetadataWriteJobRepository` on library binding and supplies a
  concrete `ExifToolMetadataWriter` by default. Job recovery, retry, and status
  writes are consequently coupled to a GUI-owned queue.
- The [architecture](../architecture.md#assign-location) requires workflows and
  durable-state ownership behind application/session boundaries; the current
  import gate does not reject these concrete GUI composition paths.

Closure requires moving assignment and durable job orchestration behind the
owning application/session surface and ports, composed by the runtime. Keep Qt
progress and signals as thin adapters. Preserve atomic local assignment/job
creation, superseded queued/failed jobs, interrupted-write verification,
explicit failed-job retry, relocation, waitable shutdown, and local-state-first
warnings. Validate queue/coordinator and temporary-library lifecycle regressions;
do not introduce another independently bound location service in the GUI.

## CG-03: ExifTool Payload Assembly

Status: open tooling gap. Owner: Linux standalone/Flatpak release tooling.

Evidence:

- [`build_nuitka_fast.sh`](../../scripts/build_nuitka_fast.sh) includes the
  `exiftool` Python wrapper and emits the final standalone tree hash, but has no
  maintained hook to stage the external ExifTool executable and dependencies
  before that manifest is generated.
- [`build_flatpak.sh`](../../scripts/build_flatpak.sh) invokes the
  [provenance audit](../../tools/check_flatpak_elf_abi.py) before copying the
  standalone. Changes to any payload node invalidate the recorded tree hash.
- The old guide advised adding an executable to the completed standalone.
  That instruction conflicted with the audit and has been removed. This entry
  does not assert that existing published Flatpaks contain a broken payload.

The supported documented posture is optional ExifTool with recoverable
degradation. Closure requires an explicit, reproducible assembly step before
final provenance, followed by ABI/content checks and real Flatpak metadata
extraction/GPS write-back validation on an isolated library. Include executable
permissions and ExifTool's runtime dependencies; the Python wrapper alone is
insufficient. Do not bypass provenance validation or label manually modified
output as an unchanged maintained build.

## Evidence Boundaries

Source support, explicit frozen-runtime inputs, and packaged native acceptance
are separate evidence. In particular, staged Pets models alone do not establish
that Torch, torchvision, and USearch are usable in a frozen artifact. Retain the
capability warning and release smoke requirements in the
[executable guide](../misc/BUILD_EXE.md#pets-recognition-in-release-builds).
No new Pets implementation defect or release support claim is inferred here.

Existing unified CPU/mmap/RenderSession/GPU/RAW ownership work remains
[TD-890-02](PR_890.md); it is not duplicated or marked complete by this audit.
