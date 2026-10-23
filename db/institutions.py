"""Institution registry. `aicte_permanent_id` is the only automatic join key."""
import time

from db.table import Table

T = Table("institutions", ("id", "aicte_permanent_id", "name", "address", "region",
                           "metadata", "created_at"), json_cols=("metadata",))
ALIASES = Table("institution_aliases", ("id", "institution_id", "alias", "status",
                                        "complaint_id", "created_at"))


def create(aicte_permanent_id, name, address=None, region=None, metadata=None):
    aid = (aicte_permanent_id or "").strip()
    if not aid or not (name or "").strip():
        raise ValueError("AICTE permanent ID and name are required")
    if T.get_by("aicte_permanent_id", aid):
        raise ValueError(f"institution {aid!r} already exists")
    return T.insert(aicte_permanent_id=aid, name=name.strip(), address=address,
                    region=region, metadata=metadata or {})


def get(iid):
    return T.get(iid)


def get_by_aicte_id(aid):
    return T.get_by("aicte_permanent_id", (aid or "").strip())


def list_all():
    return sorted(T.all(), key=lambda i: i["name"].lower())


# -- aliases ----------------------------------------------------------------

def add_alias(institution_id, alias, status="suggested", complaint_id=None):
    alias = (alias or "").strip()
    if status not in ("suggested", "approved"):
        raise ValueError(status)
    existing = next((a for a in ALIASES.where(institution_id=institution_id)
                     if a["alias"].lower() == alias.lower()), None)
    if existing:
        if status == "approved" and existing["status"] != "approved":
            return ALIASES.update(existing["id"], status="approved")
        return existing
    return ALIASES.insert(institution_id=institution_id, alias=alias, status=status,
                          complaint_id=complaint_id)


def get_alias(aid):
    return ALIASES.get(aid)


def set_alias_status(aid, status):
    return ALIASES.update(aid, status=status)


def delete_alias(aid):
    ALIASES.delete(aid)


def list_aliases(institution_id=None, status=None):
    return [a for a in ALIASES.all()
            if (institution_id is None or a["institution_id"] == institution_id)
            and (status is None or a["status"] == status)]
