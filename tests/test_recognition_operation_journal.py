from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from iPhoto.recognition.mutation_coordinator import RecognitionMutationCoordinator
from iPhoto.recognition.operation_journal import RecognitionOperationJournal


def _hold_unfinished_operation(root, pipe):
    owner = RecognitionMutationCoordinator(root)
    with owner.mutation_scope():
        operation_id = owner.prepare("pet_rename", {"pet_id": "a", "name": "Miso"})
        pipe.send(operation_id)
        if pipe.poll(15):
            pipe.recv()
        os._exit(0)  # Exercise process death before the applying transition.


def _check_forked_leases(owner, root, pipe):
    new_owner = RecognitionMutationCoordinator(root)
    for _ in range(2):
        acquired = []
        for coordinator in (owner, new_owner):
            with coordinator.try_mutation_scope() as held:
                acquired.append(held)
        pipe.send(acquired)
        if not pipe.poll(10):
            return
        pipe.recv()


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="POSIX fork")
@pytest.mark.parametrize("alias_name", ["Library", "library"], ids=["same-path", "case-alias"])
def test_fork_child_can_only_acquire_after_parent_releases(tmp_path, alias_name):
    root = tmp_path / "Library"
    root.mkdir()
    alias = tmp_path / alias_name
    if not alias.is_dir():
        pytest.skip("case-sensitive filesystem")

    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe()
    owner = RecognitionMutationCoordinator(root)
    alias_owner = RecognitionMutationCoordinator(alias)
    process = ctx.Process(target=_check_forked_leases, args=(owner, root, child))
    try:
        with owner.mutation_scope():
            with alias_owner.try_mutation_scope() as acquired:
                assert acquired
            alias_owner.close()
            process.start()
            child.close()
            assert parent.poll(5)
            assert parent.recv() == [False, False]
        parent.send("retry")
        assert parent.poll(5)
        assert parent.recv() == [True, True]
        parent.send("finish")
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        alias_owner.close()
        owner.close()


def test_competing_process_cannot_recover_live_owner_and_recovers_after_exit(tmp_path):
    import numpy as np

    from iPhoto.bootstrap.library_pet_service import create_pet_service
    from iPhoto.people.index_coordinator import PeopleIndexCoordinator
    from iPhoto.pets.records import PetMutationFailure, PetRecord
    from iPhoto.pets.repository_utils import utc_now_iso

    owner = RecognitionMutationCoordinator(tmp_path)
    pets = create_pet_service(tmp_path, mutation_coordinator=owner)
    now = utc_now_iso()
    repo = pets.repository()
    repo.initialize()
    pet = PetRecord(
        pet_id="a", name=None, key_detection_id="d", detection_count=1,
        center_embedding=np.array([1.0], dtype=np.float32), embedding_dim=1,
        created_at=now, updated_at=now,
    )
    repo.state_repository.ensure_runtime_candidates([pet], [])

    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_hold_unfinished_operation, args=(tmp_path, child))
    process.start()
    child.close()
    try:
        assert parent.poll(10)
        operation_id = parent.recv()
        competing = RecognitionMutationCoordinator(tmp_path)
        people = PeopleIndexCoordinator(tmp_path, mutation_coordinator=competing)
        assert not competing.recover_pending()
        assert competing.try_prepare("people_rename", {}) is None
        assert pets.merge_pets("a", "b").failure is PetMutationFailure.RECOVERY_PENDING
        assert not people.set_person_hidden("a", True)
        assert competing.unfinished_head().operation_id == operation_id
        assert competing.unfinished_head().state == "prepared"
        people.close()
        competing.close()
        other = RecognitionMutationCoordinator(tmp_path / "other")
        assert other.recover_pending()
        other.close()
        parent.send("exit")
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()

    assert owner.recover_pending()
    assert owner.unfinished() == ()
    assert owner.pending_events() == ()
    assert repo.state_repository.get_profile("a").name == "Miso"
    assert pets.set_pet_hidden("a", True)
    pets.shutdown()
    owner.close()


def test_unfinished_operations_follow_insertion_sequence(tmp_path: Path) -> None:
    journal = RecognitionOperationJournal(tmp_path / "operations.db")

    inserted = [journal.prepare("test", {"index": index}) for index in range(20)]

    pending = journal.unfinished()
    assert [operation.operation_id for operation in pending] == inserted
    assert [operation.sequence for operation in pending] == list(range(1, 21))


def test_legacy_operations_schema_migrates_deterministically(tmp_path: Path) -> None:
    db_path = tmp_path / "operations.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO operations (
                operation_id, kind, state, payload_json, created_at, updated_at
            ) VALUES (?, 'test', 'applying', ?, '2026-07-27 10:00:00',
                      '2026-07-27 10:00:00')
            """,
            [(operation_id, json.dumps({"id": operation_id})) for operation_id in ("c", "a", "b")],
        )

    journal = RecognitionOperationJournal(db_path)

    assert [operation.operation_id for operation in journal.unfinished()] == ["a", "b", "c"]
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(operations)")}
    assert "sequence" in columns


def test_try_prepare_allows_only_one_global_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "operations.db"
    RecognitionOperationJournal(db_path)

    def prepare(index: int) -> str | None:
        return RecognitionOperationJournal(db_path).try_prepare(
            f"owner-{index}",
            {"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(prepare, range(8)))

    assert sum(result is not None for result in results) == 1
    assert len(RecognitionOperationJournal(db_path).unfinished()) == 1


def test_transition_compare_and_set_allows_only_expected_state(tmp_path: Path) -> None:
    journal = RecognitionOperationJournal(tmp_path / "operations.db")
    operation_id = journal.prepare("test", {})

    assert journal.transition(
        operation_id,
        "applying",
        expected_state="prepared",
    )
    assert not journal.transition(
        operation_id,
        "committed",
        expected_state="prepared",
    )


def test_legacy_outbox_schema_migrates_to_pending_event_id(tmp_path: Path) -> None:
    db_path = tmp_path / "operations.db"
    journal = RecognitionOperationJournal(db_path)
    operation_id = journal.prepare("test", {})
    journal.transition(operation_id, "applying")
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE event_outbox")
        connection.execute(
            """
            CREATE TABLE event_outbox (
                operation_id TEXT PRIMARY KEY,
                event_json TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            "INSERT INTO event_outbox VALUES (?, '{\"changed\":true}', 0)",
            (operation_id,),
        )
        connection.execute(
            "UPDATE operations SET state = 'committed' WHERE operation_id = ?",
            (operation_id,),
        )

    reopened = RecognitionOperationJournal(db_path)

    assert reopened.pending_events()[0].event_id == operation_id
    assert reopened.pending_events()[0].event == {"changed": True}
    assert reopened.mark_dispatched(operation_id)
    assert reopened.unfinished() == ()


def test_mutation_coordinator_recovers_registered_operations_fifo(tmp_path: Path) -> None:
    coordinator = RecognitionMutationCoordinator(tmp_path)
    recovered: list[int] = []

    def recover(operation) -> bool:
        recovered.append(int(operation.payload["index"]))
        return coordinator.transition(
            operation.operation_id,
            "finalized",
            expected_state="applying",
        )

    coordinator.register_recovery_handler({"legacy-test"}, recover)
    for index in range(3):
        operation_id = coordinator.prepare("legacy-test", {"index": index})
        assert coordinator.transition(operation_id, "applying", expected_state="prepared")

    assert coordinator.recover_pending()
    assert recovered == [0, 1, 2]


def test_live_mutation_lease_prevents_second_owner_from_recovering_active_work(
    tmp_path: Path,
) -> None:
    active_owner = RecognitionMutationCoordinator(tmp_path)
    recovery_owner = RecognitionMutationCoordinator(tmp_path)
    recovery_called = threading.Event()

    def recover(operation) -> bool:
        recovery_called.set()
        return recovery_owner.transition(operation.operation_id, "finalized")

    recovery_owner.register_recovery_handler({"pet_rename"}, recover)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with active_owner.mutation_scope():
            operation_id = active_owner.try_prepare("pet_rename", {"pet_id": "pet-a"})
            assert operation_id is not None
            recovery = executor.submit(recovery_owner.recover_pending)

            assert not recovery_called.wait(timeout=0.2)
            assert not recovery.done()
            active_owner.transition(
                operation_id,
                "finalized",
                expected_state="applying",
            )

        assert recovery.result(timeout=2.0) is True
    assert not recovery_called.is_set()


def test_mutation_coordinator_unknown_kind_blocks_head(tmp_path: Path) -> None:
    coordinator = RecognitionMutationCoordinator(tmp_path)
    operation_id = coordinator.prepare("future-unknown-kind", {})
    assert coordinator.transition(operation_id, "applying", expected_state="prepared")

    assert not coordinator.recover_pending()
    head = coordinator.unfinished_head()
    assert head is not None and head.operation_id == operation_id
    assert "No recovery handler" in str(coordinator.recovery_error)


def test_mutation_coordinator_dispatches_stable_outbox_event(tmp_path: Path) -> None:
    coordinator = RecognitionMutationCoordinator(tmp_path)
    operation_id = coordinator.try_prepare("pet_rename", {"pet_id": "pet-a"})
    assert operation_id is not None
    event_id = coordinator.commit_outbox(operation_id, {"changed": ["pet-a"]})
    delivered = []

    coordinator.subscribe(delivered.append)

    assert [event.event_id for event in delivered] == [event_id]
    assert delivered[0].operation_id == operation_id
    assert coordinator.unfinished() == ()


def test_dispatch_failure_leaves_committed_event_for_stable_replay(tmp_path: Path) -> None:
    coordinator = RecognitionMutationCoordinator(tmp_path)
    operation_id = coordinator.try_prepare("pet_rename", {"pet_id": "pet-a"})
    assert operation_id is not None

    def fail_dispatch() -> None:
        raise RuntimeError("injected dispatch crash")

    with pytest.raises(RuntimeError, match="injected dispatch crash"):
        coordinator.commit_and_dispatch(
            operation_id,
            {"changed": ["pet-a"]},
            fail_dispatch,
        )

    pending = coordinator.pending_events()
    assert [event.event_id for event in pending] == [operation_id]
    delivered = []
    coordinator.subscribe(delivered.append)
    assert [event.event_id for event in delivered] == [operation_id]
    assert coordinator.unfinished() == ()
