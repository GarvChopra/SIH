"""Who may see what. Enforced in the service layer, checked here without templates."""
import pytest

from db import case_links
from services import case_view_service as views, complaint_service as cs
from services.errors import NotFound

SECRET = "SECRET-BUREAU-DETAIL xyzzy"


@pytest.fixture()
def two_cases(actor, inst):
    a = cs.create(actor("pgrc.officer"), aicte_id="123456", title="PGRC case title",
                  category="Unauthorized fees", description=f"PGRC description {SECRET}")
    b = cs.create(actor("rb.officer"), aicte_id="123456", title="RB case title",
                  category="Unauthorized fees", description="RB description")
    cs.assign(actor("pgrc.admin"), a["id"], actor("pgrc.officer")["id"])
    cs.add_remark(actor("pgrc.officer"), a["id"], f"internal remark {SECRET}")
    return a, b


def test_other_bureau_sees_summary_but_not_content(actor, two_cases):
    a, _ = two_cases
    d = views.case_detail(actor("rb.officer"), a["case_number"])
    assert d["case"]["restricted"] and not d["full"]
    assert d["case"]["bureau_code"] == "PGRC" and d["case"]["status"] and d["case"]["category"]
    flat = repr(d)
    assert SECRET not in flat and "PGRC description" not in flat
    assert d["history"] == [] and d["documents"] == [] and d["case"]["officer_name"] is None
    assert d["can_edit"] is False


def test_list_masks_titles_of_other_bureaus(actor, two_cases):
    a, b = two_cases
    rows = {r["case_number"]: r for r in views.list_cases(actor("rb.officer"))}
    assert rows[a["case_number"]]["restricted"] and rows[a["case_number"]]["title"].startswith("Restricted")
    assert not rows[b["case_number"]]["restricted"] and rows[b["case_number"]]["title"] == "RB case title"


def test_search_text_does_not_leak_restricted_descriptions(actor, two_cases):
    assert views.list_cases(actor("rb.officer"), {"q": "xyzzy"}) == []
    assert len(views.list_cases(actor("pgrc.officer"), {"q": "xyzzy"})) == 1


def test_super_admin_sees_everything(actor, two_cases):
    a, _ = two_cases
    d = views.case_detail(actor("superadmin"), a["case_number"])
    assert d["full"] and SECRET in d["case"]["description"] and d["history"]


def test_confirmed_overlap_opens_content_read_only(actor, two_cases):
    a, b = two_cases
    case_links.upsert(a["id"], b["id"], relationship_type="overlapping_proceeding", score=0.9,
                      evidence={}, review_status="confirmed")
    d = views.case_detail(actor("rb.officer"), a["case_number"])
    assert d["full"] and SECRET in d["case"]["description"]
    assert d["can_edit"] is False and d["can_assign"] is False      # viewing never implies editing
    # the other direction is symmetric
    assert views.case_detail(actor("pgrc.officer"), b["case_number"])["full"]


def test_pending_or_dismissed_overlap_does_not_open_content(actor, two_cases):
    a, b = two_cases
    for st in ("pending", "dismissed", "related_distinct"):
        case_links.upsert(a["id"], b["id"], relationship_type="overlapping_proceeding", score=0.9,
                          evidence={}, review_status=st)
        assert views.case_detail(actor("rb.officer"), a["case_number"])["full"] is False


def test_third_bureau_gets_nothing_from_someone_elses_confirmation(actor, two_cases):
    a, b = two_cases
    case_links.upsert(a["id"], b["id"], relationship_type="overlapping_proceeding", score=0.9,
                      evidence={}, review_status="confirmed")
    assert views.case_detail(actor("ab.officer"), a["case_number"])["full"] is False


def test_regional_user_only_sees_their_region(actor, inst):
    south = cs.create(actor("pgrc.officer"), aicte_id="654321", title="South case title",
                      category="Harassment", description="Something happened at the southern college.")
    north = cs.create(actor("pgrc.officer"), aicte_id="123456", title="North case title",
                      category="Harassment", description="Something happened at the northern college.")
    nums = {r["case_number"] for r in views.list_cases(actor("ron.user"))}
    assert north["case_number"] in nums and south["case_number"] not in nums
    with pytest.raises(NotFound):
        views.case_detail(actor("ron.user"), south["case_number"])
    with pytest.raises(NotFound):
        views.institution_profile(actor("ron.user"), "654321")


def test_every_role_pair_never_leaks_through_present(actor, two_cases):
    """Pairwise: for each non-owner, non-super user the PGRC case body never appears."""
    a, _ = two_cases
    for u in ("rb.admin", "rb.officer", "ab.admin", "ab.officer", "ac.officer", "vc.admin",
              "ron.admin", "ros.admin"):
        try:
            d = views.case_detail(actor(u), a["case_number"])
        except NotFound:
            continue                       # regional users outside the region: not even visible
        assert SECRET not in repr(d), u
