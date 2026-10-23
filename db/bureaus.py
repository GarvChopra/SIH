"""Bureaus / cells / regional offices."""
from db.table import Table

T = Table("bureaus", ("id", "code", "name", "region"))


def create(code, name, region=None):
    if T.get_by("code", code):
        raise ValueError(f"bureau {code!r} already exists")
    return T.insert(code=code, name=name, region=region)


def get(bid):
    return T.get(bid)


def get_by_code(code):
    return T.get_by("code", code)


def list_all():
    return sorted(T.all(), key=lambda b: b["code"])
