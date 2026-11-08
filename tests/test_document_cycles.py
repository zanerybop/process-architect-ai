"""Safety regressions for the conservative local token-persistence proof."""

import pytest
from lxml import etree

from backend.app.document_validation import validate_document_behavior


def flow(identifier, source, target, condition=None):
    content = f"<conditionExpression>{condition}</conditionExpression>" if condition is not None else ""
    return (
        f'<sequenceFlow id="{identifier}" sourceRef="{source}" targetRef="{target}">{content}</sequenceFlow>'
    )


def graph(gateway="inclusiveGateway", *, condition=None, default=False):
    return (
        '<startEvent id="enter"/>'
        f'<{gateway} id="review"' + (' default="return_a"' if default else "") + "/>"
        '<userTask id="measure"/><manualTask id="record"/><endEvent id="done"/>'
        + flow("begin", "enter", "review")
        + flow("return_a", "review", "measure", condition)
        + flow("return_b", "review", "record", condition)
        + flow("again_a", "measure", "review")
        + flow("again_b", "record", "review")
        + flow("exit", "review", "done")
    )


def root(body):
    return etree.fromstring(
        (
            '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
            'xmlns:local="urn:cycle-test" xmlns:ext="urn:foreign" targetNamespace="urn:cycle-test">'
            + body
            + "</definitions>"
        ).encode()
    )


def process(body):
    return f'<process id="workflow">{body}</process>'


def nested(body, loop="", extra=""):
    return process(
        '<startEvent id="outer_start"/><subProcess id="batch">'
        + loop
        + '<startEvent id="unit_start"/><subProcess id="inspection">'
        + body
        + "</subProcess>"
        + '<endEvent id="unit_end"/>'
        + flow("unit_in", "unit_start", "inspection")
        + flow("unit_out", "inspection", "unit_end")
        + extra
        + "</subProcess>"
        + '<endEvent id="outer_end"/>'
        + flow("outer_in", "outer_start", "batch")
        + flow("outer_out", "batch", "outer_end")
    )


def findings(report):
    return [
        finding
        for item in report.processes
        for finding in item.findings
        if finding.code == "BEHAVIOR_FORCED_CYCLE"
    ]


def test_inclusive_cycle_with_plain_end_does_not_drain_or_claim_early_scope_completion():
    document = root(process(graph()))
    before = etree.tostring(document)
    report = validate_document_behavior(document)
    assert report.status == "failed"
    (finding,) = findings(report)
    assert {"measure", "record", "review"}.issubset(finding.element_ids)
    assert finding.path[0] == "enter" and finding.path[-1] in finding.path[:-1]
    edges = {
        (node.get("sourceRef"), node.get("targetRef"))
        for node in document.iter()
        if isinstance(node.tag, str) and node.tag.endswith("}sequenceFlow")
    }
    assert all(pair in edges for pair in zip(finding.path, finding.path[1:]))
    assert not report.processes[0].complete_exploration
    assert etree.tostring(document) == before


def test_task_and_cycle_proven_after_entry_task_forks_to_both_join_inputs():
    body = '<startEvent id="enter"/><task id="fork"/><task id="branch"/>'
    body += '<parallelGateway id="join"/><endEvent id="done"/>'
    body += flow("a", "enter", "fork") + flow("b", "fork", "branch") + flow("c", "fork", "join")
    body += flow("d", "branch", "join") + flow("e", "join", "fork") + flow("f", "join", "done")
    assert findings(validate_document_behavior(root(process(body))))


def test_task_self_loop_cannot_disappear_through_its_other_ordinary_end():
    body = '<startEvent id="enter"/><task id="work"/><endEvent id="done"/>'
    body += flow("a", "enter", "work") + flow("b", "work", "work") + flow("c", "work", "done")
    assert findings(validate_document_behavior(root(process(body))))


@pytest.mark.parametrize("gateway", ["inclusiveGateway", "parallelGateway"])
def test_acyclic_fork_join_is_not_a_cycle_failure(gateway):
    body = f'<startEvent id="enter"/><{gateway} id="fork"/><{gateway} id="join"/>'
    body += '<task id="a"/><task id="b"/><endEvent id="done"/>'
    body += flow("f1", "enter", "fork") + flow("f2", "fork", "a") + flow("f3", "fork", "b")
    body += flow("f4", "a", "join") + flow("f5", "b", "join") + flow("f6", "join", "done")
    report = validate_document_behavior(root(process(body)))
    assert not findings(report) and report.status != "failed"


def test_exclusive_retry_can_choose_an_exit():
    body = graph("exclusiveGateway").replace("manualTask", "userTask")
    report = validate_document_behavior(root(process(body)))
    assert not findings(report) and report.status == "passed"


@pytest.mark.parametrize("condition,default", [("continue_review", False), (None, True)])
def test_conditional_or_default_cycle_edges_are_not_assumed_to_fire(condition, default):
    body = graph(condition=condition, default=default)
    if default:
        # Break the other unconditional cycle as well.
        body = body.replace(
            'id="return_b" sourceRef="review" targetRef="record">',
            'id="return_b" sourceRef="review" targetRef="record">'
            "<conditionExpression>more</conditionExpression>",
        )
    assert not findings(validate_document_behavior(root(process(body))))


def test_enclosed_plain_cycle_under_fixed_all_instances_and_noninterrupting_message():
    loop = (
        "<multiInstanceLoopCharacteristics><loopCardinality>4</loopCardinality>"
        "</multiInstanceLoopCharacteristics>"
    )
    listener = (
        '<subProcess id="listener" triggeredByEvent="true"><startEvent id="notice" isInterrupting="false">'
        '<messageEventDefinition/></startEvent><task id="log"/><endEvent id="logged"/>'
        + flow("notice_work", "notice", "log")
        + flow("notice_end", "log", "logged")
        + "</subProcess>"
    )
    report = validate_document_behavior(root(nested(graph(), loop, listener)))
    assert report.status == "failed"
    (nested_report,) = [
        item for item in report.processes if any(f.code == "BEHAVIOR_FORCED_CYCLE" for f in item.findings)
    ]
    assert nested_report.process_id == "inspection"
    assert report.processes[0].status == "inconclusive"
    assert all(not item.complete_exploration for item in report.processes)
    assert "локальная" in nested_report.findings[0].message


@pytest.mark.parametrize(
    "loop",
    [
        "<standardLoopCharacteristics/>",
        "<multiInstanceLoopCharacteristics><loopCardinality>0</loopCardinality></multiInstanceLoopCharacteristics>",
        "<multiInstanceLoopCharacteristics><loopCardinality>count</loopCardinality></multiInstanceLoopCharacteristics>",
        '<multiInstanceLoopCharacteristics behavior="One"><loopCardinality>4</loopCardinality>'
        "</multiInstanceLoopCharacteristics>",
        "<multiInstanceLoopCharacteristics><loopCardinality>4</loopCardinality><completionCondition>enough</completionCondition></multiInstanceLoopCharacteristics>",
    ],
)
def test_ancestor_loop_or_early_mi_completion_is_not_flattened(loop):
    assert not findings(validate_document_behavior(root(nested(graph(), loop))))


@pytest.mark.parametrize("kind", ["terminate", "error", "cancel"])
def test_sibling_end_can_cancel_the_ancestor_context(kind):
    body = nested(graph()).replace(
        '<endEvent id="unit_end"/>', f'<endEvent id="unit_end"><{kind}EventDefinition/></endEvent>'
    )
    assert not findings(validate_document_behavior(root(body)))


@pytest.mark.parametrize("interrupting", ["true", "false"])
def test_boundary_cancellation_context_is_conservatively_excluded(interrupting):
    body = nested(graph()).replace(
        "</process>",
        f'<boundaryEvent id="timeout" attachedToRef="batch" cancelActivity="{interrupting}">'
        "<timerEventDefinition><timeDuration>PT1M</timeDuration></timerEventDefinition></boundaryEvent></process>",
    )
    assert not findings(validate_document_behavior(root(body)))


def test_interrupting_event_subprocess_is_not_ignored():
    listener = (
        '<subProcess id="abort" triggeredByEvent="true"><startEvent id="signal">'
        "<messageEventDefinition/></startEvent></subProcess>"
    )
    assert not findings(validate_document_behavior(root(nested(graph(), extra=listener))))


@pytest.mark.parametrize(
    "attribute", ['isForCompensation="true"', 'startQuantity="2"', 'completionQuantity="2"']
)
def test_nonordinary_activity_semantics_are_excluded(attribute):
    body = graph().replace('<userTask id="measure"/>', f'<userTask id="measure" {attribute}/>')
    assert not findings(validate_document_behavior(root(process(body))))


def test_graph_reachability_through_unproven_prefix_is_not_execution_reachability():
    body = graph().replace(
        '<startEvent id="enter"/>', '<startEvent id="enter"/><exclusiveGateway id="choice"/>'
    )
    body = body.replace('sourceRef="enter" targetRef="review"', 'sourceRef="choice" targetRef="review"')
    body += flow("choose", "enter", "choice") + flow("avoid", "choice", "done")
    assert not findings(validate_document_behavior(root(process(body))))


def test_dormant_cycle_and_foreign_metadata_are_not_process_execution():
    main = '<startEvent id="safe_start"/><endEvent id="safe_end"/>' + flow("safe", "safe_start", "safe_end")
    metadata = "<extensionElements><ext:archive>" + process(graph()) + "</ext:archive></extensionElements>"
    report = validate_document_behavior(root(process(main) + metadata))
    assert not findings(report)
    dormant = graph().replace(flow("begin", "enter", "review"), flow("begin", "enter", "done"))
    assert not findings(validate_document_behavior(root(process(dormant))))


def test_local_qname_refs_work_but_foreign_suffix_does_not_match():
    body = graph().replace('sourceRef="review"', 'sourceRef="local:review"')
    assert findings(validate_document_behavior(root(process(body))))
    body = body.replace('sourceRef="local:review"', 'sourceRef="ext:review"')
    assert not findings(validate_document_behavior(root(process(body))))


def test_called_process_context_is_not_assumed_uncancellable():
    body = (
        process(graph()) + '<process id="caller"><callActivity id="call" calledElement="workflow"/></process>'
    )
    assert not findings(validate_document_behavior(root(body)))


def test_missing_scope_id_and_mixed_cardinality_are_conservatively_skipped():
    assert not findings(validate_document_behavior(root("<process>" + graph() + "</process>")))
    loop = (
        "<multiInstanceLoopCharacteristics><loopCardinality>4<ext:expression>n</ext:expression>"
        "</loopCardinality></multiInstanceLoopCharacteristics>"
    )
    assert not findings(validate_document_behavior(root(nested(graph(), loop))))
