# Presentation Qt Adapters

This package contains **thin Qt presentation adapters** that relay signals
between the application layer and UI consumers.

---

## What belongs here

| ✅ Allowed | ❌ Not allowed |
|---|---|
| Qt signal relay (`connect`, `emit`) | Business rules or conditions |
| Converting application-layer results into Qt signal payloads | Direct access to infrastructure (DB, filesystem) |
| Throttling / batching of high-frequency signals | Owning worker lifecycle |
| Triggering use-case calls in response to Qt events | Duplicating façade logic |

Adapters must **not** become a second façade or a second service layer.

---

## Boundary rules (Phase 4)

1. **Adapters only do presentation-level signal / state adaptation.**
   They must not contain `if`-branches that express application policies.

2. **Adapters never touch infrastructure.**
   No database access, no filesystem access, no network.

3. **Adapters never own business state.**
   Flags such as "is scanning" or "current album root" belong in application
   services, not here.

4. **Adapters are passive relay objects.**
   They respond to signals emitted by service-layer objects and re-emit on
   behalf of UI consumers.

5. **Adapters must not grow into a "new middle layer".**
   If you find yourself adding methods beyond signal relay, the logic belongs
   in an application service or use case.

---

## Current adapters

| File | Responsibility |
|---|---|
| `library_update_adapter.py` | Forwards index/links/reload/error signals from `LibraryUpdateService` to UI |
| `scan_progress_adapter.py` | Aggregates scan-progress signals from background workers |

---

## Adding a new adapter

1. Create `<name>_adapter.py` in this directory.
2. Inherit from `QObject`.
3. Declare `Signal` fields for the events you relay.
4. Implement `@Slot` relay methods – each slot emits the corresponding signal.
5. Do **not** add business logic.
6. Register the export in `__init__.py`.
7. Add at least one test in `tests/presentation/qt/` that verifies:
   - signals are forwarded correctly,
   - no business rule is embedded.
