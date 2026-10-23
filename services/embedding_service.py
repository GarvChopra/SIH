"""Optional semantic embeddings via Cohere embed-v4.0 (pattern from NEVO's cohere_client).

Everything here is optional at runtime: with no COHERE_API_KEY, no `cohere`
package, or any API failure, `embed()` returns None and the overlap engine falls
back to the offline lexical matcher. Every call is logged to the audit log
(model, latency, success) so AI use stays auditable.

Complaint-to-complaint matching is symmetric, so BOTH sides are embedded with the
same input_type ("search_document"). NEVO's search_query/search_document split is
for asymmetric query-vs-corpus search and would skew these scores.
"""
from __future__ import annotations

import logging
import math
import time

from config import Config
from db import audit
from domain.constants import EMBED_DIMENSION, EMBED_MODEL

log = logging.getLogger("samanvay.embed")

INPUT_TYPE = "search_document"
_client_factory = None          # tests inject a fake: callable(api_key) -> client


def available() -> bool:
    if _client_factory is not None:
        return True
    if not Config.COHERE_API_KEY:
        return False
    try:
        import cohere  # noqa: F401
        return True
    except ImportError:
        return False


def _client():
    if _client_factory is not None:
        return _client_factory(Config.COHERE_API_KEY)
    import cohere
    return cohere.Client(api_key=Config.COHERE_API_KEY)


def embed(text: str, *, complaint_id=None):
    """Return a 1536-float vector, or None when embeddings are unavailable/failed."""
    if not available():
        return None
    started = time.time()
    ok, err, vector = False, None, None
    try:
        resp = _client().embed(texts=[text], model=EMBED_MODEL, input_type=INPUT_TYPE,
                               embedding_types=["float"])
        vectors = resp.embeddings.float
        if not vectors:
            raise ValueError("Cohere returned an empty embeddings list")
        vector = list(vectors[0])
        if len(vector) != EMBED_DIMENSION:                  # hard fail, as in NEVO
            raise ValueError(f"embedding dimension mismatch: expected {EMBED_DIMENSION}, "
                             f"got {len(vector)}")
        ok = True
    except Exception as e:  # noqa: BLE001 - any failure means "fall back to lexical"
        err = f"{type(e).__name__}: {e}"[:200]
        vector = None
        log.warning("embedding failed, using lexical fallback: %s", err)
    finally:
        try:
            audit.add("system", "ai.embed", target_type="complaint", target_id=complaint_id,
                      detail={"model": EMBED_MODEL, "success": ok, "error": err,
                              "latency_ms": int((time.time() - started) * 1000)})
        except Exception:  # noqa: BLE001 - logging must never break matching
            pass
    return vector


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
