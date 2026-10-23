"""Case lifecycle: state machine, history, permissions."""
import pytest

from db import bureaus, case_actions, complaints
from services import case_view_service, complaint_service as cs
from services.errors import PermissionDenied, ValidationError

TEXT = dict(title="Unauthorized examination fees", category="Unauthorized fees",
            description="Students are being charged unauthorized examination fees.")


def new_case(actor, inst, who="pgrc.officer", **kw):
    return cs.create(actor(who), aicte_id=inst["aicte_permanent_id"], **{**TEXT, **kw})


def test_two_bureaus_file_against_one_institution_and_profile_shows_both(actor, inst):
    a = new_case(actor, inst, "pgrc.officer")
    b = new_case(actor, inst, "rb.officer", title="Excess exam charges",
                 description="The institution is collecting excess examination charges.")
    assert a["bureau_id"] != b["bureau_id"]
    prof = case_view_service.institution_profile(actor("superadmin"), "123456")
    assert {r["case_number"] for r in prof["active"]} == {a["case_number"], b["case_number"]}
    assert len(prof["bureaus"]) == 2


def test_case_number_format_and_sla_deadline(actor, inst):
    c = new_case(actor, inst)
    assert c["case_number"] == "AICTE-CMP-00001"
    assert c["expected_disposal_at"] == pytest.approx(c["received_at"] + 30 * 86400)
    assert c["status"] == "Pending"


def test_create_requires_institution_or_name(actor, inst):
    with pytest.raises(ValidationError):
        cs.create(actor("pgrc.officer"), **TEXT)
    with pytest.raises(ValidationError):
        cs.create(actor("pgrc.officer"), aicte_id="000000", **TEXT)


def test_under_process_needs_an_officer(actor, inst):
    c = new_case(actor, inst)
    with pytest.raises(ValidationError):
        cs.change_status(actor("pgrc.admin"), c["id"], "Under Process")
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    assert cs.change_status(actor("pgrc.admin"), c["id"], "Under Process")["status"] == "Under Process"


def test_disposal_needs_remark_and_reopen_needs_reason(actor, inst):
    c = new_case(actor, inst)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), c["id"], "Under Process")
    with pytest.raises(ValidationError):
        cs.change_status(actor("pgrc.officer"), c["id"], "Disposed")
    cs.change_status(actor("pgrc.officer"), c["id"], "Disposed", "Refund ordered")
    with pytest.raises(ValidationError):
        cs.change_status(actor("pgrc.admin"), c["id"], "Under Process")
    assert cs.change_status(actor("pgrc.admin"), c["id"], "Under Process",
                            "New evidence")["status"] == "Under Process"


@pytest.mark.parametrize("bad", ["Disposed", "Pending", "Transferred", "Escalated"])
def test_invalid_transitions_are_refused(actor, inst, bad):
    c = new_case(actor, inst)         # Pending: only Under Process may be reached via change_status
    with pytest.raises(ValidationError):
        cs.change_status(actor("pgrc.admin"), c["id"], bad, "x")


def test_every_transition_writes_history_in_the_same_operation(actor, inst):
    c = new_case(actor, inst)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    cs.change_status(actor("pgrc.officer"), c["id"], "Under Process")
    cs.escalate(actor("pgrc.admin"), c["id"], "Needs senior view")
    cs.change_status(actor("pgrc.admin"), c["id"], "Under Process")
    cs.transfer(actor("pgrc.admin"), c["id"], bureaus.get_by_code("RB")["id"], "RB regulates this")
    cs.accept_transfer(actor("rb.admin"), c["id"])
    kinds = [a["action_type"] for a in case_actions.for_complaint(c["id"])]
    assert kinds == ["registered", "assigned", "status_changed", "escalated", "status_changed",
                     "transferred", "transfer_accepted"]
    final = complaints.get(c["id"])
    assert final["status"] == "Pending" and final["bureau_id"] == bureaus.get_by_code("RB")["id"]
    assert final["officer_id"] is None and final["last_action"] == "Transfer accepted"


def test_transfer_hands_ownership_to_receiving_bureau_and_notifies_it(actor, inst):
    from db import notifications
    c = new_case(actor, inst)
    cs.transfer(actor("pgrc.admin"), c["id"], bureaus.get_by_code("RB")["id"], "")
    assert complaints.get(c["id"])["status"] == "Transferred"
    assert notifications.for_user(actor("rb.admin")["id"])
    with pytest.raises(PermissionDenied):                       # old bureau cannot accept
        cs.accept_transfer(actor("pgrc.admin"), c["id"])


def test_history_is_append_only():
    assert not hasattr(case_actions, "update") and not hasattr(case_actions, "delete")
    with pytest.raises(PermissionError):
        case_actions.T.update(1, remarks="x")
    with pytest.raises(PermissionError):
        case_actions.T.delete(1)


def test_officer_cannot_assign_or_route_but_can_request(actor, inst):
    c = new_case(actor, inst)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    with pytest.raises(PermissionDenied):
        cs.assign(actor("pgrc.officer"), c["id"], actor("pgrc.officer2")["id"])
    with pytest.raises(PermissionDenied):
        cs.escalate(actor("pgrc.officer"), c["id"], "please")
    cs.request_action(actor("pgrc.officer"), c["id"], "escalation", "Complex matter")
    assert case_actions.for_complaint(c["id"])[-1]["action_type"] == "escalation_requested"
    assert complaints.get(c["id"])["status"] == "Pending"       # a request changes nothing


def test_officer_edits_only_assigned_cases(actor, inst):
    c = new_case(actor, inst)
    cs.assign(actor("pgrc.admin"), c["id"], actor("pgrc.officer")["id"])
    cs.add_remark(actor("pgrc.officer"), c["id"], "Called the registrar")
    with pytest.raises(PermissionDenied):
        cs.add_remark(actor("pgrc.officer2"), c["id"], "not mine")


def test_bureau_admin_cannot_manage_another_bureaus_case(actor, inst):
    c = new_case(actor, inst, "pgrc.officer")
    with pytest.raises(PermissionDenied):
        cs.assign(actor("rb.admin"), c["id"], actor("rb.officer")["id"])
    with pytest.raises(PermissionDenied):
        cs.transfer(actor("rb.admin"), c["id"], bureaus.get_by_code("AB")["id"], "")


def test_assignment_must_stay_inside_the_bureau(actor, inst):
    c = new_case(actor, inst)
    with pytest.raises(ValidationError):
        cs.assign(actor("pgrc.admin"), c["id"], actor("rb.officer")["id"])


def test_name_only_complaint_gets_suggestion_never_a_link(actor, inst):
    from db import institutions
    c = cs.create(actor("rb.officer"), institution_name="ABC Engg. College", **TEXT)
    assert c["institution_id"] is None
    sugg = institutions.list_aliases(status="suggested")
    assert [s["institution_id"] for s in sugg] == [inst["id"]]
    # approving is a human decision made by the owning bureau's admin
    from services import institution_service as isv
    with pytest.raises(PermissionDenied):
        isv.approve_alias(actor("pgrc.admin"), sugg[0]["id"])
    isv.approve_alias(actor("rb.admin"), sugg[0]["id"])
    assert complaints.get(c["id"])["institution_id"] == inst["id"]


def test_text_edit_bumps_version(actor, inst):
    c = new_case(actor, inst, "pgrc.admin")
    c2 = cs.update_text(actor("pgrc.admin"), c["id"], "Changed title here", "A different description entirely.")
    assert c2["text_version"] == 2
