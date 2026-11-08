"""Roles may be declared through valid BPMN resources or enclosing lanes."""

import pytest
from lxml import etree

from backend.app.document_validation import validate_document_structure
from backend.app.xml_validation import validate_xml


def document(body):
    return (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:foreign="urn:external" id="D" targetNamespace="urn:test">'
        + body
        + "</definitions>"
    )


def issues(body):
    return validate_document_structure(etree.fromstring(document(body).encode()))


def codes(body):
    return {issue.code for issue in issues(body)}


@pytest.mark.parametrize("role", ["resourceRole", "performer", "humanPerformer", "potentialOwner"])
def test_named_resource_assignment_is_a_role_without_lane(role):
    body = '<resource id="R" name="Operator"/><process id="P"><userTask id="T">' + (
        f'<{role} id="Assignment"><resourceRef>R</resourceRef></{role}>'
    ) + "</userTask></process>"
    assert "ROLE_UNSPECIFIED" not in codes(body)
    assert validate_xml(document(body))["checks"]["xsd"] == "passed"


@pytest.mark.parametrize(
    "declaration,reference,error",
    [
        ('<resource id="R" name=" "/>', "R", None),
        ("", "Missing", "UNKNOWN_REFERENCE"),
        ('<message id="R" name="Not a resource"/>', "R", "INVALID_REFERENCE_TYPE"),
        ('<resource id="R" name="Operator"/>', "foreign:R", "EXTERNAL_REFERENCE_UNRESOLVED"),
    ],
)
def test_invalid_or_unnamed_resource_does_not_hide_missing_role(declaration, reference, error):
    body = declaration + (
        '<process id="P"><userTask id="T"><performer id="Assignment">'
        f'<resourceRef>{reference}</resourceRef></performer></userTask></process>'
    )
    found = codes(body)
    assert "ROLE_UNSPECIFIED" in found
    if error:
        assert error in found


@pytest.mark.parametrize("kind", ["subProcess", "transaction", "adHocSubProcess"])
def test_nested_task_inherits_valid_enclosing_subprocess_lane(kind):
    body = (
        '<process id="P"><laneSet id="LS"><lane id="L" name="Operator">'
        '<flowNodeRef>Sub</flowNodeRef></lane></laneSet>'
        f'<{kind} id="Sub"><subProcess id="Nested"><userTask id="T"/>'
        f'</subProcess></{kind}></process>'
    )
    assert "ROLE_UNSPECIFIED" not in codes(body)
    assert validate_xml(document(body))["checks"]["xsd"] == "passed"


def test_cross_scope_lane_cannot_assign_nested_task_or_its_subprocess():
    body = (
        '<process id="P"><laneSet id="LS"><lane id="L" name="Operator">'
        '<flowNodeRef>T</flowNodeRef><flowNodeRef>Nested</flowNodeRef></lane></laneSet>'
        '<subProcess id="Outer"><subProcess id="Nested"><userTask id="T"/>'
        '</subProcess></subProcess></process>'
    )
    assert {"INVALID_LANE_REFERENCE", "ROLE_UNSPECIFIED"} <= codes(body)


def test_named_but_unassigned_resource_does_not_cover_tasks():
    body = '<resource id="R" name="Operator"/><process id="P"><userTask id="T"/></process>'
    assert "ROLE_UNSPECIFIED" in codes(body)


def test_resource_assignment_does_not_hide_multiple_lane_error():
    body = (
        '<resource id="R" name="Operator"/><process id="P"><laneSet id="LS">'
        '<lane id="L1"><flowNodeRef>T</flowNodeRef></lane>'
        '<lane id="L2"><flowNodeRef>T</flowNodeRef></lane></laneSet>'
        '<userTask id="T"><performer><resourceRef>R</resourceRef></performer>'
        '</userTask></process>'
    )
    assert "MULTIPLE_LANE_ASSIGNMENT" in codes(body)


def test_metadata_resource_assignment_does_not_cover_task():
    body = (
        '<resource id="R" name="Operator"/><process id="P"><userTask id="T">'
        '<extensionElements><performer><resourceRef>R</resourceRef></performer>'
        '</extensionElements></userTask></process>'
    )
    assert "ROLE_UNSPECIFIED" in codes(body)
