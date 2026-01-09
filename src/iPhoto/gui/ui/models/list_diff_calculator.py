"""Calculate diffs for incremental updates in the asset list model."""

from __future__ import annotations

from typing import Dict, List, Any

from ....utils.pathutils import normalise_rel_value


class ListDiffResult:
    """Encapsulates the result of a list diff operation."""

    def __init__(self) -> None:
        self.removed_indices: List[int] = []
        self.inserted_items: List[Any] = []  # List of (index, item, rel_key)
        self.changed_items: List[Dict[str, object]] = []  # List of fresh row data for items that changed
        self.structure_changed: bool = False
        self.is_empty_to_empty: bool = False
        self.is_reset: bool = False


class ListDiffCalculator:
    """Helper class to calculate incremental updates for asset lists."""

    @staticmethod
    def calculate_diff(
        current_rows: List[Dict[str, object]], fresh_rows: List[Dict[str, object]]
    ) -> ListDiffResult:
        """Compare *current_rows* with *fresh_rows* and return update instructions.

        This method identifies:
        - Rows to remove (by index in the original list)
        - Rows to insert (by target index in the new list)
        - Rows to update (returning the fresh data object)
        """
        result = ListDiffResult()

        fresh_rows_copy = [dict(row) for row in fresh_rows]

        if not current_rows:
            if not fresh_rows_copy:
                result.is_empty_to_empty = True
                return result
            # Full reset needed
            result.is_reset = True
            return result

        if not fresh_rows_copy:
            # Full clear needed (handled as removals or reset)
            # If we want detailed removals:
            result.removed_indices = list(range(len(current_rows) - 1, -1, -1))
            result.structure_changed = True
            return result

        # Map the existing dataset by ``rel`` so we can detect which rows need
        # to be removed or updated.
        old_lookup: Dict[str, int] = {}
        for index, row in enumerate(current_rows):
            rel_key = normalise_rel_value(row.get("rel"))
            if rel_key is None:
                continue
            old_lookup[rel_key] = index

        # Build the same mapping for the freshly loaded rows to locate
        # insertions and replacements in the target snapshot.
        new_lookup: Dict[str, int] = {}
        for index, row in enumerate(fresh_rows_copy):
            rel_key = normalise_rel_value(row.get("rel"))
            if rel_key is None:
                continue
            new_lookup[rel_key] = index

        removed_keys = set(old_lookup.keys()) - set(new_lookup.keys())
        inserted_keys = set(new_lookup.keys()) - set(old_lookup.keys())
        common_keys = set(old_lookup.keys()) & set(new_lookup.keys())

        # Removing rows first keeps indices stable for the insertion phase.
        result.removed_indices = sorted(
            (old_lookup[key] for key in removed_keys), reverse=True
        )
        result.structure_changed = bool(result.removed_indices or inserted_keys)

        # Insert new rows at the positions reported by the freshly scanned
        # snapshot.  Sorting the payload ensures indices remain valid as the
        # list grows.
        insertion_payload = sorted(
            (
                (new_lookup[key], fresh_rows_copy[new_lookup[key]], key)
                for key in inserted_keys
                if key in new_lookup
            ),
            key=lambda item: item[0],
        )
        result.inserted_items = insertion_payload

        # For updates, we return the FRESH data. The model is responsible for
        # finding the row in its current state (after structure changes) and updating it.
        # This avoids index misalignment if the calculator tries to predict the final index
        # but the model's structure changed differently (or if we want to be safe).

        for rel_key in common_keys:
            new_index = new_lookup.get(rel_key)
            old_index = old_lookup.get(rel_key)

            if new_index is None or old_index is None:
                continue

            replacement = fresh_rows_copy[new_index]
            original = current_rows[old_index]

            if original == replacement:
                continue

            result.changed_items.append(replacement)

        return result
