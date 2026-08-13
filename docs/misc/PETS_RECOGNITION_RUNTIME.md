# Pets Recognition Runtime

This note records the production contract for pet detection, clustering,
persistence, and People & Pets UI integration. It supplements
[`architecture.md`](../architecture.md); the older documents under
`docs/requirements/pets-cluster/` are historical design inputs.

## Runtime Ownership

Pets is an independent bounded context under `src/iPhoto/pets/`.

| Component | Responsibility |
| --- | --- |
| `PetClusterPipeline` | YOLOX detection, DINOv2 embeddings, species-aware bounded single-link clustering, and stable identity canonicalization. |
| `PetScanWorker` | Low-pressure feature-activated background batches, status transitions, model-version upgrades, and cancellation. |
| `PetIndexCoordinator` | Serializes snapshot mutations, updates asset bookkeeping, and publishes committed revisions. |
| `PetRepository` | Rebuildable detections and clustered pet records in `pet_index.db`. |
| `PetStateRepository` | Durable profiles, names, covers, hidden flags, rejected keys, and redirects in `pet_state.db`. |
| `PetService` | Library-bound dashboard, query, annotation, and mutation API. |
| `LibrarySession.pets` | Composition boundary used by GUI and headless callers. |

People and Pets use the same orchestration pattern but do not share detection,
embedding, profile, or runtime snapshot tables. The combined dashboard composes
`PeopleService` and `PetService` summaries at presentation time.

Face and pet workers may commit in either order. Each pet batch uses the People
boxes currently available and revalidates them inside the serialized Pets
commit. A later People snapshot triggers Pets reconciliation for its changed
assets. Reconciliation atomically removes any already-committed conflicts and
rebuilds the Pets snapshot through the normal clustering, canonicalization,
durable-state synchronization, and thumbnail cleanup path.

## Models And Optional Dependencies

Install the optional runtime with:

```bash
pip install -e ".[pets-ai]"
```

The extra provides `onnxruntime`, `torch`, `torchvision`, `usearch`, and
`certifi`. Build-time model staging uses:

```text
src/extension/models/pets/
├── detector/yolox_nano_coco.onnx
└── embedding/dinov2_vits14/dinov2_vits14.pt
```

That tree is a packaging/staging location, not a guarantee that a fresh source
checkout contains the model binaries. Runtime model resolution may also use the
platform user cache. `IPHOTO_PET_MODEL_DIR` overrides the model root.

The detector may be populated on first use unless
`IPHOTO_PET_MODEL_AUTO_DOWNLOAD=0`; its URL defaults to the upstream YOLOX
release and can be overridden with `IPHOTO_PET_DETECTOR_MODEL_URL`. Production
does not execute Torch Hub. DINOv2 must be supplied as the hash- and size-verified
TorchScript artifact declared in `iPhoto/pets/model_manifest.json`; Torch Hub is
only model provenance/conversion tooling. `IPHOTO_PET_SCAN_DISABLED=1` disables
Pets scanning without disabling the rest of the application.

Packaged/offline builds that promise Pets support must include the Python AI
runtime and the two model files under `extension/models/pets`. A build that
omits them must preserve graceful degradation: core browsing, People, Maps,
editing, and library state remain usable.

## Detection And Clustering Contract

The first production pipeline supports cats and dogs. YOLOX runs through the
available ONNX Runtime providers and uses full-image detection with tiled
fallback for small subjects. Accepted boxes must meet the configured confidence
and minimum-size thresholds.

After species filtering and pet-to-pet deduplication, accepted pet boxes are
compared with automatic and manual People face boxes for the same asset. People
normally remains authoritative for strong conflicts, but face overlap is not an
unconditional pet-suppression rule. A plausible larger pet-body detection is
preserved when it substantially contains a much smaller face box instead of
behaving like a duplicate person detection. The current guard treats a pet box
as materially larger at a pet-to-face area ratio of `1.5` or greater and avoids
using this exception for image-filling/mural-like detections whose box covers at
least `0.60` of the image area.

The result of the People/Pets conflict filter is part of the committed Pets
snapshot. Suppressed detections are absent from dashboard cards, pet gallery
queries, detail annotations, and overlays rather than being hidden only in the
dashboard.

Each accepted crop receives:

- a normalized DINOv2 embedding;
- a stable `pet_key` derived from asset identity, quantized geometry, and image
  dimensions;
- a cropped PNG under `.iPhoto/pets/thumbnails/`;
- model, image-size, confidence, and species metadata.

Clustering is species-separated and uses the current
`species-bounded-single-link-v3` pipeline. Candidate edges are formed from
embedding similarity, but ordinary single-link chaining is constrained by
cluster-diameter and cannot-link/anti-chain-merge checks so a sequence of
locally similar detections cannot bridge identities that are globally too far
apart. Cats and dogs must never enter the same identity cluster. Stable state
uses `pet_key` mappings and profile distance to preserve canonical `pet_id`
values across rebuilds.

The detector and clustering pipeline versions are stored separately. A detector
version change resets eligible `done` assets to `pending`; a clustering-only
version change reclusters stored embeddings without resetting asset scan state.
The People/Pets conflict filter is part of the detector pipeline version so
existing libraries are re-evaluated after relevant upgrades.

When an exact `pet_key` carries a canonical identity into a new embedding
contract, compatible detections from the same batch may join that staged anchor.
The runtime switch atomically retires the identity's incompatible old-contract
detections. Any retired asset outside the committed replacement scope is marked
`pending`, and the complete changed/retired asset sets are persisted for crash
recovery before the snapshot event is dispatched. Until each retired asset is
processed, a target-contract migration record lets only that asset fuzzy-match
the anchored identity even while its new profile has fewer than two samples.
Processing the asset consumes the record, so ordinary one-sample profiles never
become general embedding candidates. Outstanding records follow explicit Pet
merges and advance to the newest target contract if another embedding upgrade
starts before the previous drain finishes.

## Scan Scheduling And Status

`global_index.db.assets.pet_status` is independent from `face_status` and uses:

| Status | Meaning |
| --- | --- |
| `pending` | Eligible image has not completed the current detector pipeline. |
| `retry` | First asset-level failure; it may be attempted once more. |
| `failed` | Repeated asset-level failure; a later metadata rescan can reset it. |
| `done` | Detection completed, including valid images with no pets. |
| `skipped` | Video, non-primary Live Photo component, or another ineligible asset. |

Application startup and metadata-scan completion do not by themselves start the
People or Pets recognition workers. Startup may discover/persist pending work,
but model initialization and recognition scanning remain outside the first-frame
path. The recognition runtime is activated on first use of the recognition
feature after its initial viewport is ready; the coordinator then starts only
the required worker(s) and drains eligible persisted `pending`/`retry` work.
Interactive rescans may enqueue newly committed assets for a recognition runtime
that is already active, but they do not restore unconditional startup scanning.

The Pet worker uses small batches and queue top-up from the asset repository.
Missing dependencies/models are runtime-availability failures: pending rows are
left intact so installing the optional runtime can resume without rebuilding
the library index.

## Persistence And Mutation Safety

```text
<LibraryRoot>/.iPhoto/pets/
├── pet_index.db       # rebuildable detections and pet records
├── pet_state.db       # durable user decisions
└── thumbnails/        # rebuildable crops, except crops retained by covers
```

Snapshot commits replace rebuildable runtime rows but synchronize durable state
afterward. If durable-state synchronization fails, the previous runtime
snapshot is restored. Asset `pet_status='done'` is written only after the pet
snapshot has committed.

User mutations include rename, hide/unhide, merge, set cover, delete a rejected
detection, move a detection to another pet, and move a detection into a new pet.
Deleting a detection records its `pet_key` as rejected so a rescan does not
restore it. If it was the custom cover, that cover reference is removed and a
remaining key detection becomes the automatic cover.

Runtime replacement creates new UUID-based crop files. Cleanup is intentionally
candidate-based: only thumbnail paths displaced by a successful transaction are
considered, and paths still referenced by runtime detections or durable covers
are retained. Do not replace this with a blind directory sweep; the worker may
have written crops for an in-flight batch that has not committed yet.

## People & Pets Composition

The dashboard supports person, pet, and identity-group cards. Pets can be
pinned, opened as gallery queries, hidden, named, merged, and used in groups.
Cross-kind person/pet merges are represented as durable identity redirects;
same-kind pet merges are applied in the Pets repositories. Mixed identity
groups and redirects are coordination state stored by the People state
repository, while each bounded context keeps ownership of its own canonical
records.

Photo detail/playback surfaces request both face and pet annotations and render
them through the shared recognition annotation/overlay transport. A pet
annotation remains a `PetDetectionRecord`/`AssetPetAnnotation`; it must not be
coerced into a face or person record.

## Focused Verification

```bash
.venv/bin/python -m pytest -q tests/test_pet_service.py
.venv/bin/python -m pytest -q tests/test_people_service.py tests/test_people_repository.py
.venv/bin/python -m pytest -q tests/gui/widgets/test_people_dashboard_widget.py
.venv/bin/python -m pytest -q tests/gui/coordinators/test_playback_coordinator.py
.venv/bin/python -m pytest -q tests/ui/widgets/test_face_name_overlay.py
```

Also run `python3 tools/check_architecture.py` after changing session, port, or
GUI dependency boundaries.
