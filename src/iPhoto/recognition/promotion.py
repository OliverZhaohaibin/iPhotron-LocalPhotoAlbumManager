"""Shared identity-promotion state for People and Pets."""

from __future__ import annotations

from dataclasses import dataclass

PROMOTION_LEGACY_VISIBLE = "legacy_visible"
PROMOTION_CANDIDATE = "candidate"
PROMOTION_ELIGIBLE = "eligible"
PROMOTION_CONFIRMED = "confirmed"
VISIBLE_PROMOTION_STATES = frozenset(
    {PROMOTION_LEGACY_VISIBLE, PROMOTION_ELIGIBLE, PROMOTION_CONFIRMED}
)
VALID_PROMOTION_STATES = frozenset({*VISIBLE_PROMOTION_STATES, PROMOTION_CANDIDATE})


@dataclass(frozen=True)
class IdentityPromotionRecord:
    identity_id: str
    evidence_asset_count: int
    promotion_state: str

    @property
    def is_visible(self) -> bool:
        return self.promotion_state in VISIBLE_PROMOTION_STATES


def normalize_promotion_state(value: object, *, default: str = PROMOTION_CANDIDATE) -> str:
    state = str(value or "").strip().lower()
    return state if state in VALID_PROMOTION_STATES else default


def automatic_promotion_state(
    evidence_asset_count: int,
    *,
    minimum_evidence: int,
    previous_state: object = None,
) -> str:
    previous = normalize_promotion_state(previous_state)
    if previous in {PROMOTION_CONFIRMED, PROMOTION_LEGACY_VISIBLE}:
        return previous
    return (
        PROMOTION_ELIGIBLE
        if int(evidence_asset_count) >= int(minimum_evidence)
        else PROMOTION_CANDIDATE
    )


def merged_promotion_state(
    left: object,
    right: object,
    *,
    evidence_asset_count: int,
    minimum_evidence: int,
) -> str:
    states = {normalize_promotion_state(left), normalize_promotion_state(right)}
    if PROMOTION_CONFIRMED in states:
        return PROMOTION_CONFIRMED
    if PROMOTION_LEGACY_VISIBLE in states:
        return PROMOTION_LEGACY_VISIBLE
    return automatic_promotion_state(
        evidence_asset_count,
        minimum_evidence=minimum_evidence,
    )
