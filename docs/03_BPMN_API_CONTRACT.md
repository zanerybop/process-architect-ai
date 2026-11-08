# 03. Проектный контракт Python API и IR

Это наш проектный контракт. Оригинальный пример Codenrock получен 2026-09-28:
`references/organizer_api/api.md`; сопоставление — в соседнем README.md.
Пример использует DIAGRAM и присваивания; наш ограниченный синтаксис отличается.
Задание допускает самостоятельную реализацию интерфейса. Поддержка полного Python
и синтаксическая совместимость с DIAGRAM не заявляются.

## Python API документа 2.0

Новые запросы модели используют полный каталог официального BPMN 2.0.2.
`bpmn.document` создаёт definitions; `bpmn.element` — элемент внутри объявленного
родителя; `bpmn.attribute` — атрибут; `bpmn.content` — текст; `bpmn.namespace` —
пространство имён для внешних ссылок или расширений. Только строки, bool и None;
сценарии/выражения остаются данными и не исполняются.

```python
bpmn.document(id="definitions", name="Проверка заявки")
bpmn.element(id="proc", tag="process", parent="definitions", name="Проверка")
bpmn.element(id="sub", tag="subProcess", parent="proc", name="Проверка реквизитов")
bpmn.element(id="work", tag="userTask", parent="sub", name="Проверить реквизиты",
             source_quote="Менеджер проверяет реквизиты")
bpmn.element(id="timer", tag="boundaryEvent", parent="proc")
bpmn.attribute(element="timer", name="attachedToRef", value="sub")
bpmn.attribute(element="timer", name="cancelActivity", value=True)
bpmn.element(id="timer_definition", tag="timerEventDefinition", parent="timer")
bpmn.element(id="duration", tag="timeDuration", parent="timer_definition",
             type="tFormalExpression", text="PT2H")
```

Это фрагмент API, не законченный бизнес-процесс. Полные проверяемые примеры —
`examples/full_bpmn/`. У `element` поля `id`, `tag` обязательны; `parent`
обязателен кроме корня, `name`, `text`, `type`, `source_quote` необязательны.
`type` задаёт xsi:type, совместимый с объявлением XSD. `id` — ключ IR;
в XML он появляется только у типов с атрибутом id. У `flowNodeRef` и других
элементов ссылок ключ остаётся внутренним, а текст содержит XML ID цели.
Стандартные атрибуты допускаются и непосредственно в `bpmn.element`, например
`attachedToRef="sub", cancelActivity=True`; это эквивалент отдельных
`bpmn.attribute`. Числовые значения передаются строками, например `value="3"`.

Документный IR: `schema_version="2.0"`, `document={namespaces,elements}`.
Элемент: `key`, `tag`, `parent`, `attributes`, `text`, `tail`. Вложенность,
QName, смешанное содержимое и стандартные свойства не сплющиваются в nodes/flows.
Официальный XSD проверяет обязательные атрибуты, лексические типы и кратности;
отдельный валидатор — ссылки, границы областей, события и DI.
Наличие типа в каталоге не доказывает корректность всех его сочетаний или
качество генерации моделью. Исполнение выражений, транзакций и компенсаций
не входит в этот API; они сохраняются как стандартная модель BPMN.

Каталог для интерфейса: `GET /api/bpmn/schema` (авторизованный доступ).
`POST /api/bpmn/layout {xml}` явно перестраивает расположение текущего документа,
сохраняя семантику; сохранение ревизии не перестраивает DI автоматически.

Ресурсные лимиты: код 65536 байт, 1200 вызовов, 10000 AST-узлов, глубина AST 24,
2500 элементов документа, глубина вложенности 48. Это защита worker, а не список
разрешённых разновидностей BPMN. Внешние import хранятся без загрузки;
разрешение внешних ссылок отмечается как непроверенное.

## Python API 1.0 — совместимость сохранённых программ
В контексте выполнения заранее существует объект `bpmn`. Код содержит только
вызовы перечисленных методов с литеральными именованными аргументами. Нет импортов,
циклов, функций, выражений доступа к объектам или вычислений условий в Python.
Все ID — строки, XML-safe и уникальные. Методы ничего не пишут на диск.

```python
bpmn.process(id="proc_1", name="Обработка заявки")
bpmn.pool(id="pool_1", name="Организация", process_id="proc_1")
bpmn.lane(id="lane_1", process_id="proc_1", name="Менеджер")
bpmn.start(id="start_1", process_id="proc_1", lane_id="lane_1", name="Заявка поступила")
bpmn.task(id="check_1", process_id="proc_1", lane_id="lane_1",
          name="Проверить заявку", kind="userTask", source_quote="Менеджер проверяет заявку")
bpmn.end(id="end_1", process_id="proc_1", lane_id="lane_1", name="Проверка завершена")
bpmn.flow(id="flow_1", source="start_1", target="check_1")
bpmn.flow(id="flow_2", source="check_1", target="end_1")
```

Обязательные методы: process, pool, lane, start, end, task,
xor_gateway, parallel_gateway, flow, message_flow.
Общий набор для flow node: id, process_id, lane_id, name, source_quote (необязательное).
Для `task.kind` в API 1.0 допустимы
`task/userTask/serviceTask/sendTask/receiveTask`.
Default kind — task, если специфический тип не обоснован текстом.
Для flow: id, source, target, name (опционально), condition (опционально),
is_default (по умолчанию false), source_quote (опциональная точная цитата).
condition — данные, не исполняемый Python.
В XML default ставится ссылкой на поток у исходного gateway, не случайным атрибутом flow.
Поток по умолчанию не получает conditionExpression. Для AND условий нет.
Для message_flow: id, source, target, name, source_quote (опционально); концы в разных пулах.
В API 1.0 `pool` требует `process_id`. В API документа 2.0 стандартный
`participant` без `processRef` представляет закрытый пул и получает DI.

## Общие поля и совместимый Process IR 1.0
Поля верхнего уровня: schema_version, document, processes, participants, lanes,
nodes, flows, source_text, provenance, questions, assumptions. В версии 2.0
семантическая модель хранится в `document`; плоские списки не заменяют её.
Следующие Node/Flow относятся к совместимой версии 1.0 и проекции для
ограниченной проверки цитат.
Node: id, type, name, process_id, lane_id.
Flow: id, kind(sequence|message), source, target, name, condition, is_default.
Provenance: element_id, exact_quote, source_start/source_end (если рассчитаны), origin.
Не доверять offset модели: вычислять/проверять совпадение цитаты с исходной строкой.
Origin: explicit / structural / proposed. Нормальные технические gateway/start/end
могут быть structural, но новые бизнес-действия требуют основания или согласия.
IR валидируется отдельно от Python и XML. Сериализация IR стабильна для одних данных.

## Envelope от LLM
mode: build | clarify | unsupported.
При build: python_code, summary, assumptions[].
При clarify: questions[] с конкретной неоднозначностью и цитатой.
При unsupported: reasons[] с ресурсным ограничением или запросом вне построения BPMN.
Сам по себе стандартный тип BPMN не является основанием для unsupported.
Не принимать nodes[] вместо Python и не вырезать ошибки из кода эвристиками.
Ограничить длину полей. Исправления — максимум два дополнительных запроса.

## HTTP API
GET /api/health — здоровье API, режим, доступность зависимостей, без секретов.
GET /api/examples — известные синтетические примеры.
POST /api/generate — {description, request_id?, clarification_answers?}; генерирует новую версию.
Ответы: до 10 объектов {question, answer, source_quote}; цитата, если указана,
должна буквально находиться в description. Весь эффективный контекст до 20 000 символов.
GET /api/generation-progress/{request_id} — реальный этап, попытка и прошедшее время;
состояние доступно только владельцу запроса и хранится до 10 минут в памяти API.
GET /api/generations/{id} — результат и метаданные.
POST /api/validate — {xml}; безопасная валидация текущей ревизии XML.
POST /api/review — {xml, generation_id?}; техническая проверка, ограниченные
смысловые правила и сравнение с неизменным IR исходной генерации владельца.
PUT /api/generations/{id}/revisions — {xml, base_revision_id}; сохранить ручные правки.
GET /api/generations/{id}/artifacts/{kind} — ограниченный список видов артефактов.

В ответе генерации: id, mode(live|mock), status, python_code, ir, bpmn_xml,
checks, issues, questions, timings, attempt_count, revision_id, source_context,
semantic_review, behavior. Сохранённая ревизия также содержит semantic_diff.
checks не одно boolean, а code/graph/behavior/xsd/layout/import:
passed|failed|not_run; у behavior также inconclusive.
Import на сервере not_run, если там не запускался редактор/браузер.

Issue: code, category(technical|model|question), severity, message,
element_ids[], source_quote?, suggested_action?, witness_path[].
BPMN XML может отсутствовать при needs_clarification/unsupported/error.
Синхронный generate допустим для MVP; ошибки и таймаут не зависают навсегда.
Progress endpoint сообщает фактические этапы, а не проценты. Запрос синхронный;
возобновляемые фоновые jobs не реализованы. Операционные маршруты защищены
сессией владельца, мутации — CSRF; health общедоступен.

## Артефакты результата
source.txt, generated.py, process_ir.json, generated.bpmn,
checks.json, metadata.json и revisions/*.bpmn.
metadata хранит provider/model, mode, реальные timings/usage при наличии и попытки.
Не включает API key, raw credentials, полное окружение или секретные заголовки.
Результаты реальной LLM могут различаться; deterministic относится к Builder/Serializer
и выбранной раскладке для одного IR, а не к гарантированно одинаковому ответу LLM.
