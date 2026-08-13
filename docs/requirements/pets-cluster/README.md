# Pets Clustering Requirements — Historical Design Archive

The files in this directory are retained as design and implementation history.
They are **not** the current production contract.

Any use of words such as "current", "now", or "production" inside the archived
files must be read relative to the design snapshot in which that file was
written. In particular, the older complete-link clustering description is
superseded.

The canonical runtime contract is:

- `../../misc/PETS_RECOGNITION_RUNTIME.md`
- `../../architecture.md`

Current production behavior uses `species-bounded-single-link-v3`, including
species separation, cannot-link constraints, bounded cluster diameter to prevent
chaining, and the current People/Pets overlap arbitration rules.

Do not implement new Pets behavior from the archived requirements without first
reconciling it against the runtime code and the canonical documents above.
