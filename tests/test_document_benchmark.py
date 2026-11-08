import json

from scripts.document_benchmark import ROOT, XmlOracle, evaluate, evaluate_properties, summarize


def xml(content):
    return (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" targetNamespace="urn:test">'
        + content
        + "</definitions>"
    )


def test_full_document_uses_xml_not_empty_legacy_arrays():
    case = {"properties": [{"id": "sub", "kind": "count", "tag": "subProcess", "min": 1}]}
    assert evaluate_properties(
        case, {"ir": {"nodes": []}, "bpmn_xml": xml('<process id="p"><subProcess id="s"/></process>')}
    )[0]["passed"]
    assert not evaluate_properties(
        case, {"ir": {"nodes": [{"type": "subProcess"}]}, "bpmn_xml": xml('<process id="p"/>')}
    )[0]["passed"]


def test_missing_xml_never_passes_negative_assertion():
    case = {"properties": [{"id": "none", "kind": "count", "tag": "sequenceFlow", "max": 0}]}
    assert not evaluate_properties(case, {})[0]["passed"]


def test_order_detects_bypass_even_when_path_exists():
    body = (
        '<process id="p"><startEvent id="s"/><task id="a" name="Check"/><task id="b" name="Issue"/>'
        '<sequenceFlow id="f" sourceRef="s" targetRef="a"/>'
        '<sequenceFlow id="g" sourceRef="a" targetRef="b"/>{}</process>'
    )
    prop = {"id": "order", "kind": "order", "before": "Check", "after": "Issue"}
    assert XmlOracle(xml(body.format(""))).check(prop)["passed"]
    assert not XmlOracle(xml(body.format('<sequenceFlow sourceRef="s" targetRef="b"/>'))).check(prop)[
        "passed"
    ]


def test_compensation_requires_link_marker_and_no_ordinary_flow():
    body = (
        '<process id="p"><task id="a" name="Reserve"/><boundaryEvent id="e" attachedToRef="a">'
        '<compensateEventDefinition/></boundaryEvent><task id="h" name="Release" isForCompensation="true"/>'
        '<association id="link" sourceRef="e" targetRef="h" associationDirection="One"/>{}</process>'
    )
    prop = {"id": "c", "kind": "compensation", "host_pattern": "Reserve", "handler_pattern": "Release"}
    assert XmlOracle(xml(body.format(""))).check(prop)["passed"]
    assert not XmlOracle(xml(body.format('<sequenceFlow sourceRef="a" targetRef="h"/>'))).check(prop)[
        "passed"
    ]
    assert not XmlOracle(
        xml(body.format("").replace('isForCompensation="true"', 'isForCompensation="false"'))
    ).check(prop)["passed"]


def test_external_qname_does_not_resolve_to_local_suffix():
    data = xml(
        '<process id="p"/><collaboration>'
        '<participant id="a" processRef="ext:p" xmlns:ext="urn:external"/></collaboration>'
    )
    oracle = XmlOracle(data)
    participant = oracle.select("participant")[0]
    assert oracle.ref(participant, participant.get("processRef")) is None


def test_parallel_join_rejects_branch_that_bypasses_join():
    body = (
        '<process id="p"><task id="a" name="Chemical"/><task id="b" name="Biological"/>'
        '<parallelGateway id="j"/><task id="c" name="Approve"/>'
        '<sequenceFlow sourceRef="a" targetRef="j"/><sequenceFlow sourceRef="b" targetRef="j"/>'
        '<sequenceFlow sourceRef="j" targetRef="c"/>{}</process>'
    )
    prop = {"id": "join", "kind": "parallel_join", "branches": ["Chemical", "Biological"], "after": "Approve"}
    assert XmlOracle(xml(body.format(""))).check(prop)["passed"]
    assert not XmlOracle(xml(body.format('<sequenceFlow sourceRef="a" targetRef="c"/>'))).check(prop)[
        "passed"
    ]


def test_boundary_requires_correct_host_and_interrupting_attribute():
    body = (
        '<process id="p"><subProcess id="s"/>'
        '<boundaryEvent id="e" attachedToRef="s" cancelActivity="true">'
        "<timerEventDefinition/></boundaryEvent></process>"
    )
    prop = {
        "id": "timer",
        "kind": "boundary",
        "host_tag": "subProcess",
        "definition": "timerEventDefinition",
        "interrupting": True,
    }
    assert XmlOracle(xml(body)).check(prop)["passed"]
    assert not XmlOracle(xml(body.replace('cancelActivity="true"', 'cancelActivity="false"'))).check(prop)[
        "passed"
    ]
    assert not XmlOracle(xml(body.replace('attachedToRef="s"', 'attachedToRef="missing"'))).check(prop)[
        "passed"
    ]


def test_choreography_requires_initiator_and_message_direction():
    body = (
        '<message id="m" name="Application"/><choreography id="ch">'
        '<participant id="a" name="Applicant"/><participant id="b" name="Insurer"/>'
        '<messageFlow id="mf" sourceRef="a" targetRef="b" messageRef="m"/>'
        '<choreographyTask id="t" name="Send application" initiatingParticipantRef="a">'
        "<participantRef>a</participantRef><participantRef>b</participantRef>"
        "<messageFlowRef>mf</messageFlowRef></choreographyTask></choreography>"
    )
    prop = {
        "id": "message",
        "kind": "choreography_task",
        "pattern": "application",
        "initiator": "Applicant",
        "message": "Application",
    }
    assert XmlOracle(xml(body)).check(prop)["passed"]
    assert not XmlOracle(xml(body.replace('sourceRef="a"', 'sourceRef="b"'))).check(prop)["passed"]
    assert not XmlOracle(
        xml(body.replace('initiatingParticipantRef="a"', 'initiatingParticipantRef="b"'))
    ).check(prop)["passed"]


def test_semantic_failure_and_errors_remain_in_planned_denominator():
    case = {
        "id": "X",
        "title": "x",
        "group": "test",
        "expected_status": "ready",
        "properties": [{"id": "t", "kind": "count", "tag": "transaction", "min": 1}],
    }
    record = evaluate(
        case,
        {
            "status": "ready",
            "checks": dict.fromkeys(("code", "graph", "xsd", "layout"), "passed"),
            "attempt_count": 1,
            "bpmn_xml": xml("<process/>"),
        },
        10,
        {},
    )
    assert record["technical_pass"]
    assert not record["candidate_success"]
    summary = summarize([record], [case], 2)
    assert summary["candidate_success"] == {"numerator": 0, "denominator": 2, "rate": 0}
    assert summary["not_run"] == 1


def test_corpus_is_twelve_with_ten_builds_two_clarifications_and_four_natural():
    cases = json.loads((ROOT / "examples/document_benchmark.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == len({case["id"] for case in cases}) == 12
    assert sum(case["expected_status"] == "ready" for case in cases) == 10
    assert sum(case.get("input_style") == "business_natural" for case in cases) == 4
    assert all(case["properties"] for case in cases)
