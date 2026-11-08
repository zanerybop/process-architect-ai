"""Token semantics, meaningful counterexamples and honest analysis bounds."""

import json
from pathlib import Path

import pytest

from backend.app.behavior import BehaviorLimits, behavior_issues, validate_behavior
from backend.app.domain import Flow, Node
from backend.app.graph import validate_graph
from backend.app.serialization import serialize
from backend.app.xml_validation import validate_xml
from tests.test_xml import make_ir, parallel_ir, two_pools_ir, xor_ir
from worker.interpreter import interpret


def deadlock_ir():
    ir = parallel_ir()
    ir.nodes[1].type = "exclusiveGateway"
    ir.flows[1].name, ir.flows[2].name = "Первый способ", "Второй способ"
    return ir


def test_xor_to_and_deadlock_is_detected_despite_valid_xsd_and_structural_reachability():
    ir = deadlock_ir()
    assert not [issue for issue in validate_graph(ir) if issue.severity == "error"]
    report = validate_behavior(ir)
    assert report.status == "failed"
    finding = report.processes[0].findings[0]
    assert finding.code == "BEHAVIOR_DEADLOCK"
    assert finding.element_ids == ["join"]
    assert finding.path[0:2] == ["start", "split"]
    assert finding.path[-1] in {"legal", "finance"}
    assert len(finding.token_flows) == 1
    assert behavior_issues(report)[0].witness_path == finding.path
    xml_report = validate_xml(serialize(ir))
    assert xml_report["checks"] == {
        "xsd": "passed",
        "graph": "passed",
        "behavior": "failed",
        "layout": "passed",
        "import": "not_run",
    }
    assert xml_report["behavior"]["status"] == "failed"
    assert any(issue["code"] == "BEHAVIOR_DEADLOCK" for issue in xml_report["issues"])


@pytest.mark.parametrize("factory", [make_ir, parallel_ir, xor_ir])
def test_linear_and_parallel_join_and_alternative_ends_pass(factory):
    report = validate_behavior(factory())
    assert report.status == "passed"
    assert report.processes[0].complete_exploration
    assert report.processes[0].explored_states > 1
    assert not behavior_issues(report)


def test_plain_end_does_not_terminate_other_parallel_branch():
    ir = make_ir(
        [
            ("start", "startEvent"),
            ("split", "parallelGateway"),
            ("work", "task"),
            ("end_1", "endEvent"),
            ("end_2", "endEvent"),
        ],
        [("start", "split"), ("split", "end_1"), ("split", "work"), ("work", "end_2")],
    )
    report = validate_behavior(ir)
    assert report.status == "passed"
    assert report.processes[0].maximum_tokens == 2


def test_loop_with_exit_is_valid_without_claiming_inevitable_termination():
    ir = make_ir(
        [
            ("start", "startEvent"),
            ("work", "task"),
            ("choice", "exclusiveGateway"),
            ("revise", "task"),
            ("end", "endEvent"),
        ],
        [("start", "work"), ("work", "choice"), ("choice", "end"), ("choice", "revise"), ("revise", "work")],
    )
    report = validate_behavior(ir)
    assert report.status == "passed"
    assert report.processes[0].complete_exploration
    assert report.processes[0].maximum_tokens == 1
    assert report.conditions == "nondeterministic_not_evaluated"


def test_task_multiple_inputs_merge_independently_and_outputs_fork():
    ir = parallel_ir()
    ir.nodes[1].type = "task"  # Unconditional fan-out has implicit parallel semantics.
    ir.nodes[4].type = "task"  # Two arrivals each execute this task; they do not synchronize.
    assert validate_behavior(ir).status == "passed"


def test_mixed_parallel_gateway_consumes_all_inputs_and_produces_all_outputs():
    ir = parallel_ir()
    ir.nodes.append(Node(id="end_2", type="endEvent", process_id="process_1"))
    ir.flows.append(Flow(id="last_2", source="join", target="end_2"))
    report = validate_behavior(ir)
    assert report.status == "passed"
    assert report.processes[0].maximum_tokens == 2


def test_reachable_cycle_without_completion_is_not_passed():
    ir = make_ir(
        [("start", "startEvent"), ("loop", "task")],
        [("start", "loop"), ("loop", "loop")],
    )
    report = validate_behavior(ir)
    assert report.status == "failed"
    assert report.processes[0].complete_exploration
    assert report.processes[0].findings[0].code == "BEHAVIOR_NO_COMPLETION"


@pytest.mark.parametrize(
    "setting,code",
    [
        ({"max_states": 1}, "BEHAVIOR_STATE_LIMIT"),
        ({"max_transitions": 1}, "BEHAVIOR_TRANSITION_LIMIT"),
        ({"max_steps": 1}, "BEHAVIOR_STEP_LIMIT"),
        ({"max_tokens": 1}, "BEHAVIOR_TOKEN_LIMIT"),
    ],
)
def test_reaching_any_bound_is_inconclusive_not_passed(setting, code):
    report = validate_behavior(parallel_ir(), BehaviorLimits(**setting))
    assert report.status == "inconclusive"
    assert not report.processes[0].complete_exploration
    assert report.processes[0].findings[0].code == code
    assert all(issue.severity == "warning" for issue in behavior_issues(report))


def test_growing_loop_stops_at_token_bound():
    ir = make_ir(
        [("start", "startEvent"), ("fork", "task"), ("end", "endEvent")],
        [("start", "fork"), ("fork", "fork"), ("fork", "end")],
    )
    report = validate_behavior(ir, BehaviorLimits(max_tokens=4))
    assert report.status == "inconclusive"
    assert report.processes[0].findings[0].code == "BEHAVIOR_TOKEN_LIMIT"
    assert report.processes[0].explored_states < 50


def test_each_pool_is_checked_without_claiming_message_soundness():
    report = validate_behavior(two_pools_ir())
    assert report.status == "inconclusive"
    assert report.scope == "per_process_control_flow"
    assert report.collaboration == "inconclusive"
    assert len(report.processes) == 2
    assert all(process.status == "passed" for process in report.processes)
    assert behavior_issues(report)[0].code == "BEHAVIOR_COLLABORATION_NOT_CHECKED"


def test_deadlock_in_a_pool_is_failed_even_when_collaboration_is_unchecked():
    ir = deadlock_ir()
    other = two_pools_ir()
    ir.processes.extend(other.processes[1:])
    ir.nodes.extend(node for node in other.nodes if node.process_id == "process_2")
    ir.flows.extend(flow for flow in other.flows if flow.id.startswith("supplier_"))
    report = validate_behavior(ir)
    assert report.status == "failed"
    assert report.collaboration == "inconclusive"
    assert [process.status for process in report.processes] == ["failed", "passed"]


def test_condition_on_task_and_multiple_starts_are_explicitly_inconclusive():
    ir = make_ir()
    ir.flows[1].condition = "Условие не вычисляется"
    assert validate_behavior(ir).processes[0].findings[0].code == "BEHAVIOR_UNSUPPORTED_CONDITION"
    ir = make_ir()
    ir.nodes.append(Node(id="second_start", type="startEvent", process_id="process_1"))
    ir.flows.append(Flow(id="extra", source="second_start", target="check"))
    assert validate_behavior(ir).status == "inconclusive"
    assert validate_behavior(ir).processes[0].findings[0].code == "BEHAVIOR_START_SEMANTICS"


def test_invalid_references_do_not_crash_or_produce_pass():
    ir = make_ir()
    ir.flows[0].target = "missing"
    assert validate_behavior(ir).status == "inconclusive"
    assert validate_xml(serialize(make_ir()))["checks"]["behavior"] == "passed"
    assert validate_behavior(make_ir().model_copy(update={"processes": []})).status == "inconclusive"


def test_analysis_is_deterministic_and_does_not_mutate_ir():
    ir = deadlock_ir()
    before = ir.model_dump()
    first = validate_behavior(ir).model_dump()
    ir.nodes.reverse()
    ir.flows.reverse()
    assert validate_behavior(ir).model_dump() == first
    ir.nodes.reverse()
    ir.flows.reverse()
    assert ir.model_dump() == before


CASES = json.loads(Path("examples/cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize(
    "case", [case for case in CASES if case["expected_status"] == "ready"], ids=lambda case: case["id"]
)
def test_repository_owned_fixture_processes_have_completing_token_flow(case):
    code = Path(f"examples/programs/{case['id']}.py").read_text(encoding="utf-8")
    ir = interpret(code, case["text"])
    report = validate_behavior(ir)
    assert all(process.status == "passed" for process in report.processes), report.model_dump()
    assert report.status == ("inconclusive" if len(ir.processes) > 1 else "passed")
