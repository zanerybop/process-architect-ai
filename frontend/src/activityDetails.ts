import { elements, semanticName, XMLNS } from './xmlDocument';
import type { BpmnSchema } from './BpmnDocumentEditor';

export interface ActivityDetails {
  performers: string[]; candidates: string[]; resources: string[]; inputs: string[]; outputs: string[];
  messages: string[]; signals: string[]; errors: string[]; escalations: string[]; timers: string[];
}
const activities = new Set(['task', 'userTask', 'manualTask', 'serviceTask', 'sendTask', 'receiveTask',
  'scriptTask', 'businessRuleTask', 'callActivity', 'subProcess', 'transaction', 'adHocSubProcess']);
const events = new Set(['startEvent', 'endEvent', 'intermediateCatchEvent', 'intermediateThrowEvent', 'boundaryEvent']);
const eventDefinitions = new Set(['messageEventDefinition', 'signalEventDefinition', 'errorEventDefinition',
  'escalationEventDefinition', 'timerEventDefinition']);
const scopes = new Set(['process', 'subProcess', 'transaction', 'adHocSubProcess']);
const roles = new Set(['resourceRole', 'performer', 'humanPerformer', 'potentialOwner']);
const dataKinds = new Set(['dataObject', 'dataObjectReference', 'dataStore', 'dataStoreReference']);

/** Read only explicit current BPMN relationships; foreign content is never an assignment. */
export function activityDetails(doc: XMLDocument, selected: globalThis.Element | undefined,
  schema: BpmnSchema | null): ActivityDetails | null {
  function kind(element: globalThis.Element | null | undefined): string {
    if (!element) return '';
    for (let current: globalThis.Element | null = element; current; current = current.parentElement) {
      if (current.namespaceURI !== XMLNS.bpmn || ['extensionElements', 'documentation'].includes(current.localName)) return '';
    }
    const explicit = element.getAttributeNS(XMLNS.xsi, 'type');
    if (explicit) {
      // Do not trust a claimed type before its schema derivation can be checked.
      if (!schema) return '';
      const [prefix, name] = explicit.includes(':') ? explicit.split(':') : ['', explicit];
      if (element.lookupNamespaceURI(prefix || null) !== XMLNS.bpmn) return '';
      if (schema) {
        const expected = schema.elements[`bpmn:${element.localName}`]?.type;
        let actual: string | undefined = `bpmn:${name}`;
        const seen = new Set<string>();
        while (actual && actual !== expected && !seen.has(actual)) {
          seen.add(actual); actual = schema.types[actual]?.base;
        }
        if (expected && actual !== expected) return '';
      }
    }
    return semanticName(element);
  }
  if (!selected || !activities.has(kind(selected)) && !events.has(kind(selected))) return null;
  const semantic = elements(doc).filter(element => kind(element));
  const byId = new Map<string, globalThis.Element>();
  const duplicateIds = new Set<string>();
  for (const element of semantic) {
    const id = element.getAttribute('id');
    if (id) { if (byId.has(id)) duplicateIds.add(id); else byId.set(id, element); }
  }
  if (duplicateIds.has(selected.getAttribute('id') || '')) return null;
  function resolve(context: globalThis.Element, value: string | null) {
    const ref = value?.trim(); if (!ref) return undefined;
    const parts = ref.split(':');
    if (parts.length > 2 || parts.length === 2 && context.lookupNamespaceURI(parts[0]) !== doc.documentElement.getAttribute('targetNamespace')) return undefined;
    const id = parts.at(-1)!;
    return duplicateIds.has(id) ? undefined : byId.get(id);
  }
  const children = (element: globalThis.Element, name: string) => Array.from(element.children).filter(child => kind(child) === name);
  const title = (element: globalThis.Element) => element.getAttribute('name')?.trim() || element.getAttribute('id') || '';
  // References to event definitions and their payloads are typed root elements.
  // A similarly named node inside an activity is not a global definition.
  function rootReference(context: globalThis.Element, value: string | null, expected: string) {
    const target = resolve(context, value);
    return target?.parentElement === doc.documentElement && kind(target) === expected ? target : undefined;
  }
  const plainText = (element: globalThis.Element) => element.children.length ? '' : element.textContent?.trim() || '';
  const messages: string[] = [], signals: string[] = [], errors: string[] = [], escalations: string[] = [], timers: string[] = [];
  const linkedNames = (element: globalThis.Element, reference: string, expected: string, destination: string[]) => {
    const target = rootReference(element, element.getAttribute(reference), expected);
    if (target) {
      const codeAttribute = expected === 'error' ? 'errorCode' : expected === 'escalation' ? 'escalationCode' : null;
      destination.push(target.getAttribute('name')?.trim() || (codeAttribute && target.getAttribute(codeAttribute)?.trim()) || title(target));
    }
  };
  if (['sendTask', 'receiveTask'].includes(kind(selected))) linkedNames(selected, 'messageRef', 'message', messages);
  if (events.has(kind(selected))) {
    const definitions = Array.from(selected.children).filter(child => eventDefinitions.has(kind(child))
      && !duplicateIds.has(child.getAttribute('id') || ''));
    for (const reference of children(selected, 'eventDefinitionRef')) {
      const definition = resolve(reference, plainText(reference));
      if (definition?.parentElement === doc.documentElement && eventDefinitions.has(kind(definition))) definitions.push(definition);
    }
    for (const definition of definitions) {
      switch (kind(definition)) {
        case 'messageEventDefinition': linkedNames(definition, 'messageRef', 'message', messages); break;
        case 'signalEventDefinition': linkedNames(definition, 'signalRef', 'signal', signals); break;
        case 'errorEventDefinition': linkedNames(definition, 'errorRef', 'error', errors); break;
        case 'escalationEventDefinition': linkedNames(definition, 'escalationRef', 'escalation', escalations); break;
        case 'timerEventDefinition':
          for (const name of ['timeDuration', 'timeDate', 'timeCycle']) timers.push(...children(definition, name).map(plainText));
          break;
      }
    }
  }
  function scope(element: globalThis.Element) {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) if (scopes.has(kind(parent))) return parent;
    return null;
  }
  const performers: string[] = [], candidates: string[] = [], resources: string[] = [];
  for (const role of Array.from(selected.children).filter(child => roles.has(kind(child)))) {
    for (const reference of children(role, 'resourceRef')) {
      const resource = resolve(reference, reference.textContent);
      if (kind(resource) === 'resource') {
        const destination = kind(role) === 'potentialOwner' ? candidates : kind(role) === 'resourceRole' ? resources : performers;
        destination.push(title(resource!));
      }
    }
  }
  // An explicit resource assignment takes priority over the visual lane. Without
  // one, use the nearest lane membership of this activity or an enclosing scope.
  if (![performers, candidates, resources].some(values => values.length)) {
    for (let owner: globalThis.Element | null = selected; owner; owner = owner.parentElement) {
      if (owner !== selected && !scopes.has(kind(owner))) continue;
      const lanes = semantic.filter(element => kind(element) === 'lane' && scope(element) === scope(owner!)
        && children(element, 'flowNodeRef').some(ref => resolve(ref, ref.textContent) === owner));
      if (lanes.length) { performers.push(...lanes.map(title)); break; }
    }
  }
  function dataTitle(element: globalThis.Element | undefined): string | null {
    if (!element || !dataKinds.has(kind(element))) return null;
    const type = kind(element);
    if (type === 'dataObjectReference' || type === 'dataStoreReference') {
      const targetKind = type === 'dataObjectReference' ? 'dataObject' : 'dataStore';
      const target = resolve(element, element.getAttribute(`${targetKind}Ref`));
      if (!target || kind(target) !== targetKind) return null;
      return element.getAttribute('name')?.trim() || title(target);
    }
    return title(element);
  }
  function associatedData(direction: 'Input' | 'Output') {
    const result: string[] = [];
    for (const association of children(selected!, `data${direction}Association`)) {
      const ports = children(association, direction === 'Input' ? 'targetRef' : 'sourceRef')
        .map(reference => resolve(reference, reference.textContent));
      if (!ports.some(port => port && kind(port) === `data${direction}` && kind(port.parentElement) === 'ioSpecification'
        && port.parentElement?.parentElement === selected)) continue;
      for (const reference of children(association, direction === 'Input' ? 'sourceRef' : 'targetRef')) {
        const name = dataTitle(resolve(reference, reference.textContent));
        if (name) result.push(name);
      }
    }
    return [...new Set(result)];
  }
  const unique = (values: string[]) => [...new Set(values)].filter(Boolean);
  return { performers: unique(performers), candidates: unique(candidates), resources: unique(resources),
    inputs: associatedData('Input'), outputs: associatedData('Output'), messages: unique(messages), signals: unique(signals),
    errors: unique(errors), escalations: unique(escalations), timers: unique(timers) };
}
