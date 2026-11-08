"""Source checks for complete documents retain typed refs and real role/data links."""

import pytest

from backend.app.audit import audit
from backend.app.builder import Builder
from backend.app.document_review import (
    associated_data_names,
    explicit_document_issues,
    source_projection,
)
from backend.app.graph import validate_graph
from backend.app.semantic_review import review_source

SOURCE = "Менеджер проверяет заявку."


def builder(source=SOURCE, *, typed_process=False):
    result = Builder(source_text=source)
    result.methods["document"](id="D")
    result.methods["element"](
        id="P",
        tag="rootElement" if typed_process else "process",
        parent="D",
        **({"type": "tProcess"} if typed_process else {}),
    )
    return result


def element(builder, identifier, tag, parent="P", **kwargs):
    builder.methods["element"](id=identifier, tag=tag, parent=parent, **kwargs)


def assigned_lane(builder, task="T"):
    element(builder, "LS", "laneSet")
    element(builder, "L", "lane", "LS", name="Менеджер")
    element(builder, "NodeRef", "flowNodeRef", "L", text=task)


def assert_structural(ir):
    assert not [issue for issue in validate_graph(ir) if issue.severity == "error"]


@pytest.mark.parametrize("role_tag", ["resourceRole", "performer", "humanPerformer", "potentialOwner"])
def test_explicit_resource_reference_is_a_confirmed_role(role_tag):
    b = builder()
    element(b, "R", "resource", "D", name="Менеджер")
    element(b, "T", "userTask", name="Проверить заявку", source_quote=SOURCE)
    element(b, "Assignment", role_tag, "T")
    element(b, "ResourceRef", "resourceRef", "Assignment", text="R")
    ir = b.build()
    before = ir.model_dump_json()
    assert_structural(ir)
    assert not audit(ir)
    projected = source_projection(ir)
    lane = next(item for item in projected.lanes if item.id == projected.nodes[0].lane_id)
    assert lane.name == "Менеджер"
    assert ir.model_dump_json() == before


@pytest.mark.parametrize("reference", ["ext:R", "unknown:R"])
def test_external_or_unbound_resource_reference_never_confirms_local_role(reference):
    b = builder()
    b.methods["namespace"](prefix="ext", uri="urn:external")
    element(b, "R", "resource", "D", name="Менеджер")
    element(b, "T", "userTask", name="Проверить заявку", source_quote=SOURCE)
    element(b, "Assignment", "humanPerformer", "T")
    element(b, "ResourceRef", "resourceRef", "Assignment", text=reference)
    assert "UNASSIGNED_ROLE" in {issue.code for issue in audit(b.build())}


def test_foreign_participant_process_ref_cannot_assign_role_to_local_process():
    b = builder()
    b.methods["namespace"](prefix="ext", uri="urn:external")
    element(b, "C", "collaboration", "D")
    element(b, "Pool", "participant", "C", name="Менеджер", processRef="ext:P")
    element(b, "T", "task", name="Проверить заявку", source_quote=SOURCE)
    ir = b.build()
    assert_structural(ir)
    projected = source_projection(ir)
    assert not projected.participants
    assert not projected.nodes[0].lane_id
    assert "UNASSIGNED_ROLE" in {issue.code for issue in audit(ir)}


def test_target_namespace_process_reference_still_assigns_its_pool_role():
    b = builder()
    b.methods["namespace"](prefix="self", uri="urn:process-architect:document")
    element(b, "C", "collaboration", "D")
    element(b, "Pool", "participant", "C", name="Менеджер", processRef="self:P")
    element(b, "T", "task", name="Проверить заявку", source_quote=SOURCE)
    assert not audit(b.build())


def test_abstract_call_activity_is_audited_instead_of_disappearing():
    b = builder(typed_process=True)
    element(b, "GT", "globalUserTask", "D", name="Проверить заявку")
    element(b, "T", "flowElement", type="tCallActivity", name="Проверить заявку", calledElement="GT")
    ir = b.build()
    assert_structural(ir)
    projected = source_projection(ir)
    assert [item.id for item in projected.processes] == ["P"]
    assert [(item.id, item.process_id) for item in projected.nodes] == [("T", "P")]
    assert {issue.code for issue in audit(ir)} == {"UNASSIGNED_ROLE", "ACTION_WITHOUT_SOURCE"}


def test_abstract_task_and_gateway_keep_quote_role_and_branch_checks():
    b = builder(typed_process=True)
    assigned_lane(b)
    element(b, "T", "flowElement", type="tManualTask", name="Проверить заявку", source_quote=SOURCE)
    element(b, "G", "flowElement", type="tExclusiveGateway")
    element(b, "E1", "endEvent")
    element(b, "E2", "endEvent")
    element(b, "F1", "sequenceFlow", sourceRef="G", targetRef="E1")
    element(b, "F2", "sequenceFlow", sourceRef="G", targetRef="E2")
    assert {issue.code for issue in audit(b.build())} == {"XOR_BRANCH_UNLABELED"}


@pytest.mark.parametrize("metadata", ["documentation", "extensionElements"])
def test_bpmn_examples_inside_metadata_are_not_actual_source_activities(metadata):
    b = builder("После начального события менеджер проверяет заявку.")
    element(b, "Metadata", metadata)
    parent = "Metadata"
    if metadata == "extensionElements":
        b.methods["namespace"](prefix="vendor", uri="urn:vendor")
        element(b, "Payload", "vendor:payload", "Metadata")
        parent = "Payload"
    element(b, "Sample", "task", parent, name="Пример в документации")
    if metadata == "extensionElements":
        element(b, "SampleStart", "startEvent", parent)
    ir = b.build()
    assert_structural(ir)
    assert not source_projection(ir).nodes
    assert not audit(ir)
    assert [issue.code for issue in explicit_document_issues(ir)] == ["EXPLICIT_EVENT_OMITTED"]


def named_data_builder(*, linked_task="T", external=False, link=True):
    source = "Менеджер формирует документ «Договор»."
    b = builder(source)
    assigned_lane(b)
    element(b, "T", "task", name="Сформировать документ", source_quote=source)
    if linked_task != "T":
        element(b, linked_task, "task", name="Другое действие")
    element(b, "Data", "dataObject")
    element(b, "Doc", "dataObjectReference", name="Договор", dataObjectRef="Data")
    if link:
        b.methods["namespace"](prefix="ext", uri="urn:external")
        element(b, "IO", "ioSpecification", linked_task)
        element(b, "Output", "dataOutput", "IO")
        element(b, "Inputs", "inputSet", "IO")
        element(b, "Outputs", "outputSet", "IO")
        element(b, "OutputRef", "dataOutputRefs", "Outputs", text="Output")
        element(b, "Assoc", "dataOutputAssociation", linked_task)
        element(b, "From", "sourceRef", "Assoc", text="Output")
        element(b, "To", "targetRef", "Assoc", text="ext:Doc" if external else "Doc")
    return b


def test_visible_named_data_associated_with_quoted_task_satisfies_document_review():
    ir = named_data_builder().build()
    assert_structural(ir)
    assert associated_data_names(ir) == {"T": {"Договор"}}
    report = review_source(ir)
    assert report["issues"] == []
    assert report["status"] == "rules_passed"


@pytest.mark.parametrize("kwargs", [{"link": False}, {"linked_task": "Other"}, {"external": True}])
def test_unrelated_or_external_named_data_does_not_hide_an_omission(kwargs):
    report = review_source(named_data_builder(**kwargs).build())
    assert "SOURCE_NAMED_DATA_OMITTED" in {issue["code"] for issue in report["issues"]}


@pytest.mark.parametrize("kwargs, omitted", [
    ({}, False), ({"link": False}, True), ({"external": True}, True), ({"linked_task": "Other"}, True),
])
def test_separate_sentence_data_name_accepts_only_connection_to_a_source_cited_action(kwargs, omitted):
    b = named_data_builder(**kwargs)
    ir = b.build()
    # The name occurs in a separate sentence, outside the task's exact quotation.
    ir.source_text = "Менеджер формирует документ. Результат — документ «Договор»."
    for quote in ir.provenance:
        quote.exact_quote = "Менеджер формирует документ."
        quote.source_start = 0
        quote.source_end = len(quote.exact_quote)
    report = review_source(ir)
    assert ("SOURCE_NAMED_DATA_OMITTED" in {issue["code"] for issue in report["issues"]}) is omitted


@pytest.mark.parametrize(
    "source",
    [
        "Процесс без начального события.",
        "Начальное событие не нужно.",
        "endEvent не нужен.",
        "Не нужно начальное событие.",
        "Не использовать startEvent.",
        "Процесс имеет неявные входы и выходы.",
        "Участники обсуждают startEvent.",
    ],
)
def test_implicit_or_negated_events_are_not_made_mandatory(source):
    assert not explicit_document_issues(builder(source).build())


@pytest.mark.parametrize(
    "source, expected",
    [
        ("После начального события менеджер проверяет заявку.", "startEvent"),
        ("Добавьте endEvent.", "endEvent"),
        ("Процесс начинается с начального события.", "startEvent"),
    ],
)
def test_positive_explicit_event_request_reports_its_actual_omission(source, expected):
    b = builder(source)
    assert [issue.code for issue in explicit_document_issues(b.build())] == ["EXPLICIT_EVENT_OMITTED"]
    element(b, "Requested", "flowElement", type="t" + expected[0].upper() + expected[1:])
    assert not explicit_document_issues(b.build())
