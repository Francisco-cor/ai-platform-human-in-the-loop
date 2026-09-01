"""Tests for LockManager abstraction — F1-3."""

from procurement_platform.infra.locks.manager import (
    MemoryLockManager,
    get_lock_manager,
    reset_lock_manager,
)


def test_memory_lock_manager_acquire_release():
    mgr = MemoryLockManager()
    assert mgr.acquire("key1", blocking=False) is True
    assert mgr.is_locked("key1") is True
    # second acquire non-blocking should fail
    assert mgr.acquire("key1", blocking=False) is False
    mgr.release("key1")
    assert mgr.is_locked("key1") is False
    assert mgr.acquire("key1", blocking=False) is True
    mgr.release("key1")
    mgr.clear()


def test_memory_lock_manager_clear():
    mgr = MemoryLockManager()
    mgr.acquire("k", blocking=False)
    mgr.clear()
    assert mgr.is_locked("k") is False


def test_get_lock_manager_singleton():
    reset_lock_manager()
    m1 = get_lock_manager()
    m2 = get_lock_manager()
    assert m1 is m2
    reset_lock_manager()


def test_orchestrator_lock_via_manager():
    reset_lock_manager()
    from procurement_platform.workflows.orchestrator import (
        _acquire_execution_lock,
        _release_execution_lock,
    )

    assert _acquire_execution_lock("exec_test_1", blocking=False) is True
    # second should fail non-blocking
    assert _acquire_execution_lock("exec_test_1", blocking=False) is False
    _release_execution_lock("exec_test_1")
    assert _acquire_execution_lock("exec_test_1", blocking=False) is True
    _release_execution_lock("exec_test_1")
    reset_lock_manager()


def test_approval_lock_via_manager():
    reset_lock_manager()
    from procurement_platform.approvals.service import _get_lock

    # legacy still works
    lock = _get_lock("exec_2")
    assert lock.acquire(blocking=False) is True
    assert lock.locked() is True
    lock.release()
    reset_lock_manager()
