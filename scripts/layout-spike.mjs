import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Run from the repository root: node scripts/layout-spike.mjs
const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { layoutProcess } = require('bpmn-auto-layout');
const { BpmnModdle } = require('bpmn-moddle');
const outputDir = path.join(root, 'artifacts/m0');
await mkdir(outputDir, { recursive: true });

const escapeXml = (value) => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;');
const node = (id, type, name = id) => ({ id, type, name });
const flow = (id, source, target, name = '') => ({ id, source, target, name });
const linear = (prefix) => ({
  id: `${prefix}_process`,
  nodes: [node(`${prefix}_start`, 'startEvent'), node(`${prefix}_task`, 'userTask', 'Проверить комплектность заявки и приложенных технических документов'), node(`${prefix}_end`, 'endEvent')],
  flows: [flow(`${prefix}_f1`, `${prefix}_start`, `${prefix}_task`), flow(`${prefix}_f2`, `${prefix}_task`, `${prefix}_end`)],
});

function processXml(process) {
  const lanes = (process.lanes ?? []).map((lane) => `<bpmn:lane id="${lane.id}" name="${escapeXml(lane.name)}">${lane.nodes.map((id) => `<bpmn:flowNodeRef>${id}</bpmn:flowNodeRef>`).join('')}</bpmn:lane>`).join('');
  const nodes = process.nodes.map((element) => {
    const incoming = process.flows.filter((edge) => edge.target === element.id).map((edge) => `<bpmn:incoming>${edge.id}</bpmn:incoming>`).join('');
    const outgoing = process.flows.filter((edge) => edge.source === element.id).map((edge) => `<bpmn:outgoing>${edge.id}</bpmn:outgoing>`).join('');
    return `<bpmn:${element.type} id="${element.id}" name="${escapeXml(element.name)}">${incoming}${outgoing}</bpmn:${element.type}>`;
  }).join('');
  const flows = process.flows.map((edge) => `<bpmn:sequenceFlow id="${edge.id}" sourceRef="${edge.source}" targetRef="${edge.target}" name="${escapeXml(edge.name)}"/>`).join('');
  return `<bpmn:process id="${process.id}" isExecutable="false">${lanes ? `<bpmn:laneSet id="${process.id}_lanes">${lanes}</bpmn:laneSet>` : ''}${nodes}${flows}</bpmn:process>`;
}

function makeXml(test) {
  const collaboration = test.participants ? `<bpmn:collaboration id="collaboration">${test.participants.map((participant) => `<bpmn:participant id="${participant.id}" name="${escapeXml(participant.name)}" processRef="${participant.process}"/>`).join('')}${(test.messages ?? []).map((edge) => `<bpmn:messageFlow id="${edge.id}" sourceRef="${edge.source}" targetRef="${edge.target}" name="${escapeXml(edge.name)}"/>`).join('')}</bpmn:collaboration>` : '';
  return `<?xml version="1.0" encoding="UTF-8"?>\n<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions" targetNamespace="https://process-architect.local/spike">${test.processes.map(processXml).join('')}${collaboration}</bpmn:definitions>`;
}

function semanticGraph(definitions) {
  return definitions.rootElements.map((element) => ({
    id: element.id,
    type: element.$type,
    nodes: (element.flowElements ?? []).map((item) => ({ id: item.id, type: item.$type, name: item.name, source: item.sourceRef?.id, target: item.targetRef?.id })).sort((a, b) => a.id.localeCompare(b.id)),
    lanes: (element.laneSets ?? []).flatMap((set) => set.lanes.map((lane) => ({ id: lane.id, name: lane.name, nodes: lane.flowNodeRef.map((item) => item.id).sort() }))),
    participants: (element.participants ?? []).map((item) => ({ id: item.id, name: item.name, process: item.processRef?.id })),
    messages: (element.messageFlows ?? []).map((item) => ({ id: item.id, name: item.name, source: item.sourceRef?.id, target: item.targetRef?.id })),
  })).sort((a, b) => a.id.localeCompare(b.id));
}

const lanesProcess = linear('lane');
lanesProcess.lanes = [
  { id: 'lane_manager', name: 'Менеджер', nodes: ['lane_start', 'lane_task'] },
  { id: 'lane_engineer', name: 'Инженер', nodes: ['lane_end'] },
];
const cases = [
  { id: 'pool_lanes', processes: [lanesProcess], participants: [{ id: 'pool_1', name: 'Организация', process: lanesProcess.id }] },
  {
    id: 'xor_and',
    processes: [{
      id: 'branch_process',
      nodes: [node('start', 'startEvent'), node('xor', 'exclusiveGateway', 'Заявка полна?'), node('and_split', 'parallelGateway'), node('technical', 'task', 'Проверить технические условия'), node('financial', 'task', 'Проверить стоимость'), node('and_join', 'parallelGateway'), node('approved', 'endEvent', 'Одобрено'), node('rejected', 'endEvent', 'Отклонено')],
      flows: [flow('f1', 'start', 'xor'), flow('f2', 'xor', 'and_split', 'Да'), flow('f3', 'xor', 'rejected', 'Нет'), flow('f4', 'and_split', 'technical'), flow('f5', 'and_split', 'financial'), flow('f6', 'technical', 'and_join'), flow('f7', 'financial', 'and_join'), flow('f8', 'and_join', 'approved')],
    }],
  },
  {
    id: 'cycle',
    processes: [{
      id: 'cycle_process',
      nodes: [node('start', 'startEvent'), node('check', 'task', 'Проверить заявку'), node('choice', 'exclusiveGateway', 'Нужна доработка?'), node('revise', 'task', 'Доработать заявку'), node('end', 'endEvent')],
      flows: [flow('c1', 'start', 'check'), flow('c2', 'check', 'choice'), flow('c3', 'choice', 'revise', 'Да'), flow('c4', 'revise', 'check'), flow('c5', 'choice', 'end', 'Нет')],
    }],
  },
  {
    id: 'two_pools_message',
    processes: [linear('sender'), linear('receiver')],
    participants: [{ id: 'sender_pool', name: 'Заказчик', process: 'sender_process' }, { id: 'receiver_pool', name: 'Исполнитель', process: 'receiver_process' }],
    messages: [flow('message_1', 'sender_task', 'receiver_task', 'Заявка')],
  },
];

const packagePath = path.join(root, 'frontend/node_modules/bpmn-auto-layout/package.json');
const packageInfo = JSON.parse(await readFile(packagePath, 'utf8'));
const report = {
  library: `${packageInfo.name}@${packageInfo.version}`,
  command: 'node scripts/layout-spike.mjs',
  node: process.version,
  executed_at: new Date().toISOString(),
  scope: 'Проверка фактического DI, сохранения узлов/связей/дорожек/пулов; не XSD и не импорт в браузере.',
  cases: [],
};
for (const test of cases) {
  const input = makeXml(test);
  await writeFile(path.join(outputDir, `${test.id}.input.bpmn`), input, 'utf8');
  const expectedShapes = [...test.processes.flatMap((item) => [...item.nodes.map((element) => element.id), ...(item.lanes ?? []).map((lane) => lane.id)]), ...(test.participants ?? []).map((participant) => participant.id)];
  const expectedEdges = [...test.processes.flatMap((item) => item.flows.map((edge) => edge.id)), ...(test.messages ?? []).map((edge) => edge.id)];
  try {
    const before = await new BpmnModdle().fromXML(input);
    const output = await layoutProcess(input);
    await writeFile(path.join(outputDir, `${test.id}.output.bpmn`), output, 'utf8');
    const after = await new BpmnModdle().fromXML(output);
    const di = (after.rootElement.diagrams ?? []).flatMap((diagram) => diagram.plane.planeElement ?? []);
    const shapes = di.filter((element) => element.$type === 'bpmndi:BPMNShape').map((element) => element.bpmnElement?.id);
    const edges = di.filter((element) => element.$type === 'bpmndi:BPMNEdge').map((element) => element.bpmnElement?.id);
    const missingShapes = expectedShapes.filter((id) => !shapes.includes(id));
    const missingEdges = expectedEdges.filter((id) => !edges.includes(id));
    report.cases.push({
      id: test.id, status: missingShapes.length || missingEdges.length ? 'INCOMPLETE_DI' : 'COMPLETE_DI',
      semantic_graph_preserved: JSON.stringify(semanticGraph(before.rootElement)) === JSON.stringify(semanticGraph(after.rootElement)),
      expected_shape_count: expectedShapes.length, actual_shape_count: shapes.length,
      expected_edge_count: expectedEdges.length, actual_edge_count: edges.length,
      missing_shapes: missingShapes, missing_edges: missingEdges,
      parse_warnings: after.warnings.map((warning) => warning.message),
    });
  } catch (error) {
    report.cases.push({ id: test.id, status: 'ERROR', error: error.message });
  }
}
report.recommendation = report.cases.every((test) => test.status === 'COMPLETE_DI' && test.semantic_graph_preserved)
  ? 'Кандидат покрывает проверенные конструкции; далее необходимы XSD и браузерный импорт.'
  : 'Использовать ограниченную детерминированную раскладку под профиль MVP: кандидат не создаёт полный DI дорожек/участников/сообщений. Сохранение семантики проверять отдельно.';
await writeFile(path.join(outputDir, 'layout-spike.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
console.log(JSON.stringify(report, null, 2));
if (report.cases.some((test) => test.status === 'ERROR' || !test.semantic_graph_preserved)) process.exitCode = 1;
