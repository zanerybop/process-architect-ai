import pytest

from backend.app.domain import PipelineError
from worker.interpreter import interpret, parse_calls


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "x = 1",
        "while True: pass",
        "for x in []: pass",
        "open('x')",
        "eval('1')",
        "exec('1')",
        "bpmn.__class__()",
        "bpmn.task.__call__()",
        "bpmn.task(id=f'{1}')",
        "bpmn.task(id='a'+'b')",
        "[x for x in []]",
        "bpmn.task(**{})",
        "bpmn.task('x')",
        "bpmn.task(id=(lambda: 'x')())",
        "bpmn.task(id=bpmn.process(id='p', name='p'))",
        "bpmn.task(id='a', id='b')",
        "# empty",
        "bpmn.task(id=" + "[" * 1000 + "0" + "]" * 1000 + ")",
        "bpmn.process(id='p', name='" + "x" * 65536 + "')",
    ],
    ids=lambda code: code[:55],
)
def test_rejects_unsafe_language(code):
    with pytest.raises(PipelineError):
        parse_calls(code)


def test_program_calls_really_build_ir():
    ir = interpret('bpmn.process(id="p", name="Исходный процесс")')
    changed = interpret('bpmn.process(id="p", name="Другой процесс")')
    assert ir.processes[0].name == "Исходный процесс"
    assert changed.processes[0].name != ir.processes[0].name


def test_surrogate_from_json_is_controlled_policy_error():
    # JSON allows an escaped unpaired surrogate; UTF-8 encoding does not.
    import json

    code = json.loads('"bpmn.process(id=\\"p\\", name=\\"\\ud800\\")"')
    with pytest.raises(PipelineError) as caught:
        parse_calls(code)
    assert caught.value.issue.code == "CODE_POLICY"


def test_runner_has_no_secret_environment(monkeypatch):
    from worker.server import CHILD_ENV, sandbox_ready

    monkeypatch.setenv("LLM_API_KEY", "test-secret-do-not-forward")
    assert "LLM_API_KEY" not in CHILD_ENV
    assert "test-secret-do-not-forward" not in str(CHILD_ENV)
    # Local test processes are not a production sandbox.
    assert sandbox_ready() is False
