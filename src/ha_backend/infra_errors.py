from __future__ import annotations

import errno as errno_module
from pathlib import Path
from typing import Iterator

STORAGE_INFRA_ERRNOS: frozenset[int] = frozenset(
    {
        # FUSE/sshfs stale mount (seen in incidents).
        107,  # ENOTCONN: "Transport endpoint is not connected"
        # Broad "I/O error" signals where treating as infra is usually correct.
        errno_module.EIO,
        errno_module.ETIMEDOUT,
        # Network errors that indicate infrastructure/connectivity issues.
        101,  # ENETUNREACH: "Network is unreachable"
        111,  # ECONNREFUSED: "Connection refused"
        113,  # EHOSTUNREACH: "No route to host"
        # Storage capacity errors.
        errno_module.ENOSPC,  # 28: "No space left on device"
        # Stale NFS handles (common on network filesystems).
        116,  # ESTALE: "Stale file handle"
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


def _path_is_within(candidate: Path, base: Path) -> bool:
    """
    Return True if candidate is base or a descendant of base.
    """
    try:
        c = candidate.resolve()
    except Exception:
        c = candidate
    try:
        b = base.resolve()
    except Exception:
        b = base
    return c == b or b in c.parents


def is_output_dir_write_infra_error(exc: BaseException, *, output_dir: Path) -> bool:
    """
    Return True if the exception indicates the job output directory is not writable
    due to infrastructure or filesystem state (e.g. permissions/ownership on a
    tiered SSHFS mount).

    We intentionally keep this heuristic narrow:
    - Only EACCES/EPERM
    - Only when the failing path is within the configured output_dir
    """
    for item in iter_exception_chain(exc):
        if not isinstance(item, OSError):
            continue
        if item.errno not in {errno_module.EACCES, errno_module.EPERM}:
            continue

        raw_path = getattr(item, "filename", None)
        if not raw_path:
            continue
        try:
            path = Path(str(raw_path))
        except Exception:
            continue

        if _path_is_within(path, output_dir):
            return True
    return False
