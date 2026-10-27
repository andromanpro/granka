"""Nonblocking OS locks on stable files; automatically released after a crash."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def file_lock(path, message, error=ValueError):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Never unlink: another process could lock a different inode at this path.
    with path.open('a+b')as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise error(message)from None
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
