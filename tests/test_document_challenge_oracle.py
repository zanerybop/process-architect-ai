"""Adversarial unit checks for challenge scoring, not model success claims."""

import json
from pathlib import Path

import pytest

from scripts.document_challenge_oracle import ChallengeOracle, evaluate_properties, tag


def xml(body):
    return (
        (
            '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:b="ht'
            'tp://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:local="urn:test" xmlns:fore'
            'ign="urn:foreign" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" targ'
            'etNamespace="urn:test">'
        )
        + body
        + "</definitions>"
    )


def result(body, prop):
    return ChallengeOracle(xml(body)).check({"id": "property", **prop})["passed"]


def test_foreign_tags_and_embedded_bpmn_metadata_do_not_count():
    prop = {"kind": "count", "select": {"tags": ["subProcess"]}}
    assert result("<process><subProcess/></process>", prop)
    for body in [
        "<foreign:subProcess/>",
        "<extensionElements><subProcess/></extensionElements>",
        "<process><extensionElements><subProcess/></extensionElements></process>",
        "<process><documentation><subProcess/></documentation></process>",
        "<foreign:payload><subProcess/></foreign:payload>",
    ]:
        assert not result(body, prop)


def test_xsi_type_resolves_namespace_and_declared_derivation():
    prop = {"kind": "count", "select": {"tags": ["subProcess"]}}
    assert result('<process><flowElement xsi:type="b:tSubProcess"/></process>', prop)
    assert not result('<process><flowElement xsi:type="foreign:tSubProcess"/></process>', prop)
    assert not result('<process><task xsi:type="b:tSubProcess"/></process>', prop)
    root = ChallengeOracle(
        xml(
            (
                '<process><sequenceFlow><conditionExpression xsi:type="b:tFormalExpression">o'
                "k</conditionExpression></sequenceFlow></process>"
            )
        )
    )
    assert tag(root.select({"tags": ["conditionExpression"]})[0]) == "conditionExpression"


def test_external_qnames_never_resolve_by_local_suffix():
    prop = {
        "kind": "reference",
        "from": {"tags": ["callActivity"]},
        "attribute": "calledElement",
        "to": {"tags": ["process"], "pattern": "Reusable"},
    }
    body = (
        '<process id="p"><callActivity calledElement="{}"/></process><process id="reused" name="Reusable"/>'
    )
    assert result(body.format("reused"), prop)
    assert result(body.format("local:reused"), prop)
    assert not result(body.format("foreign:reused"), prop)


def test_reference_count_and_same_target_reject_duplicated_definitions():
    prop = {
        "kind": "reference",
        "from": {"tags": ["callActivity"]},
        "attribute": "calledElement",
        "to": {"tags": ["process"], "pattern": "Reusable"},
        "min": 2,
        "same_target": True,
    }
    body = (
        '<process><callActivity calledElement="p1"/><callActivity calledElement="{}"/'
        '></process><process id="p1" name="Reusable"/><process id="p2" name="Reusable'
        '"/>'
    )
    assert result(body.format("p1"), prop)
    assert not result(body.format("p2"), prop)


def test_metadata_id_does_not_shadow_real_resource():
    prop = {"kind": "role", "action": {"tags": ["userTask"]}, "role": "Reviewer", "role_kind": "resource"}
    body = (
        '<resource id="r" name="Reviewer"/><process><userTask><humanPerformer><resour'
        'ceRef>r</resourceRef></humanPerformer><extensionElements><resource id="r" na'
        'me="Wrong"/></extensionElements></userTask></process>'
    )
    assert result(body, prop)


def test_roles_inherit_container_lane_but_inner_lane_overrides():
    prop = {
        "kind": "role",
        "action": {"tags": ["userTask"], "pattern": "Check"},
        "role": "Engineer",
        "role_kind": "lane",
    }
    body = (
        '<process><laneSet><lane name="Engineer"><flowNodeRef>sub</flowNodeRef></lane'
        '></laneSet><subProcess id="sub">{}<userTask id="t" name="Check"/></subProces'
        "s></process>"
    )
    assert result(body.format(""), prop)
    assert not result(
        body.format('<laneSet><lane name="Visitor"><flowNodeRef>t</flowNodeRef></lane></laneSet>'), prop
    )


def test_resource_roles_do_not_leak_from_descendant_or_override():
    prop = {
        "kind": "role",
        "action": {"tags": ["subProcess"], "pattern": "Host"},
        "role": "Reviewer",
        "role_kind": "resource",
    }
    body = (
        '<resource id="r" name="Reviewer"/><process><subProcess name="Host"><userTask'
        "><humanPerformer><resourceRef>r</resourceRef></humanPerformer></userTask></s"
        "ubProcess></process>"
    )
    assert not result(body, prop)
    inherited = (
        '<resource id="r" name="Reviewer"/><resource id="v" name="Visitor"/><process>'
        '<performer><resourceRef>r</resourceRef></performer><userTask name="Work">{}<'
        "/userTask></process>"
    )
    prop["action"] = {"tags": ["userTask"]}
    assert result(inherited.format(""), prop)
    assert not result(inherited.format("<performer><resourceRef>v</resourceRef></performer>"), prop)


def test_io_association_requires_correct_direction_and_owned_port():
    prop = {
        "kind": "io",
        "action": {"tags": ["userTask"], "pattern": "Read"},
        "direction": "input",
        "data": "Report",
    }
    body = (
        '<process><dataObject id="d" name="Report"/><userTask id="t" name="Read"><ioS'
        'pecification><dataInput id="i"/><dataOutput id="o"/></ioSpecification><dataI'
        "nputAssociation><sourceRef>{}</sourceRef><targetRef>{}</targetRef></dataInpu"
        'tAssociation></userTask><userTask><ioSpecification><dataInput id="other"/></'
        "ioSpecification></userTask></process>"
    )
    assert result(body.format("d", "i"), prop)
    for source, target in [("i", "d"), ("d", "o"), ("d", "other"), ("foreign:d", "i")]:
        assert not result(body.format(source, target), prop)


def test_output_resolves_data_store_reference_to_named_store():
    prop = {"kind": "io", "action": {"tags": ["serviceTask"]}, "direction": "output", "data": "Archive"}
    body = (
        '<dataStore id="store" name="Archive"/><process><dataStoreReference id="ref" '
        'dataStoreRef="store"/><serviceTask><ioSpecification><dataOutput id="out"/></'
        "ioSpecification><dataOutputAssociation><sourceRef>out</sourceRef><targetRef>"
        "ref</targetRef></dataOutputAssociation></serviceTask></process>"
    )
    assert result(body, prop)
    assert not result(body.replace('dataStoreRef="store"', 'dataStoreRef="foreign:store"'), prop)


def test_attributes_respect_xsd_defaults_and_boolean_lexical_forms():
    prop = {
        "kind": "count",
        "select": {"tags": ["multiInstanceLoopCharacteristics"], "attrs": {"isSequential": "false"}},
    }
    assert result("<process><subProcess><multiInstanceLoopCharacteristics/></subProcess></process>", prop)
    assert result(
        '<process><subProcess><multiInstanceLoopCharacteristics isSequential="0"/></subProcess></process>',
        prop,
    )
    assert not result(
        '<process><subProcess><multiInstanceLoopCharacteristics isSequential="1"/></subProcess></process>',
        prop,
    )


def test_sequence_scope_includes_owner_not_just_endpoints():
    prop = {"kind": "sequence_scope"}
    body = '<process><subProcess><task id="a"/><task id="b"/>{}</subProcess>{}</process>'
    flow = '<sequenceFlow sourceRef="a" targetRef="b"/>'
    assert result(body.format(flow, ""), prop)
    assert not result(body.format("", flow), prop)


def test_order_rejects_bypass_within_scope_and_ignores_other_process_starts():
    prop = {
        "kind": "order",
        "before": {"tags": ["task"], "pattern": "Check"},
        "after": {"tags": ["task"], "pattern": "Publish"},
    }
    body = (
        '<process><startEvent id="s"/><task id="a" name="Check"/><task id="b" name="P'
        'ublish"/><sequenceFlow sourceRef="s" targetRef="a"/><sequenceFlow sourceRef='
        '"a" targetRef="b"/>{}</process><process><startEvent/></process>'
    )
    assert result(body.format(""), prop)
    assert not result(body.format('<sequenceFlow sourceRef="s" targetRef="b"/>'), prop)


def test_branch_requires_real_condition_and_distinct_outcome():
    prop = {
        "kind": "branch",
        "condition": "yes",
        "to": {"tags": ["task"], "pattern": "Accept"},
        "not_to": {"tags": ["task"], "pattern": "Reject"},
    }
    body = (
        '<process><exclusiveGateway id="g"/><task id="a" name="Accept"/><task id="r" '
        'name="Reject"/><sequenceFlow sourceRef="g" targetRef="a">{}</sequenceFlow>{}'
        "</process>"
    )
    assert result(body.format("<conditionExpression>yes</conditionExpression>", ""), prop)
    assert not result(body.format("<documentation>yes</documentation>", ""), prop)
    assert not result(
        body.format(
            "<conditionExpression>yes</conditionExpression>", '<sequenceFlow sourceRef="a" targetRef="r"/>'
        ),
        prop,
    )


def test_error_boundary_matches_actual_raised_reference_and_scope():
    prop = {
        "kind": "boundary",
        "host": {"tags": ["subProcess"]},
        "definition": {"tags": ["errorEventDefinition"]},
        "interrupting": True,
        "handler": {"tags": ["task"], "pattern": "Recover"},
        "raised_inside": {
            "select": {"tags": ["errorEventDefinition"], "parent": {"tags": ["endEvent"]}},
            "reference": "errorRef",
        },
    }
    body = (
        '<error id="e1"/><error id="e2"/><process><subProcess id="sub"><endEvent><err'
        'orEventDefinition errorRef="{}"/></endEvent></subProcess><boundaryEvent id="'
        'catch" attachedToRef="sub"><errorEventDefinition errorRef="e1"/></boundaryEv'
        'ent><task id="handler" name="Recover"/><sequenceFlow sourceRef="catch" targe'
        'tRef="handler"/></process>'
    )
    assert result(body.format("e1"), prop)
    assert not result(body.format("e2"), prop)
    assert not result(body.format("foreign:e1"), prop)


def test_event_subprocess_rejects_regular_subprocess_and_interrupting_start():
    prop = {
        "kind": "event_subprocess",
        "in": {"tags": ["subProcess"], "pattern": "Host"},
        "interrupting": False,
        "definition": {"tags": ["messageEventDefinition"]},
        "action": {"tags": ["task"], "pattern": "Record"},
    }
    body = (
        '<process><subProcess name="Host"><subProcess id="eventsub" triggeredByEvent='
        '"true"><startEvent id="s" isInterrupting="false"><messageEventDefinition/></'
        'startEvent><task id="a" name="Record"/><sequenceFlow sourceRef="s" targetRef'
        '="a"/></subProcess>{}</subProcess></process>'
    )
    assert result(body.format(""), prop)
    assert not result(body.format("").replace('triggeredByEvent="true"', 'triggeredByEvent="false"'), prop)
    assert not result(body.format("").replace('isInterrupting="false"', 'isInterrupting="true"'), prop)
    assert not result(body.format('<sequenceFlow sourceRef="eventsub" targetRef="a"/>'), prop)


def test_compensation_handler_needs_attachment_direction_marker_and_no_ordinary_flow():
    prop = {
        "kind": "compensation",
        "host": {"tags": ["task"], "pattern": "Reserve"},
        "handler": {"tags": ["task"], "pattern": "Release"},
    }
    body = (
        '<process><task id="host" name="Reserve"/><boundaryEvent id="event" attachedT'
        'oRef="host"><compensateEventDefinition/></boundaryEvent><task id="handler" n'
        'ame="Release" isForCompensation="true"/><association sourceRef="event" targe'
        'tRef="handler" associationDirection="One"/>{}</process>'
    )
    assert result(body.format(""), prop)
    assert not result(body.format('<sequenceFlow sourceRef="host" targetRef="handler"/>'), prop)
    assert not result(body.format("").replace('isForCompensation="true"', 'isForCompensation="false"'), prop)
    assert not result(
        body.format("").replace('associationDirection="One"', 'associationDirection="None"'), prop
    )


def test_mapping_checks_both_inner_and_outer_participants():
    prop = {
        "kind": "mapping",
        "owner": {"tags": ["callConversation"]},
        "tag": "participantAssociation",
        "first_child": "innerParticipantRef",
        "second_child": "outerParticipantRef",
        "first": {"tags": ["participant"], "pattern": "Template", "parent": {"tags": ["globalConversation"]}},
        "second": {"tags": ["participant"], "pattern": "Actual", "parent": {"tags": ["collaboration"]}},
    }
    body = (
        '<globalConversation><participant id="inner" name="Template"/></globalConvers'
        'ation><collaboration><participant id="outer" name="Actual"/><callConversatio'
        "n><participantAssociation><innerParticipantRef>{}</innerParticipantRef><oute"
        "rParticipantRef>{}</outerParticipantRef></participantAssociation></callConve"
        "rsation></collaboration>"
    )
    assert result(body.format("inner", "outer"), prop)
    assert not result(body.format("outer", "inner"), prop)


def test_choreography_checks_receiver_as_well_as_initiator_and_message():
    prop = {
        "kind": "choreography",
        "task": {"tags": ["choreographyTask"], "attrs": {"loopType": "Standard"}},
        "initiator": {"tags": ["participant"], "pattern": "Sender"},
        "receiver": {"tags": ["participant"], "pattern": "Receiver"},
        "message": {"tags": ["message"], "pattern": "Packet"},
    }
    body = (
        '<message id="m" name="Packet"/><choreography><participant id="a" name="Sende'
        'r"/><participant id="b" name="Receiver"/><messageFlow id="f" sourceRef="a" t'
        'argetRef="b" messageRef="m"/><choreographyTask initiatingParticipantRef="a" '
        'loopType="Standard"><participantRef>a</participantRef><participantRef>b</par'
        "ticipantRef><messageFlowRef>f</messageFlowRef></choreographyTask></choreogra"
        "phy>"
    )
    assert result(body, prop)
    assert not result(body.replace('targetRef="b"', 'targetRef="a"'), prop)
    assert not result(body.replace('loopType="Standard"', 'loopType="MultiInstanceParallel"'), prop)


def test_path_rejects_both_outcomes_on_same_branch():
    prop = {
        "kind": "path",
        "from": {"tags": ["intermediateCatchEvent"]},
        "to": {"tags": ["task"], "pattern": "Success"},
        "not_to": {"tags": ["task"], "pattern": "Timeout"},
    }
    body = (
        '<process><intermediateCatchEvent id="s"/><task id="a" name="Success"/><task '
        'id="b" name="Timeout"/><sequenceFlow sourceRef="s" targetRef="a"/>{}</proces'
        "s>"
    )
    assert result(body.format(""), prop)
    assert not result(body.format('<sequenceFlow sourceRef="a" targetRef="b"/>'), prop)


def test_missing_xml_does_not_pass_negative_requirements():
    case = {
        "properties": [{"id": "no", "kind": "count", "select": {"tags": ["process"]}, "min": 0, "max": 0}]
    }
    assert not evaluate_properties(case, {})[0]["passed"]
    assert not evaluate_properties(case, {"bpmn_xml": "<foreign/>"})[0]["passed"]


def test_document_rejects_duplicate_ids_and_doctype():
    with pytest.raises(ValueError, match="Duplicate"):
        ChallengeOracle(xml('<process id="p"/><process id="p"/>'))
    with pytest.raises(ValueError, match="DOCTYPE"):
        ChallengeOracle('<!DOCTYPE definitions [<!ENTITY x "x">]>' + xml(""))


def test_corpus_is_distinct_and_uses_supported_predicates_without_fixed_ids():
    corpus = json.loads(Path("examples/document_challenge.json").read_text(encoding="utf-8"))
    cases = corpus["cases"]
    assert len(cases) == len({c["id"] for c in cases}) == 9
    assert sum(c["input_style"] == "business_natural" for c in cases) == 6
    old = json.loads(Path("examples/document_benchmark.json").read_text(encoding="utf-8"))["cases"]
    assert not set(c["text"] for c in cases) & set(c["text"] for c in old)
    oracle = ChallengeOracle(xml("<process><startEvent/></process>"))
    for case in cases:
        assert len(case["properties"]) == len({p["id"] for p in case["properties"]})
        for prop in case["properties"]:
            oracle.check(prop)
            assert '"attrs": {"id"' not in json.dumps(prop)
