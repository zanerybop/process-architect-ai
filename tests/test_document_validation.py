"""Official-XSD document coverage and negative rules beyond the legacy graph."""

from copy import deepcopy

import pytest
from lxml import etree

from backend.app.document_validation import (
    NS,
    effective_type,
    semantic_snapshot,
    semantic_tag,
    validate_document_structure,
)
from backend.app.xml_validation import validate_xml


def document(body):
    return (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:ext="urn:external" id="Defs" targetNamespace="urn:test">' + body + "</definitions>"
    )


def process(body, identifier="P"):
    return f'<process id="{identifier}">{body}</process>'


def nested():
    return process(
        '<startEvent id="S"/><subProcess id="Sub"><startEvent id="SS"/>'
        '<manualTask id="T"/><endEvent id="EE"/>'
        '<sequenceFlow id="SF1" sourceRef="SS" targetRef="T"/>'
        '<sequenceFlow id="SF2" sourceRef="T" targetRef="EE"/></subProcess>'
        '<endEvent id="E"/><sequenceFlow id="F1" sourceRef="S" targetRef="Sub"/>'
        '<sequenceFlow id="F2" sourceRef="Sub" targetRef="E"/>'
    )


def codes(report):
    return {item["code"] for item in report["issues"]}


def assert_standard(body):
    result = validate_xml(document(body))
    assert result["checks"]["xsd"] == "passed", result
    assert result["checks"]["graph"] == "passed", result
    assert result["checks"]["behavior"] == "inconclusive", result
    assert result["checks"]["layout"] == "not_run", result
    assert result["validation_profile"] == "standard_document"
    assert not [item for item in result["issues"] if item["severity"] == "error"]
    return result


def test_nested_subprocess_is_a_standard_document_without_fabricated_behavior_pass():
    assert_standard(nested())


@pytest.mark.parametrize("interrupting", ["true", "false"])
def test_boundary_timer_both_interrupting_modes(interrupting):
    assert_standard(
        process(
            '<userTask id="T"/><boundaryEvent id="Timer" attachedToRef="T" '
            f'cancelActivity="{interrupting}"><timerEventDefinition>'
            "<timeDuration>PT10M</timeDuration></timerEventDefinition></boundaryEvent>"
            '<endEvent id="E"/><sequenceFlow id="F" sourceRef="Timer" targetRef="E"/>'
        )
    )


@pytest.mark.parametrize("timer_field,value", [("timeDuration", "PT2H"), ("timeCycle", "R3/PT1H")])
def test_formal_expression_type_does_not_replace_the_timer_field(timer_field, value):
    body = process(
        '<userTask id="T"/><boundaryEvent id="B" attachedToRef="T"><timerEventDefinition>'
        f'<{timer_field} xsi:type="tFormalExpression">{value}</{timer_field}>'
        "</timerEventDefinition></boundaryEvent>"
    )
    assert_standard(body)
    root = etree.fromstring(document(body).encode())
    field = root.find(f".//bpmn:{timer_field}", NS)
    assert semantic_tag(field) == timer_field
    assert effective_type(field) == "bpmn:tFormalExpression"


def test_formal_expression_preserves_condition_field_and_parallel_rule():
    body = process(
        '<startEvent id="S"/><parallelGateway id="G"/><endEvent id="E"/>'
        '<sequenceFlow id="F1" sourceRef="S" targetRef="G"/>'
        '<sequenceFlow id="F2" sourceRef="G" targetRef="E">'
        '<conditionExpression xsi:type="tFormalExpression">approved</conditionExpression>'
        "</sequenceFlow>"
    )
    root = etree.fromstring(document(body).encode())
    field = root.find(".//bpmn:conditionExpression", NS)
    assert semantic_tag(field) == "conditionExpression"
    assert effective_type(field) == "bpmn:tFormalExpression"
    report = validate_xml(etree.tostring(root, encoding="unicode"))
    assert report["checks"]["xsd"] == "passed"
    assert report["checks"]["graph"] == "failed"


def test_resource_declarations_keep_overall_behavior_inconclusive():
    body = '<resource id="Role" name="Reviewer"/>' + process(
        '<startEvent id="S"/><task id="T"/><endEvent id="E"/>'
        '<sequenceFlow id="F1" sourceRef="S" targetRef="T"/>'
        '<sequenceFlow id="F2" sourceRef="T" targetRef="E"/>'
    )
    report = assert_standard(body)
    assert report["behavior"]["processes"][0]["status"] == "passed"
    assert "BEHAVIOR_ADVANCED_NOT_CHECKED" in codes(report)


def test_error_boundary_cannot_be_non_interrupting_even_when_xsd_accepts_it():
    body = process(
        '<task id="T"/><boundaryEvent id="B" attachedToRef="T" cancelActivity="false">'
        "<errorEventDefinition/></boundaryEvent>"
    )
    result = validate_xml(document(body))
    assert result["checks"]["xsd"] == "passed"
    assert result["checks"]["graph"] == "failed"
    assert "ERROR_MUST_INTERRUPT" in codes(result)


def test_boundary_host_must_be_activity_not_gateway():
    result = validate_xml(
        document(
            process(
                '<exclusiveGateway id="G"/><boundaryEvent id="B" attachedToRef="G">'
                "<errorEventDefinition/></boundaryEvent>"
            )
        )
    )
    assert result["checks"]["xsd"] == "passed"
    assert "INVALID_REFERENCE_TYPE" in codes(result)


def test_sequence_flow_cannot_cross_nested_scope():
    root = etree.fromstring(document(nested()).encode())
    root.find('.//bpmn:sequenceFlow[@id="F1"]', NS).set("targetRef", "T")
    report = validate_xml(etree.tostring(root, encoding="unicode"))
    assert report["checks"]["xsd"] == "passed"
    assert "FLOW_SCOPE_MISMATCH" in codes(report)


def test_missing_or_wrong_reference_cannot_hide_behind_a_new_element():
    for target, expected in [("Missing", "UNKNOWN_REFERENCE"), ("Msg", "INVALID_REFERENCE_TYPE")]:
        report = validate_xml(
            document(
                '<message id="Msg"/>'
                + process(
                    '<startEvent id="S"/><scriptTask id="T"/><endEvent id="E"/>'
                    f'<sequenceFlow id="F" sourceRef="S" targetRef="{target}"/>'
                )
            )
        )
        assert report["checks"]["graph"] == "failed"
        assert expected in codes(report)


def test_inert_import_and_external_qname_are_preserved_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A BPMN declaration must not cause a network request")

    import socket

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    body = '<import namespace="urn:external" location="https://example.invalid/model.bpmn" '
    body += 'importType="http://www.omg.org/spec/BPMN/20100524/MODEL"/>'
    body += process('<callActivity id="Call" calledElement="ext:RemoteProcess"/>')
    result = assert_standard(body)
    assert result["reference_resolution"] == "inconclusive"
    assert "EXTERNAL_REFERENCE_UNRESOLVED" in codes(result)


def test_prefix_cannot_resolve_to_local_id_when_namespace_is_external():
    result = assert_standard(process('<manualTask id="T"/><callActivity id="Call" calledElement="ext:T"/>'))
    assert result["reference_resolution"] == "inconclusive"
    assert "EXTERNAL_REFERENCE_UNRESOLVED" in codes(result)


def test_unbound_prefix_is_error():
    result = validate_xml(document(process('<callActivity id="Call" calledElement="missing:P"/>')))
    assert result["checks"]["graph"] == "failed"
    assert "UNBOUND_REFERENCE_PREFIX" in codes(result)


def test_call_activity_references_callable_type():
    report = validate_xml(
        document('<message id="M"/>' + process('<callActivity id="Call" calledElement="M"/>'))
    )
    assert "INVALID_REFERENCE_TYPE" in codes(report)


def test_global_task_and_call_activity():
    assert_standard(
        '<globalManualTask id="Global"/>' + process('<callActivity id="Call" calledElement="Global"/>')
    )


def test_event_subprocess_is_not_a_normal_sequence_flow_target():
    report = validate_xml(
        document(
            process(
                '<startEvent id="S"/><subProcess id="Handler" triggeredByEvent="true">'
                '<startEvent id="ES"><errorEventDefinition/></startEvent><endEvent id="EE"/>'
                '<sequenceFlow id="EF" sourceRef="ES" targetRef="EE"/></subProcess>'
                '<sequenceFlow id="F" sourceRef="S" targetRef="Handler"/>'
            )
        )
    )
    assert report["checks"]["xsd"] == "passed"
    assert "SPECIAL_ACTIVITY_SEQUENCE_FLOW" in codes(report)


def test_cancel_events_require_transaction():
    report = validate_xml(
        document(
            process(
                '<task id="T"/><boundaryEvent id="B" attachedToRef="T">'
                "<cancelEventDefinition/></boundaryEvent>"
            )
        )
    )
    assert "CANCEL_REQUIRES_TRANSACTION" in codes(report)
    assert_standard(
        process(
            '<transaction id="Transaction"><startEvent id="S"/><endEvent id="E">'
            '<cancelEventDefinition/></endEvent><sequenceFlow id="F" sourceRef="S" targetRef="E"/>'
            '</transaction><boundaryEvent id="B" attachedToRef="Transaction">'
            "<cancelEventDefinition/></boundaryEvent>"
        )
    )


def compensation(handler_attribute="true"):
    return process(
        '<task id="Book"/><boundaryEvent id="Comp" attachedToRef="Book">'
        "<compensateEventDefinition/></boundaryEvent>"
        f'<task id="Undo" isForCompensation="{handler_attribute}"/>'
        '<association id="A" sourceRef="Comp" targetRef="Undo" associationDirection="One"/>'
    )


def test_compensation_handler_is_a_special_association_not_a_backward_flow():
    assert_standard(compensation())
    report = validate_xml(document(compensation("false")))
    assert report["checks"]["xsd"] == "passed"
    assert "COMPENSATION_HANDLER_INVALID" in codes(report)


def test_compensation_handler_cannot_enter_normal_sequence_flow():
    body = compensation().replace(
        "</process>", '<endEvent id="E"/><sequenceFlow id="F" sourceRef="Undo" targetRef="E"/></process>'
    )
    report = validate_xml(document(body))
    assert "SPECIAL_ACTIVITY_SEQUENCE_FLOW" in codes(report)


def test_multi_instance_data_and_script_remain_inert():
    assert_standard(
        '<itemDefinition id="Item"/>'
        + process(
            '<scriptTask id="Script" scriptFormat="python"><ioSpecification>'
            '<dataInput id="Input" itemSubjectRef="Item"/><inputSet id="IS">'
            '<dataInputRefs>Input</dataInputRefs></inputSet><outputSet id="OS"/></ioSpecification>'
            '<multiInstanceLoopCharacteristics isSequential="true">'
            "<loopCardinality>3</loopCardinality></multiInstanceLoopCharacteristics>"
            '<script>raise RuntimeError("never execute business script")</script></scriptTask>'
        )
    )


def test_data_reference_type_is_checked():
    report = validate_xml(
        document(
            process('<dataObject id="D"/><manualTask id="T"/><dataObjectReference id="R" dataObjectRef="T"/>')
        )
    )
    assert "INVALID_REFERENCE_TYPE" in codes(report)


def test_conversations_and_black_box_participants_are_standard():
    assert_standard(
        '<collaboration id="C"><participant id="Buyer"/><participant id="Seller"/>'
        '<messageFlow id="M" sourceRef="Buyer" targetRef="Seller"/>'
        '<conversation id="Talk"><participantRef>Buyer</participantRef>'
        "<participantRef>Seller</participantRef><messageFlowRef>M</messageFlowRef></conversation>"
        '<conversationLink id="L" sourceRef="Buyer" targetRef="Talk"/></collaboration>'
    )


def choreography(initiator="Buyer"):
    return (
        '<choreography id="C"><participant id="Buyer"/><participant id="Seller"/>'
        '<messageFlow id="M" sourceRef="Buyer" targetRef="Seller"/>'
        f'<choreographyTask id="T" initiatingParticipantRef="{initiator}">'
        "<participantRef>Buyer</participantRef><participantRef>Seller</participantRef>"
        "<messageFlowRef>M</messageFlowRef></choreographyTask></choreography>"
    )


def test_choreography_with_two_participants():
    assert_standard(choreography())


def test_choreography_initiator_must_participate():
    body = choreography("Other").replace("<messageFlow", '<participant id="Other"/><messageFlow', 1)
    report = validate_xml(document(body))
    assert report["checks"]["xsd"] == "passed"
    assert "CHOREOGRAPHY_INITIATOR" in codes(report)


def test_root_declarations_resources_interfaces_and_correlations():
    assert_standard(
        '<itemDefinition id="Item"/><message id="Msg" itemRef="Item"/>'
        '<error id="Err" errorCode="E" structureRef="Item"/>'
        '<resource id="R" name="Resource"><resourceParameter id="Param" name="value" type="Item"/></resource>'
        '<interface id="I" name="Interface"><operation id="Op" name="Do">'
        "<inMessageRef>Msg</inMessageRef><errorRef>Err</errorRef></operation></interface>"
        '<correlationProperty id="CP" name="Correlation" type="Item">'
        '<correlationPropertyRetrievalExpression messageRef="Msg">'
        "<messagePath>correlationId</messagePath></correlationPropertyRetrievalExpression>"
        "</correlationProperty>"
    )


def test_structure_api_and_semantic_snapshot_do_not_mutate_document():
    root = etree.fromstring(document(nested()).encode())
    before = etree.tostring(root)
    assert not [issue for issue in validate_document_structure(root) if issue.severity == "error"]
    initial = semantic_snapshot(root)
    changed = deepcopy(root)
    changed.find(".//bpmn:manualTask", NS).set("name", "Renamed")
    assert semantic_snapshot(changed) != initial
    assert etree.tostring(root) == before


def test_standard_xsd_is_still_authoritative_for_containment():
    report = validate_xml(document(process('<manualTask id="T"><signal id="WrongChild"/></manualTask>')))
    assert report["checks"]["xsd"] == "failed"


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE definitions [<!ENTITY x "external">]>',
        '<!DOCTYPE definitions SYSTEM "https://example.invalid/model.dtd">',
    ],
)
def test_extended_documents_keep_dtd_entity_prohibition(declaration):
    report = validate_xml(declaration + document(nested()))
    assert report["checks"]["xsd"] == "failed"
    assert "XML_DTD_FORBIDDEN" in codes(report)


def test_polymorphic_flow_elements_keep_their_effective_types():
    body = (
        '<rootElement xsi:type="tProcess" id="P"><flowElement xsi:type="tUserTask" id="T"/>'
        '<boundaryEvent id="B" attachedToRef="T"><errorEventDefinition/></boundaryEvent>'
        "</rootElement>"
    )
    assert_standard(body)


def test_global_event_definition_reference_applies_boundary_rules():
    body = '<errorEventDefinition id="ED"/>' + process(
        '<task id="T"/><boundaryEvent id="B" attachedToRef="T" cancelActivity="false">'
        "<eventDefinitionRef>ED</eventDefinitionRef></boundaryEvent>"
    )
    report = validate_xml(document(body))
    assert report["checks"]["xsd"] == "passed"
    assert "ERROR_MUST_INTERRUPT" in codes(report)


def test_standard_document_has_no_legacy_counts_or_mandatory_explicit_events():
    body = "".join(process('<task id="T' + str(index) + '"/>', "P" + str(index)) for index in range(3))
    report = validate_xml(document(body))
    assert report["checks"]["xsd"] == report["checks"]["graph"] == "passed"
    assert report["checks"]["behavior"] == "inconclusive"
    assert not [item for item in report["issues"] if item["severity"] == "error"]
    many = process("".join(f'<task id="T{index}"/>' for index in range(100)))
    assert validate_xml(document(many))["checks"]["graph"] == "passed"


def test_additional_standard_metadata_cannot_hide_a_known_deadlock():
    body = '<message id="M"/>' + process(
        '<startEvent id="S"/><exclusiveGateway id="X"/><task id="A"/><task id="B"/>'
        '<parallelGateway id="J"/><endEvent id="E"/>'
        '<sequenceFlow id="F1" sourceRef="S" targetRef="X"/>'
        '<sequenceFlow id="F2" sourceRef="X" targetRef="A" name="Yes"/>'
        '<sequenceFlow id="F3" sourceRef="X" targetRef="B" name="No"/>'
        '<sequenceFlow id="F4" sourceRef="A" targetRef="J"/>'
        '<sequenceFlow id="F5" sourceRef="B" targetRef="J"/>'
        '<sequenceFlow id="F6" sourceRef="J" targetRef="E"/>'
    )
    report = validate_xml(document(body))
    assert report["checks"]["graph"] == report["checks"]["xsd"] == "passed"
    assert report["checks"]["behavior"] == "failed"
    assert "BEHAVIOR_DEADLOCK" in codes(report)
    assert any(item["witness_path"] for item in report["issues"] if item["code"] == "BEHAVIOR_DEADLOCK")


@pytest.mark.parametrize(
    "body",
    [
        nested(),
        compensation(),
        choreography(),
        '<collaboration id="C"><participant id="Buyer"/><participant id="Seller"/>'
        '<messageFlow id="M" sourceRef="Buyer" targetRef="Seller"/>'
        '<conversation id="Talk"><participantRef>Buyer</participantRef>'
        "<participantRef>Seller</participantRef>"
        "<messageFlowRef>M</messageFlowRef></conversation>"
        '<conversationLink id="L" sourceRef="Buyer" targetRef="Talk"/></collaboration>',
    ],
)
def test_layout_for_standard_families_roundtrips_through_all_backend_gates(body):
    from backend.app.document_serialization import arrange_document

    root = arrange_document(etree.fromstring(document(body).encode()))
    report = validate_xml(etree.tostring(root, encoding="unicode"))
    assert report["checks"]["xsd"] == report["checks"]["graph"] == report["checks"]["layout"] == "passed", (
        report
    )


def test_choreography_message_definition_does_not_require_standalone_message_edge():
    from backend.app.document_serialization import arrange_document

    root = arrange_document(etree.fromstring(document(choreography()).encode()))
    for edge in root.findall(".//bpmndi:BPMNEdge", NS):
        if edge.get("bpmnElement") == "M":
            edge.getparent().remove(edge)
    report = validate_xml(etree.tostring(root, encoding="unicode"))
    assert report["checks"]["layout"] == "passed", report


def test_missing_nested_node_di_still_fails_while_multiple_diagrams_are_valid():
    from backend.app.document_serialization import arrange_document

    body = nested() + process('<manualTask id="Other"/>', "P2")
    root = arrange_document(etree.fromstring(document(body).encode()))
    assert len(root.findall("bpmndi:BPMNDiagram", NS)) == 2
    valid = validate_xml(etree.tostring(root, encoding="unicode"))
    assert valid["checks"]["layout"] == "passed", valid
    shape = next(item for item in root.findall(".//bpmndi:BPMNShape", NS) if item.get("bpmnElement") == "T")
    shape.getparent().remove(shape)
    invalid = validate_xml(etree.tostring(root, encoding="unicode"))
    assert invalid["checks"]["layout"] == "failed"
    assert "MISSING_DI" in codes(invalid)


def test_collapsed_subprocess_internal_di_is_not_required_on_the_parent_plane():
    from backend.app.document_serialization import arrange_document

    root = arrange_document(etree.fromstring(document(nested()).encode()))
    sub = next(item for item in root.findall(".//bpmndi:BPMNShape", NS) if item.get("bpmnElement") == "Sub")
    sub.set("isExpanded", "false")
    for item in list(sub.getparent()):
        if item.get("bpmnElement") in {"SS", "T", "EE", "SF1", "SF2"}:
            item.getparent().remove(item)
    report = validate_xml(etree.tostring(root, encoding="unicode"))
    assert report["checks"]["layout"] == "passed", report


def test_disconnected_explicit_start_is_invalid_but_implicit_start_remains_valid():
    broken = document(process('<startEvent id="S"/><task id="T"/>'))
    report = validate_xml(broken)
    assert report["checks"]["xsd"] == "passed"
    assert report["checks"]["graph"] == "failed"
    issue = next(item for item in report["issues"] if item["code"] == "START_WITHOUT_OUTGOING")
    assert issue["element_ids"] == ["S"]
    assert validate_xml(document(process('<task id="T"/>')))["checks"]["graph"] == "passed"


def test_start_example_inside_documentation_is_not_control_flow_or_a_di_requirement():
    body = process(
        '<documentation id="Doc"><startEvent id="Sample"/></documentation>'
        '<startEvent id="S"/><task id="T"/><endEvent id="E"/>'
        '<sequenceFlow id="F1" sourceRef="S" targetRef="T"/>'
        '<sequenceFlow id="F2" sourceRef="T" targetRef="E"/>'
    )
    report = validate_xml(document(body))
    assert report["checks"]["xsd"] == report["checks"]["graph"] == "passed"
    assert report["behavior"]["processes"][0]["status"] == "passed"
    assert "START_WITHOUT_OUTGOING" not in codes(report)


def test_metadata_node_ids_cannot_be_targets_of_model_flow():
    body = process(
        '<documentation id="Doc"><task id="ExampleTask"/></documentation>'
        '<startEvent id="S"/><sequenceFlow id="F" sourceRef="S" targetRef="ExampleTask"/>'
    )
    report = validate_xml(document(body))
    assert report["checks"]["xsd"] == "passed"
    assert report["checks"]["graph"] == "failed"
    issue = next(item for item in report["issues"] if item["code"] == "UNKNOWN_REFERENCE")
    assert issue["element_ids"] == ["F"]
    assert "targetRef='ExampleTask'" in issue["message"]


def test_idless_reference_error_names_its_field_value_and_parent():
    body = process(
        '<task id="T"><humanPerformer id="Role">'
        "<resourceRef>MissingResource</resourceRef></humanPerformer></task>"
    )
    report = validate_xml(document(body))
    issue = next(item for item in report["issues"] if item["code"] == "UNKNOWN_REFERENCE")
    assert "resourceRef='MissingResource'" in issue["message"]
    assert issue["element_ids"] == ["Role"]
