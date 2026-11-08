# Источники и происхождение

## Проверенные официальные страницы Codex при подготовке (2026-09-28)
- AGENTS.md: https://developers.openai.com/codex/guides/agents-md
  Codex читает AGENTS.md как инструкции проекта; большой контекст лучше вынести
  в отдельные файлы и явно указать, какие материалы нужны задаче.
- Cloud: https://developers.openai.com/codex/cloud
  Подключение репозитория, окружение, задание, просмотр diff/результата.
- Cloud environments: https://developers.openai.com/codex/cloud/environments
  Setup, зависимости, сеть и переменные. На момент проверки secrets доступны setup,
  но удаляются перед agent phase. Не обещать live API тест через setup secret.
- IDE: https://developers.openai.com/codex/ide
  Локальная работа в редакторе с контекстом проекта.
- CLI: https://developers.openai.com/codex/cli
  Локальный запуск `codex` в директории проекта, чтение/изменение файлов и проверки.
- Authentication: https://developers.openai.com/codex/auth
  Авторизация Codex не является ключом приложения для обычного LLM API.

Некоторые URL перенаправляют в актуальную официальную документацию learn.chatgpt.com.
Не фиксируем название «самой новой модели» или тариф: это не требуется для задания.

## Материалы от пользователя
https://optimacons.info/upload/kb-articles/pdf/BPMN-2-0-2-specification-ENG.pdf
https://codenrock.com/api/files/8fe80a9f-aa2a-41db-a84f-f0aba3740f1c
Исходное условие переписано в docs/00_HACKATHON_REQUIREMENTS.md.
Пример API не прочитан; PDF не анализировался заново при создании этого handoff.

## Технические первоисточники для реализации — проверить при разработке
- BPMN 2.0.2 / XSD: https://www.omg.org/spec/BPMN/2.0.2/
- bpmn-js: https://bpmn.io/toolkit/bpmn-js/walkthrough/
- bpmn-js examples: https://github.com/bpmn-io/bpmn-js-examples
- layout candidate: https://github.com/bpmn-io/bpmn-auto-layout
- lxml validation: https://lxml.de/validation.html
- Python AST: https://docs.python.org/3/library/ast.html
- Container controls: https://docs.docker.com/engine/containers/run/

Проектные правила/лимиты и тестовые процессы — авторские решения в этом пакете,
не нормативные требования стандарта или реальные регламенты энергетической компании.
