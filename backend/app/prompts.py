"""Versioned data-only process prompt for the restricted BPMN Python API."""

import json

from backend.app.document_prompt import DOCUMENT_PROMPT, schema_context

PROMPT_VERSION = "bpmn-python-v7-natural-composition"

_EXAMPLE_CODE = """bpmn.process(id="process", name="Регистрация заявки")
bpmn.pool(id="pool", name="Обработка заявки", process_id="process")
bpmn.lane(id="manager", process_id="process", name="Менеджер")
bpmn.start(id="start", process_id="process", lane_id="manager", name="Начало")
bpmn.task(id="register", process_id="process", lane_id="manager",
          name="Зарегистрировать заявку", source_quote="Менеджер регистрирует заявку")
bpmn.end(id="end", process_id="process", lane_id="manager", name="Завершено")
bpmn.flow(id="flow1", source="start", target="register")
bpmn.flow(id="flow2", source="register", target="end")"""

SYSTEM_PROMPT = DOCUMENT_PROMPT


def build_messages(description: str, feedback: list[dict] | None = None) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps({"description": description}, ensure_ascii=False)},
        {"role": "system", "content": schema_context(description, feedback)},
    ]
    if feedback:
        messages.append(
            {
                "role": "user",
                "content": json.dumps({"repair_feedback": feedback}, ensure_ascii=False),
            }
        )
    return messages
