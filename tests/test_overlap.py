"""Overlap detection, classification and human review."""
import json
import os

import pytest

from db import audit, case_actions, case_links, complaints, notifications
from domain.constants import LEXICAL_HIGH, LEXICAL_MEDIUM
from services import (case_view_service as views, complaint_service as cs, embedding_service,
                      lexical, overlap_service as ov)
from services.errors import NotFound, PermissionDenied, ValidationError

PGRC_TEXT = ("Students are being charged unauthorized examination fees.",
             "Students are being charged unauthorized examination fees.")
RB_TEXT = ("The institution is collecting excess examination charges.",
           "The institution is collecting excess examination charges.")


def file(actor, who, text, cat="Unauthorized fees", aicte="123456"):
    return cs.create(actor(who), aicte_id=aicte, title=text[0], description=text[1], category=cat)


@pytest.fixture()
def planted(actor, inst):
    a = file(actor, "pgrc.officer", PGRC_TEXT)
    b = file(actor, "rb.officer", RB_TEXT)
    return a, b


def test_planted_exam_fee_pair_raises_overlap_alert(actor, planted):
    a, b = planted
    link = case_links.get_pair(a["id"], b["id"])
    assert link["relationship_type"] == "overlapping_proceeding"
    assert link["review_status"] == "pending"
    ev = link["evidence"]
    assert set(ev["shared_terms"]) >= {"fee", "unauthorized", "exam"}
    assert ev["bureaus"] == ["PGRC", "RB"] and ev["aicte_id"] == "123456"
    assert ev["message"] == ("Both complaints concern AICTE institution 123456 and describe similar "
                             "unauthorized fees allegations. They are assigned to PGRC and RB.")
    assert ov.pending_alert_count() == 1
    # both bureau admins and the super admin are told; both cases record the flag
    for who in ("pgrc.admin", "rb.admin", "superadmin"):
        assert any(n["type"] == "overlap" for n in notifications.for_user(actor(who)["id"])), who
    for c in (a, b):
        assert "overlap_flagged" in [x["action_type"] for x in case_actions.for_complaint(c["id"])]
    assert {r["case_number"] for r in views.list_cases(actor("rb.admin"), {"overlap": True})} \
        == {a["case_number"], b["case_number"]}


def test_unrelated_complaints_at_same_institution_do_not_alert(actor, inst):
    a = file(actor, "pgrc.officer", ("Faculty lack required qualifications",
             "Faculty members do not hold the required qualifications for engineering courses."),
             "Faculty qualifications")
    b = file(actor, "rb.officer", ("Infrastructure deficiency",
             "Laboratories and library lack the minimum infrastructure required by norms."),
             "Infrastructure deficiency")
    link = case_links.get_pair(a["id"], b["id"])
    assert link["relationship_type"] == "same_institution"        # stored, shown on profile, never alerts
    assert ov.pending_alert_count() == 0
    assert ov.review_queue(actor("superadmin")) == []
    assert not any(n["type"] == "overlap" for n in notifications.for_user(actor("rb.admin")["id"]))
    assert views.list_cases(actor("superadmin"), {"overlap": True}) == []


def test_same_bureau_high_similarity_is_possible_duplicate_not_alert(actor, inst):
    a = file(actor, "pgrc.officer", PGRC_TEXT)
    b = file(actor, "pgrc.officer2", RB_TEXT)
    assert case_links.get_pair(a["id"], b["id"])["relationship_type"] == "possible_duplicate"
    assert ov.pending_alert_count() == 0
    assert len(ov.review_queue(actor("pgrc.admin"))) == 1          # still reviewable


def test_different_institutions_are_never_compared(actor, inst):
    file(actor, "pgrc.officer", PGRC_TEXT)
    file(actor, "rb.officer", RB_TEXT, aicte="654321")
    assert case_links.all_rows() == []


def test_disposed_complaints_are_not_candidates(actor, inst):
    a = file(actor, "pgrc.officer", PGRC_TEXT)
    cs.assign(actor("pgrc.admin"), a["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), a["id"], "Under Process")
    cs.change_status(actor("pgrc.officer"), a["id"], "Disposed", "closed")
    file(actor, "rb.officer", RB_TEXT)
    assert case_links.all_rows() == []


def test_classify_boundaries():
    assert ov.classify(LEXICAL_HIGH, "lexical", same_bureau=False) == "overlapping_proceeding"
    assert ov.classify(LEXICAL_HIGH, "lexical", same_bureau=True) == "possible_duplicate"
    assert ov.classify(LEXICAL_MEDIUM, "lexical", same_bureau=False) == "related"
    assert ov.classify(LEXICAL_MEDIUM - 0.01, "lexical", same_bureau=False) == "same_institution"
    assert ov.classify(0.79, "embedding", same_bureau=False) == "related"
    assert ov.classify(0.80, "embedding", same_bureau=False) == "overlapping_proceeding"


# ---- human review ----------------------------------------------------------

def test_confirm_requires_reason_and_lands_in_both_histories(actor, planted):
    a, b = planted
    link = case_links.get_pair(a["id"], b["id"])
    with pytest.raises(ValidationError):
        ov.decide(actor("rb.admin"), link["id"], "confirmed", "")
    ov.decide(actor("rb.admin"), link["id"], "confirmed", "Same fee demand; PGRC and RB to coordinate")
    link = case_links.get(link["id"])
    assert link["review_status"] == "confirmed" and link["reviewed_by"] == actor("rb.admin")["id"]
    for c in (a, b):
        last = case_actions.for_complaint(c["id"])[-1]
        assert last["action_type"] == "overlap_confirmed"
        assert "coordinate" in last["remarks"]
    assert any(e["action"] == "overlap.confirmed" for e in audit.list_recent())
    # the other bureau's admin is told of the decision
    assert any(n["type"] == "overlap_decision" for n in notifications.for_user(actor("pgrc.admin")["id"]))
    # confirmation opens read access both ways, nothing more
    d = views.case_detail(actor("rb.officer"), a["case_number"])
    assert d["full"] and not d["can_edit"]


def test_only_involved_bureau_admins_and_super_admin_may_decide(actor, planted):
    a, b = planted
    lid = case_links.get_pair(a["id"], b["id"])["id"]
    with pytest.raises(PermissionDenied):
        ov.decide(actor("rb.officer"), lid, "dismissed", "not mine to decide")
    with pytest.raises(NotFound):
        ov.decide(actor("ab.admin"), lid, "dismissed", "uninvolved bureau")
    with pytest.raises(NotFound):
        ov.review_detail(actor("ab.admin"), lid)
    ov.decide(actor("superadmin"), lid, "related_distinct", "Different fee heads")
    with pytest.raises(ValidationError):
        ov.decide(actor("rb.admin"), lid, "confirmed", "second decision")


def test_review_detail_hides_other_bureaus_text_until_confirmed(actor, planted):
    a, b = planted
    lid = case_links.get_pair(a["id"], b["id"])["id"]
    d = ov.review_detail(actor("rb.admin"), lid)
    sides = {s["case_number"]: s for s in (d["a"], d["b"])}
    assert sides[a["case_number"]]["restricted"] and sides[a["case_number"]]["description"] is None
    assert not sides[b["case_number"]]["restricted"]
    assert d["can_decide"] and d["different_bureaus"]
    sa = ov.review_detail(actor("superadmin"), lid)
    assert not sa["a"]["restricted"] and not sa["b"]["restricted"]
    ov.decide(actor("rb.admin"), lid, "confirmed", "Confirmed after review")
    assert not ov.review_detail(actor("rb.admin"), lid)["a"]["restricted"]


def test_dismissed_pair_is_not_reraised_unless_text_changes(actor, planted):
    a, b = planted
    lid = case_links.get_pair(a["id"], b["id"])["id"]
    ov.decide(actor("rb.admin"), lid, "dismissed", "Different matter, checked with PGRC")
    notes = len(notifications.for_user(actor("superadmin")["id"]))
    ov.detect(a["id"]); ov.detect(b["id"])                          # re-run: nothing changes
    assert case_links.get(lid)["review_status"] == "dismissed"
    assert len(notifications.for_user(actor("superadmin")["id"])) == notes
    assert ov.pending_alert_count() == 0
    # a description change re-evaluates it and re-raises the alert
    cs.update_text(actor("pgrc.admin"), a["id"], "Unauthorized examination fees again",
                   "Students are charged unauthorized examination fees and the demand has doubled.")
    again = case_links.get(lid)
    assert again["review_status"] == "pending" and again["relationship_type"] == "overlapping_proceeding"
    assert ov.pending_alert_count() == 1


def test_confirmed_link_is_not_reset_by_edits(actor, planted):
    a, b = planted
    lid = case_links.get_pair(a["id"], b["id"])["id"]
    ov.decide(actor("superadmin"), lid, "confirmed", "Confirmed overlap")
    cs.update_text(actor("pgrc.admin"), a["id"], "A new title for it", "A different description of the same fee.")
    assert case_links.get(lid)["review_status"] == "confirmed"


def test_alias_approval_triggers_detection(actor, inst):
    file(actor, "pgrc.officer", PGRC_TEXT)
    c = cs.create(actor("rb.officer"), institution_name="ABC Engg. College",
                  title=RB_TEXT[0], description=RB_TEXT[1], category="Unauthorized fees")
    assert case_links.all_rows() == []                              # unlinked: excluded from detection
    from db import institutions
    from services import institution_service as isv
    isv.approve_alias(actor("rb.admin"), institutions.list_aliases(status="suggested")[0]["id"])
    assert case_links.get_pair(1, c["id"])["relationship_type"] == "overlapping_proceeding"


def test_bureau_names_never_link_by_similarity_alone(actor, inst):
    c = cs.create(actor("rb.officer"), institution_name="abc engineering college",
                  title="Some complaint title", description="Some complaint description text.",
                  category="Harassment")
    assert c["institution_id"] is None


# ---- embeddings: optional, audited, falls back -----------------------------

class FakeEmbed:
    def __init__(self, fail=False, dim=1536):
        self.fail, self.dim = fail, dim

    def embed(self, texts, model, input_type, embedding_types):
        if self.fail:
            raise RuntimeError("api down")
        assert input_type == "search_document"                      # symmetric: same type both sides
        t = texts[0].lower()
        v = [0.0] * self.dim
        v[0] = 1.0 if "exam" in t else 0.0
        v[1] = 1.0 if "fee" in t or "charge" in t else 0.0
        v[2] = 1.0
        return type("R", (), {"embeddings": type("E", (), {"float": [v]})()})()


def test_no_key_means_lexical_mode(actor, planted, monkeypatch):
    assert embedding_service.available() is False
    link = case_links.all_rows()[0]
    assert link["evidence"]["scores"]["mode"] == "lexical"
    assert link["evidence"]["scores"]["semantic"] is None


def test_embedding_mode_stores_vectors_and_logs_calls(actor, inst, monkeypatch):
    monkeypatch.setattr(embedding_service, "_client_factory", lambda key: FakeEmbed())
    a = file(actor, "pgrc.officer", PGRC_TEXT)
    b = file(actor, "rb.officer", RB_TEXT)
    link = case_links.get_pair(a["id"], b["id"])
    sc = link["evidence"]["scores"]
    assert sc["mode"] == "embedding" and sc["semantic"] is not None and sc["lexical"] > 0
    from db import embeddings
    assert embeddings.get(a["id"])["dimension"] == 1536
    logged = [e for e in audit.list_recent() if e["action"] == "ai.embed"]
    assert logged and all(e["detail"]["model"] == "embed-v4.0" and e["detail"]["success"] for e in logged)
    assert all("latency_ms" in e["detail"] for e in logged)
    assert any(e["action"] == "ai.similarity" for e in audit.list_recent())


def test_embedding_failure_falls_back_to_lexical_and_is_logged(actor, inst, monkeypatch):
    monkeypatch.setattr(embedding_service, "_client_factory", lambda key: FakeEmbed(fail=True))
    a = file(actor, "pgrc.officer", PGRC_TEXT)
    b = file(actor, "rb.officer", RB_TEXT)
    link = case_links.get_pair(a["id"], b["id"])
    assert link["evidence"]["scores"]["mode"] == "lexical"
    assert link["relationship_type"] == "overlapping_proceeding"      # still works offline
    assert any(e["action"] == "ai.embed" and e["detail"]["success"] is False for e in audit.list_recent())


def test_embedding_dimension_mismatch_is_a_hard_failure(monkeypatch):
    monkeypatch.setattr(embedding_service, "_client_factory", lambda key: FakeEmbed(dim=1024))
    assert embedding_service.embed("anything at all") is None


# ---- measured quality guard -------------------------------------------------

def test_lexical_thresholds_meet_the_measured_floor():
    path = os.path.join(os.path.dirname(__file__), "data", "overlap_pairs.json")
    pairs = json.load(open(path, encoding="utf-8"))["pairs"]
    assert 40 <= len(pairs) <= 60
    tp = fp = fn = 0
    for p in pairs:
        pred = lexical.similarity(p["a"], p["b"])[0] >= LEXICAL_HIGH
        tp += pred and p["overlap"]
        fp += pred and not p["overlap"]
        fn += (not pred) and p["overlap"]
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    assert precision >= 0.85 and recall >= 0.75, (precision, recall)


def test_lexical_similarity_is_symmetric_and_explains_itself():
    s1, t1 = lexical.similarity(*PGRC_TEXT[:1], RB_TEXT[0])
    s2, t2 = lexical.similarity(RB_TEXT[0], PGRC_TEXT[0])
    assert s1 == s2 and set(t1) == set(t2) and "exam" in t1
    assert lexical.similarity("", "anything")[0] == 0.0


def test_different_categories_need_a_higher_bar_for_an_overlap_alert():
    s = LEXICAL_HIGH + 0.05                      # clears the normal bar, not the cross-category bar
    assert ov.classify(s, "lexical", same_bureau=False, same_category=True) == "overlapping_proceeding"
    assert ov.classify(s, "lexical", same_bureau=False, same_category=False) == "related"
    assert ov.classify(0.9, "lexical", same_bureau=False, same_category=False) == "overlapping_proceeding"
