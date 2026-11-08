# Пример кода для создания BPMN-диаграммы
## Пример фреймворка (реализуется участниками самостоятельно)
### Переменные в песочнице, которые уже заданы перед выполнением кода LLM:
ROOT_START_TASK_ID - Идентификатор старт-узла корневого процесса
ROOT_END_TASK_ID - Идентификатор стоп-узла корневого процесса
ROOT_PROCESS_ID - Идентификатор корневого процесса

<subprocess_var_name>=DIAGRAM.create_subprocess('<имя подпроцесса>',<process_id_var_name или pool_line_id или group_id>) - генерация подпроцесса
<task_id_var_name>=DIAGRAM.add_task('<имя задачи>',<process_id_var_name или pool_line_id или group_id>) - генерация задачи
<task_id_var_name>=DIAGRAM.add_user_task('<имя задачи>',<process_id_var_name или pool_line_id или group_id>) - генерация пользовательской задачи
<task_id_var_name>=DIAGRAM.add_script_task('<имя задачи>',<process_id_var_name или pool_line_id или group_id>) - генерация скриптовой задачи
<pool_id>,<pool_line_id_list>=DIAGRAM.add_pool(<process_id_var_name или group_id>,<названий дорожек:List>) - создание пула/дорожек.
DIAGRAM.add_link(<task_id_parent>,<task_id_child>) - генерация связи (стрелки) между узлами.
<exclusive_gateway_task_id> = DIAGRAM.add_exclusive_gateway("имя эксклюзивного шлюза",<process_id_var_name или pool_line_id или group_id>) - генерация эксклюзивного шлюза
<parallel_gateway_task_id> = DIAGRAM.add_parallel_gateway("имя параллельного шлюза",<process_id_var_name или pool_line_id или group_id>) - генерация параллельного шлюза
<inclusive_gateway_task_id> = DIAGRAM.add_inclusive_gateway("имя инклюзивного шлюза",<process_id_var_name или pool_line_id или group_id>) - генерация инклюзивного шлюза
<group_id>=DIAGRAM.add_group('<название группы>',<process_id_var_name или pool_line_id или group_id>) - генерация группы

## Пример кода, который может сгенерировать LLM
```python
#Перед выполнением добавляется код по импорту фреймворка и инициализации ROOT_START_TASK_ID, ROOT_END_TASK_ID, ROOT_PROCESS_ID
parent_task_id_1 = DIAGRAM.add_task('<имя задачи1>', ROOT_PROCESS_ID)
DIAGRAM.add_link(ROOT_START_TASK_ID, parent_task_id_1)
DIAGRAM.add_link(parent_task_id_1, ROOT_END_TASK_ID)
#Далее происходит рендеринг процесса ROOT_PROCESS_ID в .bpmn файл
```