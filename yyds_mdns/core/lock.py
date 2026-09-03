# -*- coding:utf-8 -*-

"""
Multi-worker process lock to prevent duplicate mDNS broadcasts.
"""

import os
import tempfile
from typing import Optional

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


class WorkerLock:
    """
    Lightweight cross-process lock.
    Ensures that under multi-worker deployment (e.g. uvicorn --workers 4),
    only the primary worker registers the mDNS service.
    """

    def __init__(self, key: str):
        safe_key = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in key)
        self.lock_file_path = os.path.join(
            tempfile.gettempdir(), f"yyds_mdns_{safe_key}.lock"
        )
        self._fd: Optional[int] = None
        self._is_locked = False

    def acquire(self) -> bool:
        """
        Attempt non-blocking lock acquisition.
        Returns True if acquired, False if already held by another worker/process.
        """
        if self._is_locked:
            return True

        try:
            self._fd = os.open(
                self.lock_file_path,
                os.O_CREAT | os.O_RDWR,
                0o600,
            )
            if fcntl:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)

            # Write current PID into lock file for debugging
            os.write(self._fd, f"{os.getpid()}\n".encode("utf-8"))
            self._is_locked = True
            return True
        except (OSError, IOError):
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            self._is_locked = False
            return False

    def release(self) -> None:
        """Release lock and clean up file descriptor."""
        if not self._is_locked or self._fd is None:
            return

        try:
            if fcntl:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            elif msvcrt:
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        self._is_locked = False

        try:
            if os.path.exists(self.lock_file_path):
                os.unlink(self.lock_file_path)
        except OSError:
            pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
