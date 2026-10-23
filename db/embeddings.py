"""Stored complaint embeddings (JSON float arrays; pgvector deliberately skipped)."""
from db.table import Table

T = Table("complaint_embeddings", ("id", "complaint_id", "model", "dimension", "vector",
                                   "text_version", "created_at"), json_cols=("vector",))


def get(complaint_id, text_version=None):
    rows = [e for e in T.all() if e["complaint_id"] == complaint_id
            and (text_version is None or e["text_version"] == text_version)]
    return rows[-1] if rows else None


def store(complaint_id, model, vector, text_version):
    return T.insert(complaint_id=complaint_id, model=model, dimension=len(vector),
                    vector=list(vector), text_version=text_version)
