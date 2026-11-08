import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, AutoComplete, Button, Collapse, Empty, Form, Input, InputNumber, Modal, Select, Space, Tabs, Tag, Tree, Tooltip } from 'antd';
import type { DataNode } from 'antd/es/tree';
import { Icon } from './Icon';
import { BpmnGlyph } from './BpmnGlyph';
import { ActivitySummary } from './ActivitySummary';
import { activityDetails } from './activityDetails';
import { boundsOf, childOf, diagramPlane, elementById, elements, localRef, moveShape, nodeAt, nodePath, parseBpmn, semanticName, serializeBpmn, setXmlAttribute, XMLNS } from './xmlDocument';

export interface SchemaAttribute { name: string; type: string; required: boolean; default?: string | null; enum: string[] }
export interface SchemaType {
  name: string; base?: string; abstract: boolean; mixed: boolean; text: boolean;
  attributes: SchemaAttribute[]; children: Array<{ tag: string; type: string; min: string; max: string }>;
  any_child: boolean; any_attribute: boolean; enum: string[];
}
export interface BpmnSchema {
  version: string; namespaces: Record<string, string>;
  elements: Record<string, { tag: string; type: string; substitution?: string; abstract: boolean }>;
  types: Record<string, SchemaType>;
}

const titleOf = (element: globalThis.Element) => element.getAttribute('name') || element.getAttribute('id') || element.localName;
const tagOf = (element: globalThis.Element, schema: BpmnSchema | null) => {
  const prefix = Object.entries(schema?.namespaces || XMLNS).find(([, uri]) => uri === element.namespaceURI)?.[0];
  return prefix ? `${prefix}:${element.localName}` : element.tagName;
};
function typeOf(element: globalThis.Element, schema: BpmnSchema | null): string | undefined {
  const actualType = element.getAttributeNS(XMLNS.xsi, 'type');
  if (actualType) {
    const [prefix, local] = actualType.includes(':') ? actualType.split(':') : ['', actualType];
    const namespace = element.lookupNamespaceURI(prefix || null);
    const canonical = Object.entries(schema?.namespaces || XMLNS).find(([, uri]) => uri === namespace)?.[0];
    if (canonical && schema?.types[`${canonical}:${local}`]) return `${canonical}:${local}`;
  }
  return schema?.elements[tagOf(element, schema)]?.type;
}

function DiPreview({ doc, diagramId, selectedPath, disabled, select, change }: {
  doc: XMLDocument; diagramId: string; selectedPath: string; disabled: boolean;
  select: (path: string) => void; change: (edit: (doc: XMLDocument) => void) => void;
}) {
  const [scale, setScale] = useState(1);
  const [fit, setFit] = useState(true);
  const [viewport, setViewport] = useState({ width: 0, height: 0 });
  const scroll = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: string; x: number; y: number; clientX: number; clientY: number } | null>(null);
  const plane = diagramPlane(doc, diagramId);
  const shapes = plane ? Array.from(plane.children).filter((element) => element.localName === 'BPMNShape')
    .sort((left, right) => Number(left.hasAttribute('participantBandKind')) - Number(right.hasAttribute('participantBandKind'))) : [];
  const edges = plane ? Array.from(plane.children).filter((element) => element.localName === 'BPMNEdge') : [];
  const allBounds = [...shapes, ...edges].flatMap((shape) => {
    const main = boundsOf(shape); const label = childOf(shape, XMLNS.bpmndi, 'BPMNLabel');
    const labelBounds = label && boundsOf(label);
    return [...(main ? [main] : []), ...(labelBounds ? [labelBounds] : [])];
  });
  const allPoints = edges.flatMap((edge) => Array.from(edge.getElementsByTagNameNS(XMLNS.di, 'waypoint')))
    .map((point) => ({ x: Number(point.getAttribute('x')), y: Number(point.getAttribute('y')) }));
  const left = Math.min(0, ...allBounds.map((b) => b.x - 30), ...allPoints.map((p) => p.x - 30));
  const top = Math.min(0, ...allBounds.map((b) => b.y - 30), ...allPoints.map((p) => p.y - 30));
  const width = Math.max(450, ...allBounds.map((b) => b.x + b.width + 30), ...allPoints.map((p) => p.x + 30)) - left;
  const height = Math.max(280, ...allBounds.map((b) => b.y + b.height + 30), ...allPoints.map((p) => p.y + 30)) - top;
  useEffect(() => {
    const container = scroll.current; if (!container) return;
    const resize = () => {
      const style = getComputedStyle(container);
      setViewport({ width: container.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
        height: container.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) });
    };
    const observer = new ResizeObserver(resize); observer.observe(container); resize();
    return () => observer.disconnect();
  }, [diagramId, shapes.length, edges.length]);
  const displayedScale = fit && viewport.width > 0 && viewport.height > 0
    ? Math.min(1, viewport.width / width, viewport.height / height) : scale;
  const setZoom = (value: number) => { setFit(false); setScale(Math.max(.05, Math.min(4, value))); };
  const selected = nodeAt(doc, selectedPath);
  const text = (value: string, x: number, y: number, maxWidth: number, maxHeight: number, key: string) => {
    const letters = Math.max(8, Math.floor(maxWidth / 6.5));
    const words = value.split(/\s+/); const lines: string[] = []; let line = '';
    for (const word of words) { if (line && (line + ' ' + word).length > letters) { lines.push(line); line = word; } else line += `${line ? ' ' : ''}${word}`; }
    if (line) lines.push(line);
    const visible = lines.slice(0, Math.max(1, Math.floor(maxHeight / 15)));
    return <text key={key} x={x} y={y - (visible.length - 1) * 7.5} textAnchor="middle" dominantBaseline="middle" className="document-di-text">
      {visible.map((item, i) => <tspan key={i} x={x} dy={i ? 15 : 0}>{item}</tspan>)}
    </text>;
  };
  function point(event: React.PointerEvent<SVGSVGElement>) {
    const matrix = svg.current?.getScreenCTM();
    return matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : new DOMPoint(0, 0);
  }
  if (!plane || !shapes.length && !edges.length) return <div className="document-no-di"><Empty description="В этой диаграмме нет фигур DI" /></div>;
  return <div className="document-di-stage">
    <div ref={scroll} className="document-di-scroll" data-testid="document-di-viewport">
      <svg ref={svg} data-testid="document-di" aria-label="Представление BPMN DI" role="img"
        viewBox={`${left} ${top} ${width} ${height}`} style={{ width: width * displayedScale, height: height * displayedScale }}
        onPointerMove={(event) => {
          if (drag.current && Math.hypot(event.clientX - drag.current.clientX, event.clientY - drag.current.clientY) > 4) event.currentTarget.style.cursor = 'grabbing';
        }}
        onPointerUp={(event) => {
          const pending = drag.current; drag.current = null; event.currentTarget.style.cursor = '';
          if (!pending || Math.hypot(event.clientX - pending.clientX, event.clientY - pending.clientY) < 4) return;
          const end = point(event); const dx = Math.round(end.x - pending.x); const dy = Math.round(end.y - pending.y);
          change((document) => { const shape = elementById(document, pending.id); if (shape) moveShape(document, shape, dx, dy); });
        }} onPointerCancel={() => { drag.current = null; }}>
        <defs>
          <marker id="document-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#526178" /></marker>
          <marker id="document-message-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="white" stroke="#526178" /></marker>
          <marker id="document-open-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8" fill="none" stroke="#526178" /></marker>
          <marker id="document-message-start" markerWidth="8" markerHeight="8" refX="4" refY="4"><circle cx="4" cy="4" r="3" fill="white" stroke="#526178" /></marker>
          <marker id="document-conditional-start" markerWidth="12" markerHeight="10" refX="1" refY="5" orient="auto"><path d="M1,5 6,1 11,5 6,9Z" fill="white" stroke="#526178" /></marker>
          <marker id="document-default-start" markerWidth="12" markerHeight="12" refX="0" refY="6" orient="auto"><path d="M5,1 9,11" stroke="#526178" /></marker>
        </defs>
        {shapes.filter((shape) => {
          const semantic = elementById(doc, localRef(shape.getAttribute('bpmnElement'), shape));
          return semantic && ['participant', 'lane', 'subProcess', 'subChoreography'].includes(semanticName(semantic)) && !shape.hasAttribute('participantBandKind');
        }).map((shape) => {
          const b = boundsOf(shape); return b && <rect key={`background-${shape.getAttribute('id')}`} {...b} fill="white" stroke="#dce1eb" />;
        })}
        {edges.map((edge) => {
          const semantic = elementById(doc, localRef(edge.getAttribute('bpmnElement'), edge));
          const waypoints = Array.from(edge.getElementsByTagNameNS(XMLNS.di, 'waypoint'));
          const points = waypoints.map((p) => `${p.getAttribute('x')},${p.getAttribute('y')}`).join(' ');
          const xs = waypoints.map((p) => Number(p.getAttribute('x'))); const ys = waypoints.map((p) => Number(p.getAttribute('y')));
          const label = childOf(edge, XMLNS.bpmndi, 'BPMNLabel'); const b = label && boundsOf(label);
          const kind = semantic ? semanticName(semantic) : '';
          const conversation = kind === 'conversationLink';
          const association = /Association$/.test(kind) || kind === 'association';
          const source = semantic && elementById(doc, localRef(semantic.getAttribute('sourceRef'), semantic));
          const isDefault = semantic && source && localRef(source.getAttribute('default'), source) === semantic.getAttribute('id');
          const conditional = semantic && childOf(semantic, XMLNS.bpmn, 'conditionExpression');
          const direction = semantic?.getAttribute('associationDirection');
          const endMarker = conversation || kind === 'association' && (!direction || direction === 'None') ? undefined
            : kind === 'messageFlow' ? 'url(#document-message-arrow)' : association ? 'url(#document-open-arrow)' : 'url(#document-arrow)';
          const startMarker = kind === 'messageFlow' ? 'url(#document-message-start)' : association && direction === 'Both' ? 'url(#document-open-arrow)'
            : isDefault ? 'url(#document-default-start)' : conditional ? 'url(#document-conditional-start)' : undefined;
          const active = semantic === selected || edge === selected;
          return <g key={edge.getAttribute('id')} data-testid={`document-edge-${semantic?.getAttribute('id') || ''}`}
            className="document-di-element" role="button" tabIndex={disabled ? -1 : 0} aria-label={`Связь ${titleOf(semantic || edge)}`}
            onKeyDown={(event) => { if (event.key === 'Enter') select(nodePath(semantic || edge)); }} onClick={() => select(nodePath(semantic || edge))}>
            {xs.length > 0 && <rect x={Math.min(...xs) - 7} y={Math.min(...ys) - 7} width={Math.max(...xs) - Math.min(...xs) + 14}
              height={Math.max(...ys) - Math.min(...ys) + 14} fill="transparent" />}
            <polyline points={points} fill="none" stroke="transparent" strokeWidth="14" />
            {conversation && <polyline points={points} fill="none" stroke={active ? '#4f46e5' : '#526178'} strokeWidth="5" />}
            <polyline points={points} fill="none" stroke={conversation ? 'white' : active ? '#4f46e5' : '#526178'} strokeWidth={conversation ? 2 : active ? 2 : 1.5}
              strokeDasharray={kind === 'messageFlow' ? '6 4' : association ? '2 4' : undefined}
              markerStart={startMarker} markerEnd={endMarker} />
            {b && semantic?.getAttribute('name') && text(semantic.getAttribute('name')!, b.x + b.width / 2, b.y + b.height / 2, b.width, b.height, 'label')}
          </g>;
        })}
        {shapes.map((shape) => {
          const b = boundsOf(shape); if (!b) return null;
          const semantic = elementById(doc, localRef(shape.getAttribute('bpmnElement'), shape));
          const kind = semantic ? semanticName(semantic) : ''; const band = shape.getAttribute('participantBandKind');
          const active = semantic === selected || shape === selected;
          const label = childOf(shape, XMLNS.bpmndi, 'BPMNLabel'); const lb = label && boundsOf(label);
          const title = semantic?.getAttribute('name') || (semantic && childOf(semantic, XMLNS.bpmn, 'text')?.textContent) || semantic?.getAttribute('id') || '';
          const isEvent = /Event$/.test(kind); const isGateway = /Gateway$/.test(kind);
          const isLaneHeader = !band && (kind === 'lane' || kind === 'participant' && semantic?.hasAttribute('processRef'));
          const expanded = shape.getAttribute('isExpanded') === 'true';
          return <g key={shape.getAttribute('id')} className="document-di-element" data-testid={`document-shape-${semantic?.getAttribute('id') || ''}`}
            aria-label={`${kind}: ${title}`} role="button" tabIndex={disabled ? -1 : 0}
            onKeyDown={(event) => { if (event.key === 'Enter') select(nodePath(semantic || shape)); }}
            onClick={() => select(nodePath(semantic || shape))}
            onPointerDown={(event) => {
              if (disabled || !svg.current || !shape.getAttribute('id')) return;
              event.preventDefault(); select(nodePath(semantic || shape)); const matrix = svg.current.getScreenCTM(); if (!matrix) return;
              const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
              svg.current.setPointerCapture(event.pointerId);
              drag.current = { id: shape.getAttribute('id')!, x: p.x, y: p.y, clientX: event.clientX, clientY: event.clientY };
            }}>
            <title>{kind}: {title}</title>
            <BpmnGlyph element={semantic} shape={shape} bounds={b} active={active} />
            {isLaneHeader ? shape.getAttribute('isHorizontal') === 'false'
              ? text(title, b.x + b.width / 2, b.y + 15, b.width - 8, 26, 'header')
              : <text transform={`translate(${b.x + 16} ${b.y + b.height / 2}) rotate(-90)`} textAnchor="middle" className="document-di-text">{title}</text>
              : !isGateway && !isEvent && !lb && text(title, b.x + b.width / 2, expanded ? b.y + 16 : b.y + b.height / 2, b.width - 10, expanded ? 26 : b.height - 6, 'inside')}
            {lb && text(title, lb.x + lb.width / 2, lb.y + lb.height / 2, lb.width, lb.height, 'external')}
          </g>;
        })}
      </svg>
    </div>
    <div className="document-di-controls"><Button size="small" aria-label="Уменьшить представление DI" onClick={() => setZoom(displayedScale / 1.25)}>−</Button>
      <Button size="small" type="text" aria-label="Масштаб DI. Сбросить до 100%" data-testid="document-di-scale" onClick={() => setZoom(1)}>{Math.round(displayedScale * 100)}%</Button>
      <Button size="small" aria-label="Увеличить представление DI" onClick={() => setZoom(displayedScale * 1.25)}>+</Button>
      <Button size="small" data-testid="document-di-fit" onClick={() => setFit(true)}>Вписать</Button><span className="field-note">Представление DI</span></div>
  </div>;
}

function ElementProperties({ doc, path, schema, disabled, change, select }: {
  doc: XMLDocument; path: string; schema: BpmnSchema | null; disabled: boolean;
  change: (edit: (doc: XMLDocument) => void, selected?: string) => void; select: (path: string) => void;
}) {
  const element = nodeAt(doc, path) || doc.documentElement;
  const effectiveType = typeOf(element, schema);
  const schemaType = effectiveType ? schema?.types[effectiveType] : undefined;
  const [values, setValues] = useState<Record<string, string | undefined>>(() => Object.fromEntries(Array.from(element.attributes)
    .filter((attr) => attr.namespaceURI !== 'http://www.w3.org/2000/xmlns/').map((attr) => [attr.name, attr.value])));
  const [extraName, setExtraName] = useState('');
  const [childTag, setChildTag] = useState<string>();
  const [newText, setNewText] = useState('');
  const textNodes = Array.from(element.childNodes).map((node, index) => ({ node, index })).filter(({ node }) =>
    ([Node.TEXT_NODE, Node.CDATA_SECTION_NODE, Node.COMMENT_NODE] as number[]).includes(node.nodeType) && Boolean(node.nodeValue?.trim()));
  const [texts, setTexts] = useState<Record<number, string>>(() => Object.fromEntries(textNodes.map(({ node, index }) => [index, node.nodeValue || ''])));
  const [modal, contextHolder] = Modal.useModal();
  const fields = [...new Set([...Object.keys(values), ...(schemaType?.attributes.map((attr) => attr.name) || [])])];
  const refs = elements(doc).filter((item) => item.hasAttribute('id')).map((item) => ({ value: item.getAttribute('id')!, label: `${titleOf(item)} · ${item.localName}` }));
  const shapes = Array.from(doc.getElementsByTagNameNS(XMLNS.bpmndi, 'BPMNShape')).filter((shape) => localRef(shape.getAttribute('bpmnElement'), shape) === element.getAttribute('id'));
  const [geometry, setGeometry] = useState<Record<string, number>>(() => Object.fromEntries(shapes.flatMap((shape) => {
    const bounds = boundsOf(shape); return bounds ? Object.entries(bounds).map(([key, value]) => [`${nodePath(shape)}:${key}`, value]) : [];
  })));
  const apply = () => change((document) => {
    const target = nodeAt(document, path); if (!target) return;
    for (const name of fields) if (name in values && values[name] !== target.getAttribute(name)) setXmlAttribute(document, target, name, values[name] ?? null);
    for (const [index, value] of Object.entries(texts)) if (target.childNodes[Number(index)]) target.childNodes[Number(index)].nodeValue = value;
    if (newText) target.appendChild(document.createTextNode(newText));
    for (const shape of shapes) {
      const current = nodeAt(document, nodePath(shape)); if (!current) continue;
      const bounds = boundsOf(current); const child = childOf(current, XMLNS.dc, 'Bounds'); if (!bounds || !child) continue;
      moveShape(document, current, geometry[`${nodePath(shape)}:x`] - bounds.x, geometry[`${nodePath(shape)}:y`] - bounds.y);
      for (const dimension of ['width', 'height'] as const) {
        const next = geometry[`${nodePath(shape)}:${dimension}`];
        if (next !== bounds[dimension]) child.setAttribute(dimension, String(next));
      }
    }
  });
  const children = schemaType?.children || [];
  return <div className="document-properties" data-testid="document-properties">
    {contextHolder}<div className="document-properties-heading"><strong>{semanticName(element)}</strong><Tag>{element.getAttribute('id') || 'Без ID'}</Tag></div>
    <ActivitySummary details={activityDetails(doc, element, schema)} />
    <Form layout="vertical" size="small" disabled={disabled} onFinish={apply}>
      <Collapse size="small" defaultActiveKey={['attributes', 'text']} items={[
        { key: 'attributes', label: 'Атрибуты', children: <>
          {fields.map((name) => {
            const spec = schemaType?.attributes.find((attr) => attr.name === name);
            const options = spec?.enum?.length ? spec.enum.map((value) => ({ value, label: value }))
              : /boolean$/.test(spec?.type || '') ? [{ value: 'true', label: 'Да' }, { value: 'false', label: 'Нет' }] : null;
            const reference = /Ref$|Refs$/.test(name) || ['sourceRef', 'targetRef', 'bpmnElement', 'default'].includes(name);
            return <Form.Item label={<span className="document-attribute-label">{name === 'name' ? 'Название' : name}
              {element.hasAttribute(name) && !spec?.required && <Button type="text" size="small" aria-label={`Удалить атрибут ${name}`}
                onClick={() => setValues({ ...values, [name]: undefined })}>×</Button>}</span>} key={name} required={spec?.required}>
              {options ? <Select showSearch allowClear optionFilterProp="label" value={values[name] || undefined} options={options}
                data-testid={`document-attr-${name}`} onChange={(value) => setValues({ ...values, [name]: value || undefined })} />
                : reference ? <AutoComplete value={values[name] || ''} options={refs} data-testid={`document-attr-${name}`}
                  filterOption={(query, option) => Boolean(option?.label.toLowerCase().includes(query.toLowerCase()))}
                  onChange={(value) => setValues({ ...values, [name]: value })} />
                  : <Input value={values[name] || ''} data-testid={`document-attr-${name}`} onChange={(event) => setValues({ ...values, [name]: event.target.value })} />}
            </Form.Item>;
          })}
          <Space.Compact className="document-attribute-add"><Input value={extraName} placeholder="Новый атрибут" aria-label="Новый атрибут" onChange={(event) => setExtraName(event.target.value)} />
            <Button disabled={!extraName || disabled} onClick={() => { setValues({ ...values, [extraName]: '' }); setExtraName(''); }}>+</Button></Space.Compact>
        </> },
        { key: 'text', label: 'Текст и комментарии', children: <>
          {textNodes.map(({ node, index }) => <Form.Item label={node.nodeType === Node.COMMENT_NODE ? 'Комментарий' : `Текст ${index + 1}`} key={index}>
            <Input.TextArea value={texts[index]} rows={3} data-testid={`document-text-${index}`} onChange={(event) => setTexts({ ...texts, [index]: event.target.value })} />
          </Form.Item>)}
          {!textNodes.length && <Input.TextArea rows={3} value={newText} data-testid="document-text-new" aria-label="Текст элемента" placeholder="Текст элемента" onChange={(event) => setNewText(event.target.value)} />}
        </> },
        ...(shapes.length ? [{ key: 'geometry', label: 'Положение и размер', children: shapes.map((shape, index) => <div key={nodePath(shape)} className="document-geometry">
          <span>{shape.getAttribute('participantBandKind') || `Фигура ${index + 1}`}</span>
          {['x', 'y', 'width', 'height'].map((key) => <Form.Item label={key} key={key}><InputNumber value={geometry[`${nodePath(shape)}:${key}`]}
            min={['width', 'height'].includes(key) ? 1 : undefined} data-testid={`document-geometry-${index}-${key}`}
            onChange={(value) => setGeometry({ ...geometry, [`${nodePath(shape)}:${key}`]: Number(value) })} /></Form.Item>)}
        </div>) }] : []),
      ]} />
      <Button type="primary" htmlType="submit" block className="document-apply" data-testid="document-apply">Применить изменения</Button>
    </Form>
    <div className="document-child-add"><h3>Добавить дочерний элемент</h3>
      <Select showSearch optionFilterProp="label" placeholder="Элемент BPMN" aria-label="Дочерний элемент" data-testid="document-child-type"
        value={childTag} onChange={setChildTag} disabled={disabled || !schema}
        options={[...new Map(children.map((item) => [item.tag, { value: item.tag, label: item.tag }])).values()]} />
      <Button block disabled={!childTag || disabled} data-testid="document-add-child" onClick={() => change((document) => {
        if (!childTag || !schema) return;
        const target = nodeAt(document, path); if (!target) return;
        const [prefix, local] = childTag.split(':'); const namespace = schema.namespaces[prefix];
        const created = document.createElementNS(namespace, `${target.lookupPrefix(namespace) || prefix}:${local}`);
        const kind = schema.types[schema.elements[childTag]?.type];
        if (kind?.attributes.some((attr) => attr.name === 'id')) created.setAttribute('id', `${local}_${crypto.randomUUID().replaceAll('-', '').slice(0, 10)}`);
        // Respect the schema's declaration order; required children remain explicit edits.
        const position = children.findIndex((item) => item.tag === childTag);
        const next = Array.from(target.children).find((item) => children.findIndex((entry) => entry.tag === tagOf(item, schema)) > position);
        target.insertBefore(created, next || null);
      })}>Добавить</Button>
      {schemaType?.any_child && <p className="field-note">Произвольные расширения доступны во вкладке XML.</p>}
    </div>
    {element !== doc.documentElement && <Button danger block disabled={disabled} data-testid="document-delete" onClick={() => modal.confirm({
      title: `Удалить ${titleOf(element)}?`, content: 'Связи с этим элементом нужно исправить перед экспортом.', okText: 'Удалить', cancelText: 'Отмена', okButtonProps: { danger: true },
      onOk: () => { const parent = element.parentElement; change((document) => nodeAt(document, path)?.remove()); if (parent) select(nodePath(parent)); },
    })}>Удалить элемент</Button>}
  </div>;
}

export function BpmnDocumentEditor({ xml, schema, diagramId, disabled, focusId, reason, onChange, onLayout }: {
  xml: string; schema: BpmnSchema | null; diagramId: string; disabled: boolean; focusId?: string;
  reason?: string; onChange: (xml: string) => void; onLayout: () => void;
}) {
  const doc = useMemo(() => parseBpmn(xml), [xml]);
  const [path, setPath] = useState('root');
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState('diagram');
  const [source, setSource] = useState(xml);
  const [error, setError] = useState('');
  useEffect(() => { if (focusId) { const element = elementById(doc, focusId); if (element) setPath(nodePath(element)); } }, [focusId, doc]);
  useEffect(() => setSource(xml), [xml]);
  function change(edit: (document: XMLDocument) => void) {
    try { const next = parseBpmn(xml); edit(next); onChange(serializeBpmn(next)); setError(''); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось изменить документ.'); }
  }
  const tree = (element: globalThis.Element): DataNode => ({ key: nodePath(element),
    title: <span title={`${element.tagName} ${element.getAttribute('id') || ''}`} data-testid={element.getAttribute('id') ? `document-node-${element.getAttribute('id')}` : undefined}>
      <span className="document-tree-type">{element.localName}</span><span>{element.getAttribute('name') || element.getAttribute('id')}</span>
    </span>, children: Array.from(element.children).map(tree),
  });
  const matching = query ? elements(doc).filter((element) => `${titleOf(element)} ${element.localName}`.toLowerCase().includes(query.toLowerCase())).slice(0, 100) : [];
  const placed = new Set(Array.from(doc.getElementsByTagNameNS(XMLNS.bpmndi, '*')).map((element) => localRef(element.getAttribute('bpmnElement'), element)));
  const visualTypes = new Set(['tFlowNode', 'tConversationNode', 'tParticipant', 'tLane', 'tArtifact', 'tSequenceFlow', 'tMessageFlow', 'tConversationLink', 'tDataObjectReference', 'tDataStoreReference', 'tDataAssociation']);
  function hasVisualType(type: string | undefined): boolean {
    const visited = new Set<string>();
    while (type && !visited.has(type)) {
      if (visualTypes.has(type.split(':').at(-1)!)) return true;
      visited.add(type); type = schema?.types[type]?.base;
    }
    return false;
  }
  const representedMessages = new Set(elements(doc).filter((element) => element.namespaceURI === XMLNS.bpmn && element.localName === 'messageFlowRef')
    .map((element) => localRef(element.textContent?.trim() || null, element)));
  const collapsed = new Set(Array.from(doc.getElementsByTagNameNS(XMLNS.bpmndi, 'BPMNShape'))
    .filter((shape) => shape.getAttribute('isExpanded') !== 'true').map((shape) => localRef(shape.getAttribute('bpmnElement'), shape)));
  function hiddenInContainer(element: globalThis.Element) {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (['subProcess', 'transaction', 'adHocSubProcess', 'subChoreography'].includes(semanticName(parent))
        && collapsed.has(parent.getAttribute('id') || '')) return true;
    }
    return false;
  }
  const unplaced = elements(doc).filter((element) => element.namespaceURI === XMLNS.bpmn && element.hasAttribute('id')
    && !placed.has(element.getAttribute('id')!) && hasVisualType(typeOf(element, schema))
    && !hiddenInContainer(element)
    && !(semanticName(element) === 'messageFlow' && (representedMessages.has(element.getAttribute('id')!) || semanticName(element.parentElement!) === 'choreography')));
  const selected = nodeAt(doc, path) ? path : 'root';
  return <div className="document-workbench" data-testid="bpmn-document-editor" aria-busy={disabled}>
    <aside className="document-tree-panel" aria-label="Структура документа BPMN">
      <div className="document-tree-heading"><strong>Документ BPMN</strong><Tag>{elements(doc).length}</Tag></div>
      <Input.Search placeholder="Найти элемент" aria-label="Найти элемент документа" value={query} onChange={(event) => setQuery(event.target.value)} allowClear />
      <div className="document-tree-scroll">{query ? matching.map((element) => <Button type="text" key={nodePath(element)} block onClick={() => setPath(nodePath(element))}>{titleOf(element)}</Button>)
        : <Tree treeData={[tree(doc.documentElement)]} defaultExpandedKeys={['root', ...Array.from(doc.documentElement.children).map(nodePath)]}
          selectedKeys={[selected]} onSelect={(keys) => { if (keys[0]) setPath(String(keys[0])); }} blockNode showLine />}</div>
    </aside>
    <section className="document-preview">
      <Tabs activeKey={tab} onChange={setTab} size="small" items={[{ key: 'diagram', label: 'Диаграмма' }, { key: 'xml', label: 'XML' }]}
        tabBarExtraContent={<Tooltip title="Пересчитать расположение всех элементов диаграммы"><Button size="small" disabled={disabled} onClick={onLayout} data-testid="document-layout" icon={<Icon name="fit" size={14} />}>Перестроить DI</Button></Tooltip>} />
      {reason && <Alert type="info" showIcon className="document-capability-note" title={reason} />}
      {unplaced.length > 0 && <div className="document-unplaced" data-testid="document-unplaced"><span>Без DI: {unplaced.length}</span>
        {unplaced.slice(0, 3).map((element) => <Button size="small" type="link" key={nodePath(element)} onClick={() => setPath(nodePath(element))}>{titleOf(element)}</Button>)}
      </div>}
      {error && <Alert type="error" showIcon title={error} closable onClose={() => setError('')} />}
      {tab === 'diagram' ? <DiPreview doc={doc} diagramId={diagramId} selectedPath={selected} disabled={disabled} select={setPath} change={change} />
        : <div className="document-source"><Input.TextArea aria-label="XML документа" data-testid="document-source" value={source} onChange={(event) => setSource(event.target.value)} disabled={disabled} spellCheck={false} />
          <Button type="primary" disabled={disabled || source === xml} data-testid="document-apply-source" onClick={() => {
            try { parseBpmn(source); onChange(source); setError(''); } catch (failure) { setError(failure instanceof Error ? failure.message : 'Некорректный XML'); }
          }}>Применить XML</Button></div>}
    </section>
    <ElementProperties key={`${selected}:${xml}`} doc={doc} path={selected} schema={schema} disabled={disabled} change={change} select={setPath} />
  </div>;
}
