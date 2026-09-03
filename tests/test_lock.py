# -*- coding:utf-8 -*-

import unittest
from yyds_mdns.core.lock import WorkerLock


class TestWorkerLock(unittest.TestCase):
    def test_lock_acquire_and_release(self):
        lock1 = WorkerLock("test_service_key_1")
        lock2 = WorkerLock("test_service_key_1")

        try:
            acquired1 = lock1.acquire()
            self.assertTrue(acquired1)

            # Second lock with same key in another instance should fail
            acquired2 = lock2.acquire()
            self.assertFalse(acquired2)

            lock1.release()

            # Now lock2 should be able to acquire
            acquired2_after = lock2.acquire()
            self.assertTrue(acquired2_after)
            lock2.release()
        finally:
            lock1.release()
            lock2.release()

    def test_lock_context_manager(self):
        with WorkerLock("test_context_key") as acquired:
            self.assertTrue(acquired)


if __name__ == "__main__":
    unittest.main()
