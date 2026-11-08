export type DefaultCase = { name: string; before: string; after: string; loss: boolean };
const wrap = (body: string) => `<b:definitions xmlns:b="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:v="urn:vendor" id="D" targetNamespace="urn:test"><b:process id="P">${body}</b:process></b:definitions>`;
const event = (value: string | null, attribute = 'waitForCompletion') => wrap(`<b:intermediateThrowEvent id="Throw">
  <b:compensateEventDefinition id="C"${value === null ? '' : ` ${attribute}="${value}"`}/></b:intermediateThrowEvent>`);
export const compensationDefaultCases: DefaultCase[] = [];
for (const value of ['true', '1']) {
  compensationDefaultCases.push({ name: `${value} to omitted`, before: event(value), after: event(null), loss: false });
  compensationDefaultCases.push({ name: `omitted to ${value}`, before: event(null), after: event(value), loss: false });
}
compensationDefaultCases.push({ name: 'true to 1', before: event('true'), after: event('1'), loss: false });
for (const value of ['false', '0']) {
  compensationDefaultCases.push({ name: `${value} to omitted`, before: event(value), after: event(null), loss: true });
  compensationDefaultCases.push({ name: `omitted to ${value}`, before: event(null), after: event(value), loss: true });
  compensationDefaultCases.push({ name: `true to ${value}`, before: event('true'), after: event(value), loss: true });
}
for (const value of ['true', '1', 'false', '0']) compensationDefaultCases.push({
  name: `vendor attribute ${value} cannot disappear`, before: event(value, 'v:waitForCompletion'), after: event(null), loss: true,
});
for (const [name, body, attribute] of [
  ['vendor boolean on process', '<b:task id="T" v:isExecutable="false"/>', 'v:isExecutable="false"'],
  ['vendor boolean on BPMN event', '<b:compensateEventDefinition id="C" v:cancelActivity="true"/>', 'v:cancelActivity="true"'],
  ['vendor metadata boolean', '<b:extensionElements><v:payload isExecutable="false"/></b:extensionElements>', 'isExecutable="false"'],
  ['BPMN-looking metadata', '<b:extensionElements><v:payload><b:compensateEventDefinition id="C" waitForCompletion="true"/></v:payload></b:extensionElements>', 'waitForCompletion="true"'],
  ['foreign compensation metadata', '<b:extensionElements><v:compensateEventDefinition waitForCompletion="true"/></b:extensionElements>', 'waitForCompletion="true"'],
  ['documentation content', '<b:documentation><b:compensateEventDefinition id="C" waitForCompletion="true"/></b:documentation>', 'waitForCompletion="true"'],
  ['wrong BPMN element', '<b:task id="T" waitForCompletion="true"/>', 'waitForCompletion="true"'],
] as const) compensationDefaultCases.push({ name, before: wrap(body), after: wrap(body.replace(` ${attribute}`, '')), loss: true });
compensationDefaultCases.push({ name: 'ordinary schema default still accepted',
  before: wrap('<b:task id="T" isForCompensation="false"/>'), after: wrap('<b:task id="T"/>'), loss: false });
