"""Matrix-LAN role policy. Server-side authorization; never trust UI role claims.

The bootstrap owner is fixed at installation time. New registrations must not
be able to claim superuser status, even if the owner account is later deleted.
"""
from dataclasses import dataclass
from enum import IntEnum


class Role(IntEnum):
    USER = 10
    ADMIN = 20
    SUPERUSER = 30


@dataclass(frozen=True)
class Principal:
    mxid: str
    role: Role


PERMISSIONS = {
    "chats.read_own": Role.USER,
    "accounts.manage_own": Role.USER,
    "preferences.manage_own": Role.USER,
    "bridges.manage": Role.ADMIN,
    "server.status": Role.ADMIN,
    "users.manage": Role.ADMIN,
    "roles.assign_admin": Role.SUPERUSER,
    "server.settings": Role.SUPERUSER,
    "server.backups": Role.SUPERUSER,
    "tailscale.configure": Role.SUPERUSER,
}


def can(principal: Principal, action: str, *, target_mxid: str | None = None) -> bool:
    """Deny unknown permissions and enforce ownership for personal resources."""
    required = PERMISSIONS.get(action)
    if required is None or principal.role < required:
        return False
    if action.endswith("_own") and target_mxid != principal.mxid:
        return False
    return True


def bootstrap_role(mxid: str, configured_owner: str) -> Role:
    """Only the immutable installer-selected owner can be initial superuser.

    Caller must persist this grant exactly once in a transactional user store.
    Never invoke this function to elevate arbitrary registrations.
    """
    return Role.SUPERUSER if mxid == configured_owner else Role.USER
