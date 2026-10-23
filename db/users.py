"""User accounts: bureau staff and super admins."""
from db.table import Table
from domain.constants import ROLES

COLS = ("id", "username", "display_name", "role", "pin_hash", "department", "contact",
        "bureau_id", "region", "is_active", "created_at", "created_by", "must_change_pin")
T = Table("users", COLS, defaults={"is_active": True, "must_change_pin": False})

VALID_ROLES = tuple(ROLES)


def get_by_username(username: str):
    return T.get_by("username", (username or "").lower().strip())


def get_by_id(uid):
    return T.get(uid)


def list_staff():
    return [u for u in list_all() if u["role"] != "citizen"]


def list_all(role=None, bureau_id=None):
    rows = sorted(T.all(), key=lambda u: u["display_name"].lower())
    return [u for u in rows if (role is None or u["role"] == role)
            and (bureau_id is None or u["bureau_id"] == bureau_id)]


def create(username, display_name, role, pin_hash, department=None, contact=None,
           created_by=None, must_change_pin=False, bureau_id=None, region=None):
    username = username.lower().strip()
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role {role!r}")
    if role not in ("super_admin", "citizen") and bureau_id is None:
        raise ValueError(f"role {role!r} needs a bureau")
    if get_by_username(username):
        raise ValueError(f"username {username!r} already exists")
    return T.insert(username=username, display_name=display_name, role=role,
                    pin_hash=pin_hash, department=department, contact=contact,
                    bureau_id=bureau_id, region=region, created_by=created_by,
                    must_change_pin=bool(must_change_pin))


def set_active(uid, active: bool) -> None:
    T.update(uid, is_active=active)


def set_pin(uid, pin_hash: str) -> None:
    """Set a new PIN. Always clears must_change_pin; the user just chose one."""
    T.update(uid, pin_hash=pin_hash, must_change_pin=False)


def set_must_change_pin(uid, flag: bool) -> None:
    T.update(uid, must_change_pin=bool(flag))
