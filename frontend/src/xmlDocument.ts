/** Complete XML stays authoritative. The canvas is only one projection of it. */
export const XMLNS = {
  bpmn: 'http://www.omg.org/spec/BPMN/20100524/MODEL',
  bpmndi: 'http://www.omg.org/spec/BPMN/20100524/DI',
  dc: 'http://www.omg.org/spec/DD/20100524/DC',
  di: 'http://www.omg.org/spec/DD/20100524/DI',
  xsi: 'http://www.w3.org/2001/XMLSchema-instance',
};
export type XmlDiagram = { id: string; name: string; kind: string; rootId: string; canvas: boolean };
export type XmlBounds = { x: number; y: number; width: number; height: number };

export function parseBpmn(xml: string): XMLDocument {
  if (new TextEncoder().encode(xml).length > 2_000_000) throw new Error('BPMN-файл превышает 2 МБ.');
  if (/<!DOCTYPE|<!ENTITY/i.test(xml)) throw new Error('DTD и объявления сущностей запрещены.');
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  if (doc.querySelector('parsererror')) throw new Error('Некорректный XML. Проверьте закрывающие теги и атрибуты.');
  if (doc.documentElement.namespaceURI !== XMLNS.bpmn || doc.documentElement.localName !== 'definitions') {
    throw new Error('Корневой элемент должен быть BPMN definitions.');
  }
  const pending: Array<[globalThis.Element, number]> = [[doc.documentElement, 1]];
  let count = 0;
  while (pending.length) {
    const [element, depth] = pending.pop()!;
    if (++count > 2500 || depth > 48 || element.attributes.length > 64) {
      throw new Error('Документ превышает ресурсный лимит редактора: 2500 элементов, 48 уровней или 64 атрибута на элемент.');
    }
    for (const child of Array.from(element.children)) pending.push([child, depth + 1]);
  }
  return doc;
}

export const serializeBpmn = (doc: XMLDocument) => new XMLSerializer().serializeToString(doc);
export function localRef(value: string | null, context?: globalThis.Element): string {
  value = value?.trim() || '';
  if (!value.includes(':')) return value;
  const [prefix, local] = value.split(':');
  const namespace = context?.lookupNamespaceURI(prefix);
  const target = context?.ownerDocument.documentElement.getAttribute('targetNamespace');
  // Foreign QName references remain foreign; they must never select a same-id local element.
  return namespace && [XMLNS.bpmn, target].includes(namespace) ? local : value;
}
export function semanticName(element: globalThis.Element): string {
  const value = element.getAttributeNS(XMLNS.xsi, 'type');
  // Named expression fields keep their meaning even with tFormalExpression.
  // Abstract/generic notation elements may carry their concrete visual type.
  const typedNotation = ['baseElement', 'rootElement', 'flowElement', 'flowNode', 'activity', 'task', 'subProcess',
    'event', 'catchEvent', 'throwEvent', 'gateway', 'conversationNode', 'choreographyActivity', 'artifact',
    'loopCharacteristics', 'eventDefinition', 'resourceRole'];
  if (value && typedNotation.includes(element.localName)) {
    const [prefix, local] = value.includes(':') ? value.split(':') : ['', value];
    const namespace = element.lookupNamespaceURI(prefix || null);
    if (namespace === XMLNS.bpmn && /^t[A-Z]/.test(local)) return local[1].toLowerCase() + local.slice(2);
  }
  return element.localName;
}
export const elements = (root: Document | globalThis.Element) => Array.from(root.getElementsByTagName('*'));
export const elementById = (doc: Document, id: string) => elements(doc).find((element) => element.getAttribute('id') === id);
export function childOf(element: globalThis.Element, namespace: string, name: string) {
  return Array.from(element.children).find((child) => child.namespaceURI === namespace && child.localName === name);
}
export function nodePath(element: globalThis.Element): string {
  const parts: number[] = [];
  let current: globalThis.Element | null = element;
  while (current?.parentElement) {
    parts.unshift(Array.from(current.parentElement.children).indexOf(current));
    current = current.parentElement;
  }
  return `root${parts.map((part) => `/${part}`).join('')}`;
}
export function nodeAt(doc: Document, path: string): globalThis.Element | undefined {
  let element: globalThis.Element | undefined = doc.documentElement;
  for (const position of path.split('/').slice(1)) element = element?.children[Number(position)];
  return element;
}

export function documentDiagrams(doc: XMLDocument): XmlDiagram[] {
  return Array.from(doc.getElementsByTagNameNS(XMLNS.bpmndi, 'BPMNDiagram')).map((diagram) => {
    const plane = childOf(diagram, XMLNS.bpmndi, 'BPMNPlane');
    const rootId = localRef(plane?.getAttribute('bpmnElement') || null, plane);
    const root = elementById(doc, rootId);
    const hasConversation = root && elements(root).some((element) =>
      element.namespaceURI === XMLNS.bpmn && /conversation/i.test(semanticName(element)));
    // bpmn-js 18 skips a direct DataObject even when it has an explicit shape.
    // Keep its original ID and edges editable in the lossless document view.
    // An ordinary hidden definition used through DataObjectReference is safe.
    const hasDirectDataObjectShape = Array.from(plane?.children || []).some((shape) => {
      if (shape.namespaceURI !== XMLNS.bpmndi || shape.localName !== 'BPMNShape') return false;
      const element = elementById(doc, localRef(shape.getAttribute('bpmnElement'), shape));
      return element?.namespaceURI === XMLNS.bpmn && semanticName(element) === 'dataObject';
    });
    const rootKind = root && semanticName(root);
    const kind = rootKind === 'choreography' ? 'Хореография'
      : hasConversation ? 'Разговоры' : rootKind === 'collaboration' ? 'Взаимодействие' : 'Процесс';
    return { id: diagram.getAttribute('id') || '', name: diagram.getAttribute('name') || root?.getAttribute('name') || diagram.getAttribute('id') || kind,
      kind, rootId, canvas: Boolean(rootKind && ['process', 'collaboration', 'subProcess'].includes(rootKind)
        && !hasConversation && !hasDirectDataObjectShape) };
  });
}

export function diagramPlane(doc: XMLDocument, diagramId: string) {
  const diagram = Array.from(doc.getElementsByTagNameNS(XMLNS.bpmndi, 'BPMNDiagram'))
    .find((element) => element.getAttribute('id') === diagramId);
  return diagram ? childOf(diagram, XMLNS.bpmndi, 'BPMNPlane') : undefined;
}

export function boundsOf(shape: globalThis.Element): XmlBounds | null {
  const bounds = childOf(shape, XMLNS.dc, 'Bounds');
  if (!bounds) return null;
  const result = Object.fromEntries(['x', 'y', 'width', 'height'].map((key) => [key, Number(bounds.getAttribute(key))])) as XmlBounds;
  return Object.values(result).every(Number.isFinite) ? result : null;
}

/** Conservatively detect modeler data loss before granting graphical editing. */
export function roundtripLoss(original: string, serialized: string, editedIds = new Set<string>()): string[] {
  const before = parseBpmn(original);
  const after = parseBpmn(serialized);
  const problems: string[] = [];
  const signature = (element: globalThis.Element) => `${element.namespaceURI}|${element.localName}`;
  const qnameValue = (value: string, element: globalThis.Element) => {
    if (/^[\w.-]+:[\w.-]+$/.test(value)) {
      const [prefix, local] = value.split(':');
      return `{${element.lookupNamespaceURI(prefix) || prefix}}${local}`;
    }
    return value;
  };
  const editedAttributes = new Set(['id', 'name', 'default', 'attachedToRef', 'sourceRef', 'targetRef', 'processRef',
    'isExecutable', 'triggeredByEvent', 'eventGatewayType', 'instantiate', 'isInterrupting', 'cancelActivity',
    'isForCompensation', 'gatewayDirection', 'calledElement', 'isSequential', 'behavior', 'loopMaximum']);
  const knownNamespaces = new Set<string>([XMLNS.bpmn, XMLNS.bpmndi, XMLNS.dc, XMLNS.di]);
  function inspect(left: globalThis.Element, right: globalThis.Element | undefined, parentEditable = false, parentStrict = false) {
    const label = left.getAttribute('id') || left.tagName;
    const strict = parentStrict || !knownNamespaces.has(left.namespaceURI || '')
      || ['extensionElements', 'documentation'].includes(left.localName);
    const edited = !strict && (editedIds.has(left.getAttribute('id') || '') || parentEditable);
    if (!right) { if (!edited) problems.push(`Элемент ${label}`); return; }
    if (signature(left) !== signature(right) && !edited) { problems.push(`Элемент ${label}`); return; }
    const compensateDefault = !strict && left.namespaceURI === XMLNS.bpmn && left.localName === 'compensateEventDefinition'
      && right.namespaceURI === XMLNS.bpmn && right.localName === 'compensateEventDefinition';
    const trueBoolean = (value: string | null) => value === 'true' || value === '1';
    for (const attr of Array.from(left.attributes)) {
      if (attr.namespaceURI === 'http://www.w3.org/2000/xmlns/') continue;
      if (edited && ((!attr.namespaceURI && (left.namespaceURI !== XMLNS.bpmn || editedAttributes.has(attr.localName)))
        || attr.namespaceURI === XMLNS.xsi && attr.localName === 'type')) continue;
      const actual = right.getAttributeNS(attr.namespaceURI, attr.localName);
      // XML serializers may omit standard defaults without changing semantics.
      const harmlessDefault = !strict && left.namespaceURI === XMLNS.bpmn && !attr.namespaceURI && actual === null && (
        attr.value === 'false' && ['isExecutable', 'isForCompensation', 'isCollection', 'isSequential'].includes(attr.localName)
        || attr.value === 'true' && ['cancelActivity', 'isInterrupting'].includes(attr.localName)
        || attr.value === 'Unspecified' && attr.localName === 'gatewayDirection'
      );
      // OMG BPMN 2.0.2 Table 10.94, p.263 specifies true even though the XSD
      // declaration has no default. This is not a rule for vendor attributes.
      const normativeDefault = compensateDefault && !attr.namespaceURI && attr.localName === 'waitForCompletion'
        && trueBoolean(attr.value) && (actual === null || trueBoolean(actual));
      if (!harmlessDefault && !normativeDefault && (actual === null || qnameValue(actual, right) !== qnameValue(attr.value, left))) {
        problems.push(`Атрибут ${label}.${attr.name}`);
      }
    }
    // The reverse direction matters too: omission means true, so an inserted
    // false/0 changes compensation timing and cannot be silently normalized.
    if (compensateDefault && !left.hasAttribute('waitForCompletion') && right.hasAttribute('waitForCompletion')
      && !trueBoolean(right.getAttribute('waitForCompletion'))) problems.push(`Атрибут ${label}.waitForCompletion`);
    let preserveWhitespace = strict;
    for (let ancestor: globalThis.Element | null = left; ancestor && !strict; ancestor = ancestor.parentElement) {
      const space = ancestor.getAttributeNS('http://www.w3.org/XML/1998/namespace', 'space');
      if (space) { preserveWhitespace = space === 'preserve'; break; }
    }
    const meaningful = (element: globalThis.Element) => Array.from(element.childNodes).filter((node) =>
      node.nodeType === Node.ELEMENT_NODE || node.nodeType === Node.COMMENT_NODE || node.nodeType === Node.PROCESSING_INSTRUCTION_NODE
      || (node.nodeType === Node.TEXT_NODE || node.nodeType === Node.CDATA_SECTION_NODE) && (preserveWhitespace || Boolean(node.nodeValue?.trim())));
    const leftContent = meaningful(left);
    const rightContent = meaningful(right);
    const leftText = leftContent.filter((node) => node.nodeType !== Node.ELEMENT_NODE);
    const rightText = rightContent.filter((node) => node.nodeType !== Node.ELEMENT_NODE);
    const protectedText = !edited || left.children.length > 0 || leftText.some((node) => [Node.COMMENT_NODE, Node.PROCESSING_INSTRUCTION_NODE].includes(node.nodeType as 7 | 8));
    if (protectedText && leftText.map((node) => `${node.nodeType === Node.CDATA_SECTION_NODE ? Node.TEXT_NODE : node.nodeType}:${node.nodeValue}`).join('|')
      !== rightText.map((node) => `${node.nodeType === Node.CDATA_SECTION_NODE ? Node.TEXT_NODE : node.nodeType}:${node.nodeValue}`).join('|')) {
      problems.push(`Текст или комментарий ${label}`);
    }
    if (left.children.length && leftText.length && leftContent.map((node) => node.nodeType === Node.ELEMENT_NODE ? 'element' : 'text').join(',')
      !== rightContent.map((node) => node.nodeType === Node.ELEMENT_NODE ? 'element' : 'text').join(',')) {
      problems.push(`Порядок смешанного содержимого ${label}`);
    }
    const used = new Set<globalThis.Element>();
    for (const child of Array.from(left.children)) {
      const candidate = Array.from(right.children).find((other) => !used.has(other)
        && (child.getAttribute('id') ? child.getAttribute('id') === other.getAttribute('id') : signature(child) === signature(other)));
      if (candidate) used.add(candidate);
      inspect(child, candidate, edited && !['definitions', 'process', 'collaboration'].includes(left.localName), strict);
    }
  }
  inspect(before.documentElement, after.documentElement);
  const outside = (doc: Document) => Array.from(doc.childNodes).filter((node) =>
    node.nodeType === Node.COMMENT_NODE || node.nodeType === Node.PROCESSING_INSTRUCTION_NODE).map((node) => node.nodeValue).join('|');
  if (outside(before) !== outside(after)) problems.push('Комментарии или инструкции документа');
  return [...new Set(problems)];
}

export function setXmlAttribute(doc: XMLDocument, element: globalThis.Element, name: string, value: string | null) {
  if (name === 'id' && value && value !== element.getAttribute('id')) {
    if (elementById(doc, value)) throw new Error('Такой ID уже существует в документе.');
    const old = element.getAttribute('id');
    if (old) for (const item of elements(doc)) {
      for (const attr of Array.from(item.attributes)) {
        if (/Ref$|Refs$/.test(attr.localName) || ['sourceRef', 'targetRef', 'default', 'bpmnElement', 'choreographyActivityShape', 'sourceElement', 'targetElement'].includes(attr.localName)) {
          const updated = attr.value.split(/(\s+)/).map((part) => localRef(part, item) === old ? part.replace(new RegExp(`${old.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`), value) : part).join('');
          if (updated !== attr.value) item.setAttributeNS(attr.namespaceURI, attr.name, updated);
        }
      }
      if (/Ref$|Refs$/.test(item.localName) || ['incoming', 'outgoing'].includes(item.localName)) {
        for (const text of Array.from(item.childNodes).filter((node) => node.nodeType === Node.TEXT_NODE)) {
          if (localRef(text.nodeValue?.trim() || null, item) === old) {
            text.nodeValue = text.nodeValue!.replace(new RegExp(`${old.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?=\\s*$)`), value);
          }
        }
      }
    }
  }
  const prefix = name.includes(':') ? name.split(':')[0] : null;
  const namespace = prefix ? element.lookupNamespaceURI(prefix) || XMLNS[prefix as keyof typeof XMLNS] : null;
  if (prefix && !namespace) throw new Error('Для префикса атрибута не задано пространство имён.');
  if (value === null) element.removeAttributeNS(namespace, name.split(':').at(-1)!);
  else element.setAttributeNS(namespace, name, value);
}

export function moveShape(doc: XMLDocument, shape: globalThis.Element, dx: number, dy: number) {
  if (!dx && !dy) return;
  const id = shape.getAttribute('id');
  const plane = shape.parentElement;
  if (!plane) return;
  const semantic = elementById(doc, localRef(shape.getAttribute('bpmnElement'), shape));
  const container = semantic && (semanticName(semantic) === 'participant'
    ? elementById(doc, localRef(semantic.getAttribute('processRef'), semantic)) : semantic);
  const children = container && !shape.hasAttribute('participantBandKind') ? new Set(elements(container).flatMap((element) => [
    element.getAttribute('id') || '', ...(element.localName === 'flowNodeRef' ? [localRef(element.textContent?.trim() || null, element)] : []),
  ])) : new Set<string>();
  const planeShapes = elements(plane).filter((element) => element.namespaceURI === XMLNS.bpmndi && element.localName === 'BPMNShape');
  const owners = new Set([semantic?.getAttribute('id') || '', ...children]);
  const related = [shape, ...planeShapes.filter((element) => {
    if (element === shape) return false;
    const target = elementById(doc, localRef(element.getAttribute('bpmnElement'), element));
    const attachment = target && localRef(target.getAttribute('attachedToRef'), target);
    return localRef(element.getAttribute('choreographyActivityShape'), element) === id
      || children.has(localRef(element.getAttribute('bpmnElement'), element)) || Boolean(attachment && owners.has(attachment));
  })];
  const movedShapes = new Set(related.map((item) => item.getAttribute('id')));
  for (const band of planeShapes) if (!related.includes(band) && movedShapes.has(localRef(band.getAttribute('choreographyActivityShape'), band))) {
    related.push(band); movedShapes.add(band.getAttribute('id'));
  }
  const oldBounds = new Map(planeShapes.map((item) => [item, boundsOf(item)]));
  const movedIds = new Set(related.map((item) => localRef(item.getAttribute('bpmnElement'), item)));
  for (const item of related) for (const bounds of Array.from(item.getElementsByTagNameNS(XMLNS.dc, 'Bounds'))) {
    bounds.setAttribute('x', String(Number(bounds.getAttribute('x')) + dx));
    bounds.setAttribute('y', String(Number(bounds.getAttribute('y')) + dy));
  }
  for (const edge of Array.from(plane.getElementsByTagNameNS(XMLNS.bpmndi, 'BPMNEdge'))) {
    const connection = elementById(doc, localRef(edge.getAttribute('bpmnElement'), edge));
    const points = Array.from(edge.getElementsByTagNameNS(XMLNS.di, 'waypoint'));
    const shift = (point: globalThis.Element | undefined) => {
      if (point) { point.setAttribute('x', String(Number(point.getAttribute('x')) + dx)); point.setAttribute('y', String(Number(point.getAttribute('y')) + dy)); }
    };
    const reference = (name: string) => connection && (connection.getAttribute(name) || childOf(connection, XMLNS.bpmn, name)?.textContent?.trim() || null);
    const endpointMoved = (ref: string, diRef: string, point: globalThis.Element | undefined) => {
      if (diRef) return movedShapes.has(diRef);
      if (!movedIds.has(ref) || !point) return false;
      // A choreography participant can have a different band on every activity.
      // Without explicit sourceElement/targetElement, use the endpoint geometry.
      const candidates = planeShapes.filter((item) => localRef(item.getAttribute('bpmnElement'), item) === ref);
      const distances = candidates.map((item) => {
        const bounds = oldBounds.get(item); if (!bounds) return { item, distance: Infinity };
        const x = Number(point.getAttribute('x')), y = Number(point.getAttribute('y'));
        return { item, distance: Math.max(bounds.x - x, 0, x - bounds.x - bounds.width) ** 2
          + Math.max(bounds.y - y, 0, y - bounds.y - bounds.height) ** 2 };
      });
      const nearest = Math.min(...distances.map((item) => item.distance));
      return distances.filter((item) => item.distance === nearest).every((item) => related.includes(item.item));
    };
    const sourceMoved = endpointMoved(localRef(reference('sourceRef') || null, connection), localRef(edge.getAttribute('sourceElement'), edge), points[0]);
    const targetMoved = endpointMoved(localRef(reference('targetRef') || null, connection), localRef(edge.getAttribute('targetElement'), edge), points.at(-1));
    if (sourceMoved && targetMoved) {
      points.forEach(shift);
      for (const bounds of Array.from(edge.getElementsByTagNameNS(XMLNS.dc, 'Bounds'))) shift(bounds);
    } else { if (sourceMoved) shift(points[0]); if (targetMoved) shift(points.at(-1)); }
  }
}
