"""An interpreter for literal-only bpmn calls. Never executes Python bytecode."""

import ast

from backend.app.builder import Builder
from backend.app.domain import PipelineError, ProcessIR

MAX_CODE_BYTES = 65536
MAX_AST_NODES = 10000
MAX_AST_DEPTH = 24
MAX_CALLS = 1200


def policy_error(message: str):
    raise PipelineError("CODE_POLICY", message)


def parse_calls(code: str) -> list[tuple[str, dict]]:
    if not isinstance(code, str):
        policy_error("Python должен быть строкой.")
    try:
        byte_length = len(code.encode("utf-8"))
    except UnicodeError:
        policy_error("Python содержит недопустимые символы Unicode.")
    if byte_length > MAX_CODE_BYTES:
        policy_error("Python превышает лимит 65536 байт.")
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        policy_error("Не удалось разобрать ограниченную Python-программу.")
    pending = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            policy_error("Превышен лимит размера или глубины AST.")
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    if not tree.body or len(tree.body) > MAX_CALLS:
        policy_error("Нужна непустая программа, максимум 1200 вызовов.")
    calls = []
    allowed = set(Builder().methods)
    for statement in tree.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            policy_error("Разрешены только вызовы bpmn.method с литеральными именованными аргументами.")
        call = statement.value
        receiver = call.func
        if not (
            isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == "bpmn"
            and receiver.attr in allowed
            and not call.args
        ):
            policy_error("Вызов отсутствует в разрешённом BPMN API.")
        kwargs = {}
        for kw in call.keywords:
            if kw.arg is None or kw.arg in kwargs or kw.arg.startswith("_"):
                policy_error("Распаковка, повторные и приватные аргументы запрещены.")
            # Current API only needs scalars; no eval/literal_eval or recursive expressions.
            if not isinstance(kw.value, ast.Constant) or type(kw.value.value) not in (str, bool, type(None)):
                policy_error("Аргументы API — строковые литералы, True/False или None.")
            if isinstance(kw.value.value, str) and len(kw.value.value) > 20000:
                policy_error("Слишком длинный строковый литерал.")
            kwargs[kw.arg] = kw.value.value
        calls.append((receiver.attr, kwargs))
    return calls


def interpret(code: str, source_text: str = "") -> ProcessIR:
    calls = parse_calls(code)
    if len(source_text) > 20000:
        raise PipelineError("INPUT_LIMIT", "Описание превышает 20000 символов.")
    builder = Builder(source_text=source_text)
    for name, kwargs in calls:
        try:
            builder.methods[name](**kwargs)
        except PipelineError:
            raise
        except (TypeError, ValueError) as exc:
            raise PipelineError(
                "API_ARGUMENT", f"Недопустимые аргументы bpmn.{name}: {str(exc)[:300]}"
            ) from exc
    return builder.build()
