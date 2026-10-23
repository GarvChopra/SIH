"""Offline lexical similarity: the always-on fallback and the evidence shown to officials.

Pure Python, no network, no model. Deliberately transparent: text is reduced to
"concepts" through a small, readable domain lexicon (so "charged", "charges" and
"fees" count as the same thing, and so do "excess" and "unauthorized"), then
compared with a log-damped term-frequency cosine. The shared concepts are returned
so an official can see exactly why two complaints were matched.
"""
from __future__ import annotations

import math
import re

# canonical concept -> the surface words that mean it in complaints about institutions
CONCEPTS = {
    "fee": "fee fees charge charges charged charging levy levied payment payments paid pay "
           "collect collected collecting collection money amount amounts rs rupees",
    "unauthorized": "unauthorized unauthorised illegal illegally unlawful excess excessive extra "
                    "additional arbitrary undue overcharge overcharged overcharging unfair "
                    "unapproved improper",
    "exam": "exam exams examination examinations semester semesters test tests hall ticket tickets",
    "faculty": "faculty teacher teachers professor professors lecturer lecturers staff teaching "
               "instructor instructors",
    "qualification": "qualification qualifications qualified unqualified degree degrees phd "
                     "credential credentials eligibility eligible ug pg",
    "infrastructure": "infrastructure building buildings laboratory laboratories lab labs library "
                      "classroom classrooms hostel hostels facility facilities equipment toilets "
                      "water electricity campus",
    "admission": "admission admissions admit admitted seat seats quota counselling counseling "
                 "enrolment enrollment capitation donation",
    "approval": "approval approved affiliation permission recognition licence license accreditation "
                "lapsed expired renewal",
    "harass": "harassment harass harassed abuse abused ragging bullying misbehave misbehaviour "
              "misbehavior threat threatened threats",
    "certificate": "certificate certificates marksheet marksheets transcript transcripts document "
                   "documents original originals",
    "withhold": "withheld withhold withholding retained retain retaining refuse refused refusing "
                "detained detain",
    "refund": "refund refunds refunded reimburse reimbursement",
}
_CANON = {w: c for c, words in CONCEPTS.items() for w in words.split()}

_STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being
this that these those it its they them their he she his her we our you your i me my not no nor so
than then there here have has had do does did done will would can could should may might must
about over under into onto out up down off per via also very any all some such each other more most
many much one two three students student institution institutions college colleges university
institute complaint complaints complain complained reported report alleged alleging allege said says
matter case issue issues being getting get got made make makes making demand demanded demanding
told asked ask asking required requires require requirement requirements norms minimum""".split())

_SUFFIXES = ("ations", "ation", "ingly", "ments", "ment", "ings", "ing", "ies", "ied", "ed", "es", "s")


def _stem(w: str) -> str:
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def concepts(text: str) -> list:
    """Meaningful tokens: lexicon concepts first, light stems otherwise."""
    out = []
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in _CANON:
            out.append(_CANON[w])
        elif w not in _STOP and len(w) > 2:
            out.append(_stem(w))
    return out


def _vector(tokens: list) -> dict:
    tf = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return {t: 1.0 + math.log(n) for t, n in tf.items()}


def similarity(a: str, b: str):
    """(score 0..1, shared terms sorted by weight). Symmetric."""
    va, vb = _vector(concepts(a)), _vector(concepts(b))
    if not va or not vb:
        return 0.0, []
    shared = [t for t in va if t in vb]
    dot = sum(va[t] * vb[t] for t in shared)
    na = math.sqrt(sum(x * x for x in va.values()))
    nb = math.sqrt(sum(x * x for x in vb.values()))
    score = dot / (na * nb) if na and nb else 0.0
    shared.sort(key=lambda t: -(va[t] * vb[t]))
    return round(score, 4), shared
