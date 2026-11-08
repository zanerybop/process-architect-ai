import { boundsOf, childOf, elementById, localRef, semanticName, XMLNS, type XmlBounds } from './xmlDocument';

/** SVG notation for the lossless document view; XML remains the editable model. */
function Trigger({ kind, filled = false }: { kind: string; filled?: boolean }) {
  const fill = filled ? 'currentColor' : 'white';
  return <g data-bpmn-marker={kind} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
    {kind === 'message' && <><rect x="-9" y="-6" width="18" height="12" fill={fill} /><path d="M-9-6 0 1 9-6" stroke={filled ? 'white' : 'currentColor'} /></>}
    {kind === 'timer' && <><circle r="10" /><path d="M0-8v2m0 12v2M-8 0h2m12 0h2M-6-6l1.5 1.5m9 9L6 6M6-6 4.5-4.5m-9 9L-6 6M0-5v5l4 2" /></>}
    {kind === 'error' && <path d="m-7 9 4-18 4 8 6-8L3 9-1 1Z" fill={fill} />}
    {kind === 'signal' && <path d="M0-10 10 8H-10Z" fill={fill} />}
    {kind === 'escalation' && <path d="M0-10 9 9 0 3-9 9Z" fill={fill} />}
    {kind === 'cancel' && <path d="m-7-10 7 7 7-7 3 3-7 7 7 7-3 3-7-7-7 7-3-3 7-7-7-7Z" fill={fill} />}
    {kind === 'compensate' && <path d="M0-8-10 0 0 8ZM10-8 0 0 10 8Z" fill={fill} />}
    {kind === 'conditional' && <><rect x="-7" y="-9" width="14" height="18" /><path d="M-4-5h8m-8 4h8m-8 4h8" /></>}
    {kind === 'link' && <path d="M-10-4H1v-5L11 0 1 9V4h-11Z" fill={fill} />}
    {kind === 'terminate' && <circle r="10" fill="currentColor" stroke="none" />}
    {kind === 'multiple' && <path d="M0-11 10-3 6 10H-6L-10-3Z" fill={fill} />}
    {kind === 'parallelMultiple' && <path d="M-3-10h6v7h7v6H3v7h-6V3h-7v-6h7Z" fill={fill} />}
  </g>;
}

function eventTrigger(element: Element) {
  const definitions = Array.from(element.children).filter((child) => child.namespaceURI === XMLNS.bpmn)
    .flatMap((child) => /EventDefinition$/.test(semanticName(child)) ? [semanticName(child)]
      : child.localName === 'eventDefinitionRef' ? [elementById(element.ownerDocument, localRef(child.textContent, child))].filter(Boolean).map((item) => semanticName(item!)) : []);
  if (definitions.length > 1) return element.getAttribute('parallelMultiple') === 'true' ? 'parallelMultiple' : 'multiple';
  return definitions[0]?.replace('EventDefinition', '') || '';
}

function TaskMarker({ kind }: { kind: string }) {
  return <g transform="translate(15 15)" data-bpmn-task={kind} fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round">
    {kind === 'userTask' && <><circle cy="-4" r="3.5" /><path d="M-7 7v-3q0-4 7-4t7 4v3Z" /></>}
    {kind === 'manualTask' && <path d="M-7 6-9 1q-1-3 1-3l4 3V-6q0-3 2-3t2 3v3-4q2-2 3 1v3q2-2 3 1v3q3-1 3 2v6Z" />}
    {kind === 'serviceTask' && <><path d="m-2-9 4 0 1 3 3-1 2 4-2 2 2 3-3 3-3-1-1 3h-4l-1-3-3 1-2-4 2-2-2-3 3-3 3 1Z" /><circle r="3" /></>}
    {kind === 'scriptTask' && <><path d="M-6-8H7V5H-4l-3 4V-8Z" /><path d="M-3-4h7m-7 4h7" /></>}
    {kind === 'businessRuleTask' && <><rect x="-8" y="-7" width="16" height="14" /><path d="M-8-2H8M-3-7V7M-8 3H8" /><path d="M-8-7H8v5H-8Z" fill="currentColor" /></>}
    {['sendTask', 'receiveTask'].includes(kind) && <Trigger kind="message" filled={kind === 'sendTask'} />}
  </g>;
}

function ActivityMarkers({ element, shape, width, height }: { element: Element; shape: Element; width: number; height: number }) {
  const kind = semanticName(element);
  const loop = Array.from(element.children).find((child) => ['standardLoopCharacteristics', 'multiInstanceLoopCharacteristics'].includes(semanticName(child)));
  const choreography = ['choreographyTask', 'subChoreography', 'callChoreography'].includes(kind);
  const choreographyLoop: Record<string, string> = {
    Standard: 'loop', MultiInstanceSequential: 'sequential', MultiInstanceParallel: 'parallel',
  };
  const loopMarker = loop ? semanticName(loop) === 'standardLoopCharacteristics' ? 'loop'
    : loop.getAttribute('isSequential') === 'true' ? 'sequential' : 'parallel'
    : choreography ? choreographyLoop[element.getAttribute('loopType') || 'None'] : undefined;
  const markers = [
    ...((['subProcess', 'adHocSubProcess', 'transaction', 'subChoreography'].includes(kind) && shape.getAttribute('isExpanded') !== 'true')
      || ['subConversation', 'callConversation'].includes(kind) ? ['collapsed'] : []),
    ...(kind === 'adHocSubProcess' ? ['adhoc'] : []),
    ...(element.getAttribute('isForCompensation') === 'true' ? ['compensate'] : []),
    ...(loopMarker ? [loopMarker] : []),
  ];
  // Choreography participant bands are painted over the host. Keep its activity
  // markers in the central body, above the lowest participant band stack.
  const origin = boundsOf(shape);
  const lowerBands = choreography && origin ? Array.from(shape.parentElement?.children || [])
    .filter((band) => band.namespaceURI === XMLNS.bpmndi && band.localName === 'BPMNShape'
      && localRef(band.getAttribute('choreographyActivityShape'), band) === shape.getAttribute('id')
      && /^(?:bottom|middle)_/.test(band.getAttribute('participantBandKind') || ''))
    .map(boundsOf).filter((bounds): bounds is XmlBounds => bounds !== null) : [];
  const bottom = lowerBands.length ? Math.min(...lowerBands.map((bounds) => bounds.y - origin!.y)) : height;
  return <g transform={`translate(${width / 2 - (markers.length - 1) * 9} ${bottom - 12})`} fill="none" stroke="currentColor" strokeWidth="1.4">
    {markers.map((marker, index) => <g key={marker} data-bpmn-marker={marker} transform={`translate(${index * 18} 0)`}>
      {marker === 'collapsed' && <><rect x="-6" y="-6" width="12" height="12" fill="white" /><path d="M-4 0h8M0-4v8" /></>}
      {marker === 'adhoc' && <path d="M-7 2q3-8 7-3t7-3" strokeWidth="2" />}
      {marker === 'compensate' && <path d="M0-5-6 0 0 5ZM6-5 0 0 6 5Z" />}
      {marker === 'loop' && <><path d="M-5 3a6 6 0 1 1 9 1" /><path d="m-6-2 1 5 5-1" /></>}
      {marker === 'parallel' && <path d="M-5-6V6M0-6V6M5-6V6" strokeWidth="2" />}
      {marker === 'sequential' && <path d="M-6-5H6M-6 0H6M-6 5H6" strokeWidth="2" />}
    </g>)}
  </g>;
}

export function BpmnGlyph({ element, shape, bounds, active }: { element?: Element; shape: Element; bounds: XmlBounds; active: boolean }) {
  const { width: w, height: h } = bounds;
  const kind = element ? semanticName(element) : '';
  const band = shape.getAttribute('participantBandKind');
  const color = active ? '#4f46e5' : '#526178';
  const background = active ? '#eeedff' : '#fff';
  const cx = w / 2, cy = h / 2;
  let glyph;
  if (band) glyph = <rect width={w} height={h} fill={band.includes('non_initiating') ? '#e2e8f0' : background} />;
  else if (/Event$/.test(kind)) {
    const intermediate = ['intermediateCatchEvent', 'intermediateThrowEvent', 'boundaryEvent'].includes(kind);
    const dashed = (kind === 'boundaryEvent' && element?.getAttribute('cancelActivity') === 'false') || (kind === 'startEvent' && element?.getAttribute('isInterrupting') === 'false');
    const trigger = element ? eventTrigger(element) : '';
    glyph = <>
      <ellipse cx={cx} cy={cy} rx={cx} ry={cy} fill={background} strokeWidth={kind === 'endEvent' ? 3.5 : 1.4} strokeDasharray={dashed ? '4 3' : undefined} />
      {intermediate && <ellipse cx={cx} cy={cy} rx={Math.max(1, cx - 3)} ry={Math.max(1, cy - 3)} fill="none" strokeWidth="1" strokeDasharray={dashed ? '4 3' : undefined} />}
      {trigger && <g transform={`translate(${cx} ${cy}) scale(${Math.min(w, h) / 36})`}><Trigger kind={trigger} filled={['endEvent', 'intermediateThrowEvent'].includes(kind)} /></g>}
    </>;
  } else if (/Gateway$/.test(kind)) {
    const size = Math.min(w, h) / 50;
    glyph = <><polygon points={`${cx},0 ${w},${cy} ${cx},${h} 0,${cy}`} fill={background} />
      <g transform={`translate(${cx} ${cy}) scale(${size})`} fill="none" strokeWidth="3" data-bpmn-marker={kind}>
        {kind === 'parallelGateway' && <path d="M-11 0h22M0-11v22" />}
        {kind === 'exclusiveGateway' && shape.getAttribute('isMarkerVisible') !== 'false' && <path d="m-9-11 18 22m0-22L-9 11" />}
        {kind === 'inclusiveGateway' && <circle r="12" />}
        {kind === 'complexGateway' && <path d="M0-12v24M-12 0h24M-9-9 9 9M9-9-9 9" />}
        {kind === 'eventBasedGateway' && <><circle r="15" strokeWidth="1" />{element?.getAttribute('instantiate') !== 'true' && <circle r="12" strokeWidth="1" />}<g transform="scale(.75)"><Trigger kind={element?.getAttribute('eventGatewayType') === 'Parallel' ? 'parallelMultiple' : 'multiple'} /></g></>}
      </g></>;
  } else if (/conversation/i.test(kind)) {
    glyph = <><polygon points={`${w * .25},0 ${w * .75},0 ${w},${cy} ${w * .75},${h} ${w * .25},${h} 0,${cy}`} fill={background} strokeWidth={kind === 'callConversation' ? 3.5 : 1.4} />
      {element && <ActivityMarkers element={element} shape={shape} width={w} height={h} />}</>;
  } else if (['dataObject', 'dataObjectReference', 'dataInput', 'dataOutput'].includes(kind)) {
    const fold = Math.min(12, w * .25);
    const referenced = element && elementById(element.ownerDocument, localRef(element.getAttribute('dataObjectRef'), element));
    glyph = <><path d={`M0 0H${w - fold}L${w} ${fold}V${h}H0ZM${w - fold} 0V${fold}H${w}`} fill={background} />
      {['dataInput', 'dataOutput'].includes(kind) && <path d="M5 9h9V6l5 5-5 5v-3H5Z" fill={kind === 'dataOutput' ? color : 'white'} />}
      {(element?.getAttribute('isCollection') === 'true' || referenced?.getAttribute('isCollection') === 'true') && <path data-bpmn-marker="collection" d={`M${cx - 5} ${h - 11}v8m5-8v8m5-8v8`} strokeWidth="2" />}</>;
  } else if (['dataStore', 'dataStoreReference'].includes(kind)) {
    const ry = Math.min(8, h / 6);
    glyph = <><path d={`M0 ${ry}a${cx} ${ry} 0 0 1 ${w} 0V${h - ry}a${cx} ${ry} 0 0 1 ${-w} 0Z`} fill={background} />
      <ellipse cx={cx} cy={ry} rx={cx} ry={ry} fill="none" /><path d={`M0 ${ry + 4}a${cx} ${ry} 0 0 0 ${w} 0M0 ${ry + 8}a${cx} ${ry} 0 0 0 ${w} 0`} fill="none" /></>;
  } else if (kind === 'textAnnotation') {
    glyph = <><rect width={w} height={h} fill="transparent" stroke="none" /><path d={`M10 0H0V${h}h10`} fill="none" /></>;
  } else if (kind === 'group') {
    glyph = <rect width={w} height={h} rx="7" fill="transparent" strokeDasharray="7 3 1 3" />;
  } else if (['participant', 'lane'].includes(kind)) {
    const expanded = kind === 'lane' || Boolean(element?.getAttribute('processRef'));
    glyph = <><rect width={w} height={h} fill={expanded ? 'transparent' : background} />
      {expanded && <path d={shape.getAttribute('isHorizontal') === 'false' ? `M0 30H${w}` : `M30 0V${h}`} />}
      {element && childOf(element, XMLNS.bpmn, 'participantMultiplicity') && <path data-bpmn-marker="multiplicity" d={`M${cx - 5} ${h - 12}v8m5-8v8m5-8v8`} strokeWidth="2" />}</>;
  } else {
    const expanded = shape.getAttribute('isExpanded') === 'true';
    const call = ['callActivity', 'callChoreography'].includes(kind);
    glyph = <><rect width={w} height={h} rx={kind === 'message' ? 0 : 7} fill={expanded ? 'transparent' : background}
      strokeWidth={call ? 3.5 : 1.4} strokeDasharray={element?.getAttribute('triggeredByEvent') === 'true' ? '4 3' : undefined} />
      {kind === 'transaction' && <rect x="3" y="3" width={Math.max(1, w - 6)} height={Math.max(1, h - 6)} rx="5" fill="none" />}
      {kind === 'message' && <path d={`M0 0 ${cx} ${cy} ${w} 0`} fill="none" />}
      {/Task$/.test(kind) && <TaskMarker kind={kind} />}
      {element && <ActivityMarkers element={element} shape={shape} width={w} height={h} />}</>;
  }
  return <g transform={`translate(${bounds.x} ${bounds.y})`} color={color} stroke={color} strokeWidth="1.4" data-bpmn-symbol={kind}>{glyph}</g>;
}
