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


def _get_user_scope() -> str:
    """Return user-scoped token to avoid cross-user /tmp permission collisions."""
    if hasattr(os, "geteuid"):
        return str(os.geteuid())
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    return "".join(c if c.isalnum() else "_" for c in user)


class WorkerLock:
    """
    Lightweight cross-process lock.
    Ensures that under multi-worker deployment (e.g. uvicorn --workers 4),
    only the primary worker registers the mDNS service.

    Safety guarantees:
    - Scoped per OS user to avoid multi-user /tmp EACCES permission collisions.
    - Does not unlink the lockfile after unlock, avoiding the POSIX flock+unlink inode race.
    - Seeks to byte 0 before Windows msvcrt unlock so byte-range lock releases cleanly.
    - Tracks owner PID so forked children never accidentally unlock parent's flock.
    """

    def __init__(self, key: str):
        safe_key = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in key)
        user_scope = _get_user_scope()
        self.lock_file_path = os.path.join(
            tempfile.gettempdir(), f"yyds_mdns_{user_scope}_{safe_key}.lock"
        )
        self._fd: Optional[int] = None
        self._is_locked = False
        self._owner_pid: Optional[int] = None

    def acquire(self) -> bool:
        """
        Attempt non-blocking lock acquisition.
        Returns True if acquired, False if already held by another worker/process.
        """
        if self._is_locked and self._owner_pid == os.getpid():
            return True

        fd: Optional[int] = None
        try:
            fd = os.open(
                self.lock_file_path,
                os.O_CREAT | os.O_RDWR,
                0o600,
            )
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

            # Truncate and write current PID into lock file for diagnostics
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            except OSError:
                pass

            self._fd = fd
            self._is_locked = True
            self._owner_pid = os.getpid()
            return True
        except (OSError, IOError):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._fd = None
            self._is_locked = False
            self._owner_pid = None
            return False

    def release(self) -> None:
        """Release lock and clean up file descriptor safely (fork-aware)."""
        if not self._is_locked or self._fd is None:
            return

        is_owner = self._owner_pid == os.getpid()
        if is_owner:
            try:
                if fcntl:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                elif msvcrt:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass

        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        self._is_locked = False
        self._owner_pid = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
