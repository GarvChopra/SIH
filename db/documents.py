"""Supporting-document metadata (files live under static/uploads/cases)."""
from db.table import Table

T = Table("documents", ("id", "complaint_id", "filename", "stored_name", "bureau_id",
                        "uploaded_by", "uploaded_at"), ts_create="uploaded_at")


def add(complaint_id, filename, stored_name, bureau_id, uploaded_by):
    return T.insert(complaint_id=complaint_id, filename=filename, stored_name=stored_name,
                    bureau_id=bureau_id, uploaded_by=uploaded_by)


def get(did):
    return T.get(did)


def for_complaint(complaint_id):
    return [d for d in T.all() if d["complaint_id"] == complaint_id]
