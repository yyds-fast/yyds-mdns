# -*- coding:utf-8 -*-

import os
import unittest
from unittest.mock import patch

from yyds_mdns.core.lock import WorkerLock


class TestWorkerLock(unittest.TestCase):
    def test_lock_acquire_and_release(self):
        lock1 = WorkerLock("test_service_key_1")
        lock2 = WorkerLock("test_service_key_1")

        try:
            acquired1 = lock1.acquire()
            self.assertTrue(acquired1)

            if os.name != "nt":
                # On Windows, msvcrt.locking is mandatory and blocks reads from a second handle
                with open(lock1.lock_file_path, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), str(os.getpid()))

            # Second lock with same key in another instance should fail
            acquired2 = lock2.acquire()
            self.assertFalse(acquired2)

            lock1.release()

            # Now lock2 should be able to acquire cleanly
            acquired2_after = lock2.acquire()
            self.assertTrue(acquired2_after)
            lock2.release()
        finally:
            lock1.release()
            lock2.release()

    def test_lock_context_manager(self):
        with WorkerLock("test_context_key") as acquired:
            self.assertTrue(acquired)

    @unittest.skipIf(os.name == "nt", "POSIX fork semantics only")
    def test_forked_child_does_not_unlock_parent_flock(self):
        lock1 = WorkerLock("test_fork_safety_key")
        lock2 = WorkerLock("test_fork_safety_key")
        try:
            self.assertTrue(lock1.acquire())
            # Simulate a forked child process holding a duplicated FD
            dup_fd = os.dup(lock1._fd)  # type: ignore[arg-type]
            child_lock = WorkerLock("test_fork_safety_key")
            child_lock._fd = dup_fd
            child_lock._is_locked = True
            child_lock._owner_pid = lock1._owner_pid

            with patch(
                "yyds_mdns.core.lock.os.getpid", return_value=os.getpid() + 99999
            ):
                child_lock.release()

            # Parent lock must STILL be held exclusively!
            self.assertFalse(lock2.acquire())
        finally:
            lock1.release()
            lock2.release()

    @unittest.skipIf(os.name == "nt", "POSIX fork semantics only")
    def test_forked_child_acquire_closes_inherited_fd_no_leak(self):
        parent_lock = WorkerLock("test_fork_acquire_key")
        other_lock = WorkerLock("test_fork_acquire_key")
        try:
            self.assertTrue(parent_lock.acquire())
            inherited_fd = os.dup(parent_lock._fd)  # type: ignore[arg-type]

            child_lock = WorkerLock("test_fork_acquire_key")
            child_lock._fd = inherited_fd
            child_lock._is_locked = True
            child_lock._owner_pid = parent_lock._owner_pid

            with patch(
                "yyds_mdns.core.lock.os.getpid", return_value=os.getpid() + 88888
            ):
                # Child attempts acquire while parent holds lock -> must return False AND close inherited_fd!
                self.assertFalse(child_lock.acquire())

            # Verify inherited_fd was closed so releasing parent_lock truly frees the kernel flock
            parent_lock.release()
            self.assertTrue(other_lock.acquire())
        finally:
            parent_lock.release()
            other_lock.release()


if __name__ == "__main__":
    unittest.main()
