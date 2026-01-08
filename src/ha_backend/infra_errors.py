from __future__ import annotations

import errno as errno_module
from typing import Iterator

STORAGE_INFRA_ERRNOS: frozenset[int] = frozenset(
    {
        # FUSE/sshfs stale mount (seen in incidents).
        107,  # ENOTCONN: "Transport endpoint is not connected"
        # Broad "I/O error" signals where treating as infra is usually correct.
        errno_module.EIO,
        errno_module.ETIMEDOUT,
    }
)


def iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """
    Yield an exception and its causal/context chain (best-effort).

    Useful when an OSError is wrapped by a higher-level exception.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None:
        cur_id = id(cur)
        if cur_id in seen:
            break
        seen.add(cur_id)
        yield cur
        cur = cur.__cause__ or cur.__context__


def is_storage_infra_errno(errno: int | None) -> bool:
    if errno is None:
        return False
    try:
        return int(errno) in STORAGE_INFRA_ERRNOS
    except Exception:
        return False


def is_storage_infra_error(exc: BaseException) -> bool:
    """
    Return True if the exception indicates a storage/mount infrastructure failure.
    """
    for item in iter_exception_chain(exc):
        if isinstance(item, OSError) and is_storage_infra_errno(item.errno):
            return True
    return False
