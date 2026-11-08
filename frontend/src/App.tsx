import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, ComponentRef } from 'react';
import { Alert, Avatar, Badge, Button, Dropdown, Input, Modal, Popover, Select, Space, Spin, Tabs, Tag, Tooltip } from 'antd';
import { LogoutOutlined } from '@ant-design/icons';
import Modeler from 'bpmn-js/lib/Modeler';
import type Modeling from 'bpmn-js/lib/features/modeling/Modeling';
import type Canvas from 'diagram-js/lib/core/Canvas';
import type ElementRegistry from 'diagram-js/lib/core/ElementRegistry';
import type Selection from 'diagram-js/lib/features/selection/Selection';
import type CommandStack from 'diagram-js/lib/command/CommandStack';
import type { Element } from 'bpmn-js/lib/model/Types';
import { api } from './api';
import { translationModule } from './translate';
import { Icon } from './Icon';
import { useAuth } from './auth';
import { GenerationActivity } from './GenerationActivity';
import { QualityReview } from './QualityReview';
import { BpmnDocumentEditor } from './BpmnDocumentEditor';
import { ActivitySummary } from './ActivitySummary';
import { activityDetails } from './activityDetails';
import type { BpmnSchema } from './BpmnDocumentEditor';
import { diagramPlane, documentDiagrams, elementById, localRef, parseBpmn, roundtripLoss } from './xmlDocument';
import type { CheckName, Checks, EditorDocument, Example, Generation, GenerationSummary, Health, Issue, Validation } from './types';

const CHECK_LABELS: Record<CheckName, string> = {
  code: 'Python исходной генерации', graph: 'Структура графа', xsd: 'BPMN 2.0 XSD',
  layout: 'Геометрия DI', import: 'Импорт в редактор',
  behavior: 'Поведение ветвлений',
};
const CHECK_TEXT = { passed: 'Пройдено', failed: 'Ошибка', not_run: 'Не проверено', inconclusive: 'Частично' };
const STATUS_TEXT = {
  ready: 'Схема готова', needs_clarification: 'Нужны уточнения',
  unsupported: 'Не поддерживается', error: 'Ошибка построения',
};
const MAX_XML_BYTES = 2_000_000;
const technicalPassed = (checks: Checks) => ['graph', 'xsd', 'layout'].every(
  (key) => checks[key as CheckName] === 'passed',
) && checks.behavior !== 'failed';
const documentPassed = (checks: Checks) => checks.xsd === 'passed' && checks.graph === 'passed'
  && checks.layout !== 'failed' && checks.behavior !== 'failed';
const readableError = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить операцию.';
const uniqueIssues = (issues: Issue[]) => issues.filter((issue, index) => issues.findIndex(
  (other) => other.code === issue.code && other.message === issue.message
    && other.element_ids.join(',') === issue.element_ids.join(','),
) === index);

function downloadXml(xml: string, filename: string) {
  const url = URL.createObjectURL(new Blob([xml], { type: 'application/xml;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fitModeler(editor: Modeler | null) {
  if (!editor) return;
  const canvas = editor.get<Canvas>('canvas');
  canvas.resized();
  const { inner, outer } = canvas.viewbox();
  if (!inner.width || !inner.height || !outer.width || !outer.height) return;
  const container = canvas.getContainer();
  const palette = container.querySelector('.djs-palette');
  const paletteRight = palette ? palette.getBoundingClientRect().right - container.getBoundingClientRect().left : 80;
  const left = Math.max(85, paletteRight + 16);
  const scale = Math.min(1, Math.max(100, outer.width - left - 20) / inner.width,
    Math.max(100, outer.height - 50) / inner.height);
  const top = Math.max(25, (outer.height - inner.height * scale) / 2);
  canvas.viewbox({
    x: inner.x - left / scale, y: inner.y - top / scale,
    width: outer.width / scale, height: outer.height / scale,
  });
}

export default function App() {
  const { user, logout } = useAuth();
  const [modal, modalContext] = Modal.useModal();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState('');
  const [examples, setExamples] = useState<Example[]>([]);
  const [description, setDescription] = useState('');
  const [exampleId, setExampleId] = useState('');
  const [documentState, setDocumentState] = useState<EditorDocument | null>(null);
  const [history, setHistory] = useState<EditorDocument[]>([]);
  const [savedHistory, setSavedHistory] = useState<GenerationSummary[]>([]);
  const [lastResult, setLastResult] = useState<Generation | null>(null);
  const [status, setStatus] = useState('Редактор готов');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showPanel, setShowPanel] = useState(false);
  const [readingMode, setReadingMode] = useState(false);
  const [clarificationAnswers, setClarificationAnswers] = useState<string[]>([]);
  const [showConnection, setShowConnection] = useState(false);
  const [refreshingHealth, setRefreshingHealth] = useState(false);
  const [selected, setSelected] = useState<Element | null>(null);
  const [bpmnSchema, setBpmnSchema] = useState<BpmnSchema | null>(null);
  const [documentFocus, setDocumentFocus] = useState('');
  const [elementName, setElementName] = useState('');
  const [zoomPercent, setZoomPercent] = useState(100);
  const [techTab, setTechTab] = useState<'python' | 'ir' | 'xml' | 'checks'>('python');
  const host = useRef<HTMLDivElement>(null);
  const inspectorToggle = useRef<ComponentRef<typeof Button>>(null);
  const modeler = useRef<Modeler | null>(null);
  const current = useRef<EditorDocument | null>(null);
  const busyRef = useRef(false);
  const version = useRef(0);
  const canvasEditableIds = useRef(new Set<string>());
  const documentUndo = useRef<string[]>([]);
  const documentRedo = useRef<string[]>([]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => fitModeler(modeler.current));
    return () => cancelAnimationFrame(frame);
  }, [showPanel, readingMode]);

  function closeInspector() {
    setShowPanel(false);
    inspectorToggle.current?.focus();
  }

  function remember(value: EditorDocument) {
    setHistory((items) => [value, ...items.filter((item) => item.key !== value.key)]);
  }

  function updateDocument(value: EditorDocument) {
    current.current = value;
    setDocumentState(value);
    remember(value);
  }

  async function refreshHealth() {
    setRefreshingHealth(true);
    try {
      setHealth(await api.health());
      setHealthError('');
    } catch (failure) {
      setHealth(null);
      setHealthError(readableError(failure));
    } finally {
      setRefreshingHealth(false);
    }
  }

  useEffect(() => {
    let alive = true;
    void api.bpmnSchema().then((schema) => { if (alive) setBpmnSchema(schema); }).catch(() => {
      if (alive) setNotice('Каталог BPMN недоступен. Существующие свойства документа остаются доступны.');
    });
    void Promise.allSettled([api.health(), api.examples(), api.listGenerations()]).then(([healthResult, exampleResult, historyResult]) => {
      if (!alive) return;
      if (healthResult.status === 'fulfilled') setHealth(healthResult.value);
      else setHealthError(readableError(healthResult.reason));
      if (exampleResult.status === 'fulfilled') setExamples(exampleResult.value.cases);
      else setError('Не удалось загрузить примеры. Можно ввести собственное описание или открыть BPMN-файл.');
      if (historyResult.status === 'fulfilled') setSavedHistory(historyResult.value.items);
      else setNotice('Не удалось загрузить историю схем.');
    });
    const observer = new ResizeObserver(() => modeler.current?.get<Canvas>('canvas').resized());
    if (host.current) observer.observe(host.current);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (current.current?.dirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      alive = false;
      observer.disconnect();
      window.removeEventListener('beforeunload', beforeUnload);
      modeler.current?.destroy();
      modeler.current = null;
    };
  }, []);

  async function operation(label: string, action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(label);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (failure) {
      setError(readableError(failure));
      setStatus('Ошибка');
    } finally {
      busyRef.current = false;
      setBusy('');
    }
  }

  async function stageImport(xml: string, diagramId?: string) {
    if (!host.current) throw new Error('Редактор ещё не готов.');
    const container = document.createElement('div');
    container.className = 'diagram-instance';
    container.style.visibility = 'hidden';
    host.current.appendChild(container);
    const candidate = new Modeler({ container, additionalModules: [translationModule] });
    try {
      const result = await candidate.importXML(xml, diagramId || undefined);
      return { candidate, container, warnings: result.warnings.map((warning) =>
        String((warning as unknown as { message?: string }).message || warning)) };
    } catch {
      candidate.destroy();
      container.remove();
      throw new Error('Ошибка импорта BPMN в редактор. Проверьте файл и сообщения технических проверок.');
    }
  }

  function clearModeler() {
    const previousContainer = modeler.current?.get<Canvas>('canvas').getContainer().parentElement;
    modeler.current?.destroy();
    previousContainer?.remove();
    modeler.current = null;
    setSelected(null);
    setElementName('');
  }

  async function openDocument(value: EditorDocument, preferred: 'auto' | 'canvas' | 'document' = 'auto', diagramId?: string) {
    if (value.key !== current.current?.key) { documentUndo.current = []; documentRedo.current = []; }
    const xmlDocument = parseBpmn(value.xml);
    const diagrams = documentDiagrams(xmlDocument);
    const diagram = diagrams.find((item) => item.id === (diagramId || value.diagramId)) || diagrams[0];
    let canvasNotice = !diagram ? 'В документе нет диаграммы DI. Структура и свойства доступны для редактирования.'
      : !diagram.canvas ? `${diagram.kind}: редактируйте элементы и связи в структуре документа; фигуры DI можно перемещать.` : '';
    let staged: Awaited<ReturnType<typeof stageImport>> | null = null;
    if (preferred !== 'document' && diagram?.canvas) {
      try {
        staged = await stageImport(value.xml, diagram.id);
        const saved = await staged.candidate.saveXML({ format: true });
        // bpmn-js measures text and normalizes label bounds on import. Preserve all
        // semantic/extension content while allowing this visible DI normalization.
        const losses = saved.xml ? roundtripLoss(value.xml, saved.xml, new Set([diagram.id])) : ['Редактор не вернул XML'];
        const plane = diagramPlane(xmlDocument, diagram.id);
        const missingVisuals = Array.from(plane?.children || []).filter((shape) => ['BPMNShape', 'BPMNEdge'].includes(shape.localName))
          .map((shape) => localRef(shape.getAttribute('bpmnElement'), shape))
          .filter((id) => !staged!.candidate.get<ElementRegistry>('elementRegistry').get(id));
        if (missingVisuals.length) losses.push(`Не отображены элементы DI: ${missingVisuals.slice(0, 3).join(', ')}`);
        if (losses.length || staged.warnings.length) {
          canvasNotice = losses.length ? `Графический редактор изменяет данные документа (${losses.slice(0, 2).join('; ')}). Исходный XML сохранён.`
            : `Графический импорт имеет замечания: ${staged.warnings.slice(0, 2).join('; ')}. Доступен редактор документа.`;
          staged.candidate.destroy(); staged.container.remove(); staged = null;
        }
      } catch (failure) {
        staged?.candidate.destroy(); staged?.container.remove(); staged = null;
        canvasNotice = `${readableError(failure)} Документ доступен через структуру и свойства.`;
      }
    }
    if (!staged) {
      clearModeler();
      version.current += 1;
      updateDocument({ ...value, originalXml: value.originalXml || value.xml, diagramId: diagram?.id,
        editorMode: 'document', canvasNotice, checks: { ...value.checks, import: 'not_run' } });
      setDocumentFocus('');
      setReadingMode(true);
      return;
    }
    // Keep the previous modeler, its undo history and edits until the candidate
    // has actually imported. An unsuccessful candidate never clears the editor.
    const previousContainer = modeler.current?.get<Canvas>('canvas').getContainer().parentElement;
    modeler.current?.destroy();
    previousContainer?.remove();
    staged.container.style.visibility = '';
    modeler.current = staged.candidate;
    version.current += 1;
    const ready = {
      ...value, originalXml: value.originalXml || value.xml, diagramId: diagram?.id,
      editorMode: 'canvas' as const, canvasNotice: '', checks: { ...value.checks, import: 'passed' as const }, importWarnings: staged.warnings,
    };
    updateDocument(ready);
    setSelected(null);
    setElementName('');
    const candidate = staged.candidate;
    canvasEditableIds.current = new Set(candidate.get<ElementRegistry>('elementRegistry').getAll().map((element) => element.id));
    if (diagram?.id) canvasEditableIds.current.add(diagram.id);
    candidate.on('selection.changed', (event: { newSelection: Element[] }) => {
      if (modeler.current !== candidate) return;
      const item = event.newSelection.length === 1 ? event.newSelection[0] : null;
      setSelected(item);
      setElementName(item?.businessObject?.name || '');
    });
    candidate.on('canvas.viewbox.changed', (event: { viewbox: { scale: number } }) => {
      if (modeler.current === candidate) setZoomPercent(Math.round(event.viewbox.scale * 100));
    });
    candidate.on('commandStack.changed', () => {
      if (modeler.current !== candidate || !current.current) return;
      version.current += 1;
      const changedVersion = version.current;
      updateDocument({
        ...current.current, dirty: true, stale: true,
        checks: { ...current.current.checks, graph: 'not_run', behavior: 'not_run', xsd: 'not_run', layout: 'not_run', import: 'not_run' },
        semanticDiff: undefined,
        technicalIssues: [],
      });
      setStatus('Изменено вручную');
      setNotice('');
      // The XML tab follows editor commands, never an obsolete generation snapshot.
      void candidate.saveXML({ format: true }).then(({ xml }) => {
        if (xml && modeler.current === candidate && version.current === changedVersion && current.current) {
          ensureCanvasPreservation(xml);
          updateDocument({ ...current.current, xml });
        }
      }).catch((failure) => setError(readableError(failure)));
    });
    fitModeler(candidate);
  }

  function ensureCanvasPreservation(xml: string) {
    if (!current.current) return;
    for (const element of modeler.current?.get<ElementRegistry>('elementRegistry').getAll() || []) canvasEditableIds.current.add(element.id);
    const losses = roundtripLoss(current.current.xml, xml, canvasEditableIds.current);
    if (losses.length) {
      const message = `Последнее изменение затронуло скрытые данные (${losses.slice(0, 2).join('; ')}). Открыта последняя сохранённая версия документа; повторите правку в его структуре.`;
      // A rejected canvas serialization must not trap the user in that canvas:
      // retain the last lossless XML and make its full structure editable immediately.
      clearModeler(); version.current += 1;
      updateDocument({ ...current.current, editorMode: 'document', canvasNotice: message,
        checks: { ...current.current.checks, import: 'not_run' } });
      setDocumentFocus(''); setReadingMode(true);
      throw new Error(message);
    }
  }

  function editDocumentXml(xml: string, record = true) {
    if (!current.current || busyRef.current) return;
    parseBpmn(xml);
    if (record) { documentUndo.current.push(current.current.xml); documentRedo.current = []; }
    version.current += 1;
    updateDocument({ ...current.current, xml, dirty: true, stale: true, editorMode: 'document',
      checks: { ...current.current.checks, xsd: 'not_run', graph: 'not_run', behavior: 'not_run', layout: 'not_run', import: 'not_run' },
      technicalIssues: [], semanticDiff: undefined });
    setStatus('Документ изменён'); setNotice('');
  }

  function undoDocument(redo = false) {
    const stack = redo ? documentRedo.current : documentUndo.current;
    const xml = stack.pop();
    if (xml && current.current) { (redo ? documentUndo.current : documentRedo.current).push(current.current.xml); editDocumentXml(xml, false); }
  }

  function changeDocumentView(mode: 'canvas' | 'document', diagramId?: string) {
    if (!current.current) return;
    void operation('Открываем представление…', async () => {
      await captureCurrent();
      if (current.current) await openDocument(current.current, mode, diagramId);
    });
  }

  function arrangeDocument() {
    if (!current.current) return;
    void operation('Перестраиваем расположение…', async () => {
      const { xml } = await captureCurrent();
      const result = await api.layout(xml);
      if (!current.current) return;
      documentUndo.current.push(xml); documentRedo.current = [];
      const value = { ...current.current, xml: result.xml, dirty: true, checks: result.checks, technicalIssues: result.issues };
      await openDocument(value, value.editorMode || 'auto');
      setStatus('Расположение обновлено');
    });
  }

  async function captureCurrent() {
    if (!current.current) throw new Error('Сначала постройте или откройте схему.');
    if (current.current.editorMode === 'document') return { xml: current.current.xml, capturedVersion: version.current };
    if (!modeler.current) throw new Error('Графический редактор ещё не готов.');
    modeler.current.get<{ complete(): void }>('directEditing').complete();
    const capturedVersion = version.current;
    const { xml } = await modeler.current.saveXML({ format: true });
    if (!xml) throw new Error('Редактор не вернул XML схемы.');
    ensureCanvasPreservation(xml);
    if (version.current !== capturedVersion) throw new Error('Схема изменилась во время сохранения. Повторите проверку.');
    updateDocument({ ...current.current, xml });
    return { xml, capturedVersion };
  }

  async function validateCurrent(): Promise<{ xml: string; validation: Validation }> {
    const { xml, capturedVersion } = await captureCurrent();
    const validation = current.current?.generation
      ? await api.review(xml, current.current.generation.id) : await api.validate(xml);
    let checks: Checks = { ...current.current?.checks, ...validation.checks };
    const documentMode = current.current?.editorMode === 'document';
    if (technicalPassed(checks) && !documentMode) {
      // Prove that the exact serialization from saveXML can be reimported without
      // replacing the live editor or losing its undo stack/user DI.
      try {
        const probe = await stageImport(xml, current.current?.diagramId);
        probe.candidate.destroy();
        probe.container.remove();
        checks = { ...checks, import: 'passed' };
      } catch (failure) {
        checks = { ...checks, import: 'failed' };
        if (current.current) updateDocument({ ...current.current, checks, technicalIssues: validation.issues });
        throw failure;
      }
    }
    if (!current.current || version.current !== capturedVersion) {
      throw new Error('Схема изменилась во время проверки. Повторите проверку.');
    }
    updateDocument({ ...current.current, checks, technicalIssues: validation.issues,
      semanticReview: validation.semantic_review ?? current.current.semanticReview,
      semanticDiff: validation.semantic_diff,
      stale: validation.semantic_diff?.status === 'unchanged' ? false : current.current.stale });
    if (!(documentMode ? documentPassed(checks) : technicalPassed(checks))) throw new Error('Текущая схема не прошла технические проверки. Исправьте замечания.');
    return { xml, validation: { ...validation, checks } };
  }

  async function persistCurrent(xml: string, validation: Validation) {
    const value = current.current;
    if (!value) return;
    if (value.generation && value.dirty) {
      const revision = await api.revise(value.generation.id, value.generation.revision_id, xml);
      updateDocument({
        ...value, xml, dirty: false, stale: revision.audit_stale, checks: validation.checks,
        technicalIssues: validation.issues,
        semanticReview: revision.semantic_review, semanticDiff: revision.semantic_diff,
        generation: {
          ...value.generation, revision_id: revision.revision_id, audit_stale: revision.audit_stale,
          bpmn_xml: xml, checks: validation.checks, revision_issues: validation.issues,
          semantic_review: revision.semantic_review, semantic_diff: revision.semantic_diff,
        },
      });
    } else {
      updateDocument({ ...value, xml, dirty: false, checks: validation.checks, technicalIssues: validation.issues });
    }
  }

  async function preserveCurrent() {
    modeler.current?.get<{ complete(): void }>('directEditing').complete();
    if (!current.current?.dirty) return;
    setBusy('Сохраняем ручные правки…');
    const validated = await validateCurrent();
    await persistCurrent(validated.xml, validated.validation);
  }

  function fromGeneration(result: Generation, source: string): EditorDocument {
    return {
      key: result.id,
      title: examples.find((example) => example.text === source)?.title || source.slice(0, 65) || 'Схема процесса',
      source, generation: result, xml: result.bpmn_xml || '', checks: result.checks,
      technicalIssues: result.revision_issues || result.issues.filter((issue) => issue.category === 'technical'),
      importWarnings: [], dirty: false, stale: result.audit_stale,
      semanticReview: result.semantic_review, semanticDiff: result.semantic_diff,
    };
  }

  function generate() {
    startGeneration(false);
  }

  function startGeneration(continueAnswers: boolean) {
    const source = continueAnswers ? lastResult?.source_context?.description || description : description;
    const answers = continueAnswers ? [
      ...(lastResult?.source_context?.clarification_answers || []),
      ...(lastResult?.questions || []).map((question, index) => ({ ...question, answer: clarificationAnswers[index] || '' })),
    ] : [];
    void operation('Подготавливаем схему…', async () => {
      await preserveCurrent();
      setBusy('Генерируем схему');
      const requestId = crypto.randomUUID();
      let finished = false;
      const phases: Record<string, string> = { model: 'Генерируем схему', repair: 'Модель исправляет замечания',
        code: 'Проверяем Python', worker: 'Строим модель процесса', review: 'Проверяем смысл и ветвления', xml: 'Готовим BPMN' };
      const progressTimer = window.setInterval(() => {
        void api.progress(requestId).then((value) => {
          if (!finished && phases[value.phase]) setBusy(`${phases[value.phase]}${value.phase === 'repair' ? ` · попытка ${value.attempt}` : ''}`);
        }).catch(() => { /* Progress is optional; the generation response remains authoritative. */ });
      }, 2000);
      let result: Generation;
      try { result = await api.generate(source, answers, requestId); }
      finally { finished = true; window.clearInterval(progressTimer); }
      setBusy('Открываем схему…');
      setLastResult(result);
      setClarificationAnswers([]);
      if (result.status !== 'ready' || result.issues.length) setShowPanel(true);
      if (result.status === 'ready' && result.bpmn_xml) {
        try {
          await openDocument(fromGeneration(result, source));
        } catch (failure) {
          setLastResult({ ...result, checks: { ...result.checks, import: 'failed' }, status: 'error' });
          throw failure;
        }
      }
      setStatus(STATUS_TEXT[result.status]);
      if (result.status === 'error') setError(result.issues[0]?.message || 'Не удалось построить схему.');
    });
  }

  function verify() {
    setShowPanel(true);
    void operation('Проверяем текущую схему…', async () => {
      await validateCurrent();
      setStatus(current.current?.dirty ? 'Правки проверены' : 'Схема проверена');
      setNotice('');
    });
  }

  function exportCurrent() {
    void operation('Проверяем и экспортируем…', async () => {
      const validated = await validateCurrent();
      await persistCurrent(validated.xml, validated.validation);
      const filename = current.current?.generation
        ? `process-${current.current.generation.id.slice(0, 8)}.bpmn` : 'edited-process.bpmn';
      downloadXml(validated.xml, filename);
      setStatus('Экспорт выполнен');
      setNotice('');
    });
  }

  function saveRevision() {
    void operation('Сохраняем ревизию…', async () => {
      const validated = await validateCurrent();
      await persistCurrent(validated.xml, validated.validation);
      setStatus('Ревизия сохранена');
      setNotice('');
    });
  }

  function openHistory(key: string) {
    if (!key || key === current.current?.key) return;
    const entry = history.find((item) => item.key === key);
    const saved = savedHistory.find((item) => item.id === key);
    if (!entry && !saved) return;
    void operation('Открываем сохранённую схему…', async () => {
      await preserveCurrent();
      const generation = entry?.generation || saved ? await api.generation(key) : null;
      const source = entry?.source || generation?.ir?.source_text || '';
      const value = generation ? fromGeneration(generation, source) : entry;
      if (!value) return;
      await openDocument(value);
      setDescription(source);
      setExampleId(examples.find((example) => example.text === source)?.id || '');
      setLastResult(generation);
      setStatus('Схема открыта');
    });
  }

  function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    void operation('Открываем BPMN-файл…', async () => {
      if (file.size > MAX_XML_BYTES) throw new Error('BPMN-файл превышает 2 МБ.');
      const xml = await file.text();
      const validation = await api.validate(xml);
      if (!documentPassed(validation.checks)) {
        setNotice(validation.issues.map((issue) => issue.message).join(' '));
        throw new Error('Файл не прошёл проверки BPMN, структуры или DI.');
      }
      await preserveCurrent();
      await openDocument({
        key: `file-${crypto.randomUUID()}`, title: file.name, source: '', generation: null, xml,
        checks: validation.checks, technicalIssues: validation.issues, importWarnings: [], dirty: false, stale: true,
      });
      setLastResult(null);
      setStatus('BPMN-файл открыт');
      setNotice('');
    });
  }

  function renameSelected() {
    if (!selected || !modeler.current || busyRef.current) return;
    modeler.current.get<Modeling>('modeling').updateLabel(selected, elementName);
  }

  function focusElement(id: string) {
    if (current.current?.editorMode === 'document') { setDocumentFocus(id); return; }
    const value = modeler.current?.get<ElementRegistry>('elementRegistry').get(id);
    if (!value || !modeler.current) {
      setNotice('Этот элемент отсутствует в текущей схеме.');
      return;
    }
    modeler.current.get<Selection>('selection').select(value);
    const canvas = modeler.current.get<Canvas>('canvas');
    if (canvas.zoom() < 1) canvas.zoom(1);
    canvas.scrollToElement(value, { left: 140, right: 50, top: 75, bottom: 85 });
  }

  function zoom(direction: number) {
    const canvas = modeler.current?.get<Canvas>('canvas');
    if (canvas) canvas.zoom(Math.min(3, Math.max(0.15, canvas.zoom() * direction)));
  }

  function requestLogout() {
    if (busyRef.current) return;
    if (current.current?.dirty) {
      modal.confirm({
        title: 'Выйти без сохранения?',
        content: 'В схеме есть несохранённые изменения.',
        okText: 'Выйти', cancelText: 'Остаться', okButtonProps: { danger: true },
        onOk: logout,
      });
    } else {
      void logout().catch((failure: unknown) => setError(readableError(failure)));
    }
  }

  const characterCount = Array.from(description).length;
  const historyEntries = [
    ...history.map((entry) => ({ key: entry.key, title: entry.title, isFile: !entry.generation })),
    ...savedHistory.filter((entry) => entry.status === 'ready' && !history.some((item) => item.key === entry.id))
      .map((entry) => ({ key: entry.id, title: entry.title, isFile: false })),
  ];
  const canGenerate = Boolean(description.trim()) && characterCount <= 20000 && !busy
    && Boolean(health?.generation_available);
  const documentMode = documentState?.editorMode === 'document';
  const canExport = Boolean(documentState) && !busy && (documentMode ? documentPassed(documentState?.checks || {})
    : technicalPassed(documentState?.checks || {}) && documentState?.checks.import === 'passed');
  const diagrams = documentState ? documentDiagrams(parseBpmn(documentState.xml)) : [];
  const generation = documentState?.generation;
  const artifactGeneration = generation || (!documentState ? lastResult : null);
  const visibleChecks = documentState?.checks || lastResult?.checks || {};
  const displayMode = documentState ? generation?.mode || 'import' : health?.mode || 'unknown';
  const failedAttempt = lastResult && lastResult.id !== generation?.id ? lastResult : null;
  const modelIssues = uniqueIssues([
    ...(generation?.issues.filter((issue) => issue.category !== 'technical') || []),
    ...(documentState?.technicalIssues.filter((issue) => issue.category !== 'technical') || []),
  ]);
  const technicalIssues = uniqueIssues(documentState?.technicalIssues.filter(
    (issue) => issue.category === 'technical',
  ) || []);
  const selectedProvenance = generation?.ir?.provenance?.find((entry) => entry.element_id === selected?.id);
  const selectedDetails = useMemo(() => {
    if (!documentState || !selected) return null;
    const doc = parseBpmn(documentState.xml);
    return activityDetails(doc, elementById(doc, selected.id), bpmnSchema);
  }, [documentState, selected, bpmnSchema]);
  const nameOf = (id: string) => modeler.current?.get<ElementRegistry>('elementRegistry').get(id)?.businessObject?.name
    || (documentState && elementById(parseBpmn(documentState.xml), id)?.getAttribute('name')) || id;
  const outline = modeler.current?.get<ElementRegistry>('elementRegistry').getAll()
    .filter((element) => !element.labelTarget && (element.type === 'bpmn:Lane' || /Task$/.test(element.type)))
    .map((element) => ({ value: element.id, label: `${element.type === 'bpmn:Lane' ? 'Роль · ' : ''}${element.businessObject?.name || element.id}` })) || [];
  const issueCount = technicalIssues.length + modelIssues.length + (lastResult?.questions.length || 0);
  const connectionReason = healthError || (health && !health.generation_available
    ? !health.dependencies.worker ? 'Не подключён worker. Можно открывать и редактировать BPMN-файлы.'
      : !health.dependencies.xsd_files_available ? 'Отсутствуют схемы проверки BPMN.'
        : 'Для LIVE не настроено подключение к модели.' : '');
  const codeText = artifactGeneration?.python_code || (documentState
    ? 'Для открытого файла исходный Python отсутствует.' : 'Python-код ещё не получен.');
  const techText = techTab === 'python' ? codeText : techTab === 'ir'
    ? (artifactGeneration?.ir ? JSON.stringify(artifactGeneration.ir, null, 2)
      : documentState ? 'IR не восстанавливается из открытого файла.' : 'Внутренняя модель ещё не построена.')
    : techTab === 'xml' ? documentState?.xml || 'Схема ещё не открыта.'
      : JSON.stringify({ checks: visibleChecks, issues: documentState?.technicalIssues || lastResult?.issues || [],
        importWarnings: documentState?.importWarnings || [], timings: artifactGeneration?.timings,
        attempt_count: artifactGeneration?.attempt_count }, null, 2);

  const renderIssue = (issue: Issue, index: number) => <li key={`${issue.code}-${index}`} className={`issue ${issue.severity}`}>
    <p>{issue.message}</p>
    {issue.source_quote && <blockquote>{issue.source_quote}</blockquote>}
    {issue.suggested_action && <p className="muted">{issue.suggested_action}</p>}
    <div className="issue-actions">{issue.element_ids.map((id) => <Button
      key={id} type="link" size="small" disabled={Boolean(busy)} onClick={() => focusElement(id)}
    >К элементу {id}</Button>)}</div>
    <details className="issue-details"><summary>Код замечания</summary><code>{issue.code}</code></details>
  </li>;

  return <main className="application" onKeyDown={(event) => {
    if (event.key === 'Escape') {
      if (showPanel) closeInspector();
      setShowConnection(false);
    }
  }}>
    {modalContext}
    <header className="topbar">
      <div className="brand" aria-label="Process Architect AI">
        <span className="brand-symbol"><Icon name="flow" size={22} /></span>
        <span className="brand-name">Process Architect<span className="brand-ai">AI</span></span>
      </div>
      <div className="document-heading">
        <span className="document-divider" />
        <h1 title={documentState?.title}>{documentState?.title || 'Новый процесс'}</h1>
        {documentState?.dirty && <Tooltip title="Есть несохранённые правки"><span className="unsaved-dot" aria-label="Есть несохранённые правки" /></Tooltip>}
      </div>
      <div className="header-actions">
        {documentState && <Tag data-testid="mode" className={`mode-badge ${displayMode}`}>
          {displayMode === 'mock' ? 'MOCK' : displayMode === 'live' ? 'LIVE' : 'BPMN-файл · без генерации'}
        </Tag>}
        {!documentState && <span data-testid="mode" className="sr-only">{displayMode.toUpperCase()}</span>}
        <input id="bpmn-upload" className="sr-only" type="file" accept=".bpmn,.xml,application/xml,text/xml"
          aria-label="Открыть BPMN-файл" data-testid="import-file" tabIndex={-1} disabled={Boolean(busy)} onChange={importFile} />
        <Tooltip title="Открыть BPMN-файл">
          <Button className="header-open" icon={<Icon name="upload" size={16} />} disabled={Boolean(busy)}
            loading={busy === 'Открываем BPMN-файл…'} aria-label="Открыть BPMN-файл" onClick={() => document.getElementById('bpmn-upload')?.click()}>
            <span className="action-label">Открыть</span>
          </Button>
        </Tooltip>
        <Tooltip title={documentState && !canExport ? 'Проверьте схему перед экспортом' : 'Скачать текущую схему'}>
          <Button type="primary" className="export-button" data-testid="export" disabled={!canExport} onClick={exportCurrent}
            loading={busy === 'Проверяем и экспортируем…'} icon={<Icon name="download" size={16} />} aria-label="Экспорт BPMN">
            <span className="action-label">Экспорт BPMN</span>
          </Button>
        </Tooltip>
        <span className="account-divider" />
        <Dropdown trigger={['click']} placement="bottomRight" menu={{ items: [
          { key: 'account', label: <span className="account-menu-name">{user?.username}</span>, disabled: true },
          { type: 'divider' },
          { key: 'logout', label: <span data-testid="logout">Выйти</span>, icon: <LogoutOutlined aria-hidden="true" />, disabled: Boolean(busy), onClick: requestLogout },
        ] }}>
          <Button type="text" className="account-button" disabled={Boolean(busy)} aria-label={`Аккаунт ${user?.username || ''}`} data-testid="account-menu">
            <Avatar size={28}>{user?.username.slice(0, 1).toUpperCase()}</Avatar>
            <span className="account-name">{user?.username}</span><Icon name="chevron-down" size={12} />
          </Button>
        </Dropdown>
      </div>
    </header>

    <div className={`workspace ${showPanel ? 'panel-visible' : ''} ${readingMode ? 'reading-mode' : ''}`}>
      <aside className="source-panel" aria-label="Описание и примеры">
        <div className="source-content">
          <div className="panel-heading"><h2>Описание процесса</h2></div>
          <label htmlFor="example" className="sr-only">Синтетический пример</label>
          <Select id="example" className="example-picker" data-testid="example-select" value={exampleId || undefined}
            placeholder="Взять пример" aria-label="Синтетический пример" disabled={Boolean(busy)}
            suffixIcon={<Icon name="chevron-down" size={14} />}
            options={examples.map((example) => ({ value: example.id, label: `${example.id} · ${example.title}`, title: example.id }))}
            onChange={(value: string) => {
              setExampleId(value);
              const example = examples.find((item) => item.id === value);
              if (example) { setDescription(example.text); setError(''); }
            }} />
          <div className={`composer ${characterCount > 20000 ? 'invalid' : ''}`}>
            <label htmlFor="description" className="sr-only">Описание процесса</label>
            <Input.TextArea id="description" data-testid="description" value={description} disabled={Boolean(busy)}
              status={characterCount > 20000 ? 'error' : undefined}
              placeholder="Менеджер получает заявку и проверяет её. Если всё в порядке — передаёт на согласование…"
              onChange={(event) => { setDescription(event.target.value); setExampleId(''); }}
              onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && canGenerate) generate(); }} />
            <div className="composer-footer">
              <span className={`character-count ${characterCount > 20000 ? 'over-limit' : ''}`}>{characterCount.toLocaleString('ru-RU')} / 20 000</span>
            </div>
          </div>
          {characterCount > 20000 && <p className="field-error">Превышен лимит 20 000 символов.</p>}
          <Tooltip title="Ctrl + Enter">
            <Button type="primary" size="large" className="build-button" data-testid="generate" disabled={!canGenerate} onClick={generate}
              loading={busy === 'Генерируем схему' || busy === 'Подготавливаем схему…'} icon={<Icon name="spark" size={18} />}>
              {busy === 'Генерируем схему' ? 'Генерируем схему' : 'Построить схему'}
            </Button>
          </Tooltip>
          <div className="request-mode" data-testid="request-mode">
            <span className={`mode-dot ${health?.mode || ''}`} />
            {health?.mode === 'live' ? 'LIVE · генерация моделью' : health?.mode === 'mock' ? 'MOCK · готовые примеры' : 'Подключение…'}
          </div>
        </div>
        <div className="source-bottom">
          {historyEntries.length > 0 && <div className="history-section">
            <label htmlFor="history"><Icon name="clock" size={15} /> История<span>{historyEntries.length}</span></label>
            <Select id="history" className="history-picker" data-testid="history-select" value={documentState?.key || undefined}
              aria-label="История схем" placeholder="Выберите схему" disabled={Boolean(busy)} onChange={openHistory}
              options={historyEntries.map((entry) => ({ value: entry.key, title: entry.key,
                label: <span data-testid={`history-option-${entry.key}`}>{entry.isFile ? 'Файл · ' : ''}{entry.title}</span>,
              }))} />
          </div>}
          <div className="connection-section">
            <Popover open={showConnection} onOpenChange={setShowConnection} trigger="click" placement="topLeft" title="Подключение"
              content={<div className="connection-details">{connectionReason || 'API и изолированный worker доступны.'}
                {health?.mode === 'mock' && <p>В MOCK доступны точные тексты примеров. Для своего описания подключите LIVE.</p>}
              </div>}>
              <Button type="text" className="connection-status" aria-expanded={showConnection}>
                <span className={`connection-dot ${connectionReason ? 'unavailable' : ''}`} />
                <span>{connectionReason ? 'Генерация недоступна' : health ? 'Подключено' : 'Подключение…'}</span><Icon name="chevron-down" size={12} />
              </Button>
            </Popover>
            <Tooltip title="Проверить подключение"><Button type="text" size="small" icon={<Icon name="refresh" size={15} />}
              aria-label="Проверить подключение" data-testid="connection-refresh" disabled={Boolean(busy)} loading={refreshingHealth}
              onClick={() => void refreshHealth()} /></Tooltip>
          </div>
        </div>
      </aside>

      <section className="editor-panel" aria-label="Рабочая схема">
        <div className="editor-toolbar">
          <div className="editor-title"><Icon name="flow" size={17} /><span>Схема процесса</span><Tag className="format-tag">BPMN 2.0</Tag></div>
          <div className="editor-actions">
            <Button type={readingMode ? 'default' : 'text'} data-testid="reading-mode"
              aria-pressed={readingMode} onClick={() => setReadingMode((value) => !value)}>
              {readingMode ? 'Показать описание' : 'Режим чтения'}
            </Button>
            <Button type="text" className="validate-button" data-testid="validate" disabled={!documentState || Boolean(busy)} onClick={verify}
              loading={busy === 'Проверяем текущую схему…'} icon={<Icon name="check-circle" size={16} />}>Проверить</Button>
            <Badge count={issueCount} size="small" offset={[-5, 4]}>
              <Button type={showPanel ? 'default' : 'text'} className={`panel-toggle ${showPanel ? 'active' : ''}`} data-testid="inspector-toggle"
                ref={inspectorToggle} aria-controls="review-panel" aria-label="Проверки и замечания"
                aria-expanded={showPanel} onClick={() => showPanel ? closeInspector() : setShowPanel(true)}
                icon={<Icon name="panel" size={17} />}>Проверки</Button>
            </Badge>
          </div>
        </div>
        {documentState && <div className="diagram-navigation">
          <Select className="document-view-select" value={documentMode ? 'document' : 'canvas'} disabled={Boolean(busy)}
            aria-label="Режим редактора" data-testid="document-view-select" onChange={(mode) => changeDocumentView(mode as 'canvas' | 'document')}
            options={[{ value: 'canvas', label: 'Графический редактор', disabled: !diagrams.find((item) => item.id === documentState.diagramId)?.canvas },
              { value: 'document', label: 'Документ BPMN' }]} />
          {diagrams.length > 1 && <Select className="document-diagram-select" value={documentState.diagramId} disabled={Boolean(busy)}
            aria-label="Диаграмма документа" data-testid="document-diagram-select"
            options={diagrams.map((item) => ({ value: item.id, label: `${item.kind} · ${item.name}` }))}
            onChange={(id) => changeDocumentView(diagrams.find((item) => item.id === id)?.canvas && !documentMode ? 'canvas' : 'document', id)} />}
          {!documentMode && <Select className="diagram-outline" data-testid="outline-select" showSearch
            optionFilterProp="label" aria-label="Перейти к роли или действию" placeholder="Найти роль или действие"
            options={outline} value={selected && outline.some((item) => item.value === selected.id) ? selected.id : undefined}
            onChange={focusElement} disabled={Boolean(busy)} />}
          {documentMode && <Space.Compact>
            <Button size="small" disabled={Boolean(busy) || !documentUndo.current.length} aria-label="Отменить изменение документа" onClick={() => undoDocument()} icon={<Icon name="undo" size={14} />} />
            <Button size="small" disabled={Boolean(busy) || !documentRedo.current.length} aria-label="Повторить изменение документа" onClick={() => undoDocument(true)} icon={<Icon name="redo" size={14} />} />
          </Space.Compact>}
        </div>}
        <div className="canvas-wrap" aria-busy={Boolean(busy)}>
          <div ref={host} className="modeler-host" data-testid="modeler" role="region" aria-label="Редактор BPMN" inert={Boolean(busy) || documentMode} style={{ visibility: documentMode ? 'hidden' : undefined }} />
          {documentMode && documentState && <BpmnDocumentEditor xml={documentState.xml} schema={bpmnSchema}
            diagramId={documentState.diagramId || ''} focusId={documentFocus} disabled={Boolean(busy)} reason={documentState.canvasNotice}
            onChange={editDocumentXml} onLayout={arrangeDocument} />}
          {!documentState && !busy && <div className="empty-canvas">
            <div className="empty-illustration" aria-hidden="true">
              <svg viewBox="0 0 350 132" fill="none">
                <path d="M42 67H95M192 67H228M272 67H310" stroke="currentColor" strokeWidth="1.5" />
                <path d="m87 63 5 4-5 4m133-4 5 4-5 4m82-4 5 4-5 4" stroke="currentColor" strokeWidth="1.5" />
                <circle cx="31" cy="67" r="12" fill="var(--surface)" stroke="currentColor" strokeWidth="1.5" />
                <rect x="96" y="38" width="96" height="58" rx="10" fill="var(--surface)" stroke="currentColor" strokeWidth="1.5" />
                <path d="M118 60h52M118 69h37M118 78h44" stroke="var(--illustration-line)" strokeWidth="3" strokeLinecap="round" />
                <rect x="250" y="45" width="31" height="31" rx="3" transform="rotate(45 250 45)" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.5" />
                <path d="M246 63l8 8m0-8-8 8" stroke="var(--accent)" strokeWidth="1.5" />
                <circle cx="322" cy="67" r="12" fill="var(--surface)" stroke="currentColor" strokeWidth="2.5" />
              </svg>
            </div>
            <h2>От описания — к схеме</h2>
            <p>Введите текст слева или откройте готовый BPMN.</p>
            <Button className="empty-import" icon={<Icon name="upload" size={16} />}
              onClick={() => document.getElementById('bpmn-upload')?.click()}>Открыть файл</Button>
          </div>}
          {documentState && !documentMode && <div className="canvas-controls" aria-label="Управление холстом">
            <div className="control-group">
              <Tooltip title="Отменить"><Button type="text" className="icon-button" aria-label="Отменить изменение" disabled={Boolean(busy)}
                icon={<Icon name="undo" />} onClick={() => modeler.current?.get<CommandStack>('commandStack').undo()} /></Tooltip>
              <Tooltip title="Повторить"><Button type="text" className="icon-button" aria-label="Повторить изменение" disabled={Boolean(busy)}
                icon={<Icon name="redo" />} onClick={() => modeler.current?.get<CommandStack>('commandStack').redo()} /></Tooltip>
            </div>
            <span className="control-divider" />
            <div className="control-group">
              <Tooltip title="Уменьшить"><Button type="text" className="icon-button" aria-label="Уменьшить масштаб" disabled={Boolean(busy)} onClick={() => zoom(0.8)} icon={<Icon name="minus" size={16} />} /></Tooltip>
              <Tooltip title="Сбросить до 100%"><Button type="text" className="zoom-value" aria-label={`Масштаб ${zoomPercent}%. Сбросить до 100%`} disabled={Boolean(busy)} onClick={() => modeler.current?.get<Canvas>('canvas').zoom(1)}>{zoomPercent}%</Button></Tooltip>
              <Tooltip title="Увеличить"><Button type="text" className="icon-button" aria-label="Увеличить масштаб" disabled={Boolean(busy)} onClick={() => zoom(1.25)} icon={<Icon name="plus" size={16} />} /></Tooltip>
              <Tooltip title="Вписать"><Button type="text" className="icon-button" aria-label="Вписать" disabled={Boolean(busy)} onClick={() => fitModeler(modeler.current)} icon={<Icon name="fit" size={17} />} /></Tooltip>
            </div>
          </div>}
          {selected && !documentMode && <form className="selection-bar" onSubmit={(event) => { event.preventDefault(); renameSelected(); }}>
            <label htmlFor="element-name" className="sr-only">Название элемента</label>
            <Input id="element-name" data-testid="element-name" value={elementName} maxLength={1000} prefix={<Icon name="file" size={16} />}
              aria-label="Название элемента" placeholder="Название элемента" disabled={Boolean(busy)} onChange={(event) => setElementName(event.target.value)} />
            <Tooltip title="Применить имя"><Button type="primary" htmlType="submit" className="apply-name" data-testid="rename" aria-label="Применить имя" disabled={Boolean(busy)} icon={<Icon name="check" size={17} />} /></Tooltip>
            <ActivitySummary details={selectedDetails} />
          </form>}
          {(error || notice) && <Alert className="workspace-message" type={error ? 'error' : 'info'} showIcon closable
            role={error ? 'alert' : 'status'} data-testid={error ? 'error' : 'notice'} title={error || notice}
            onClose={() => { setError(''); setNotice(''); }} />}
          {busy && <GenerationActivity label={busy} />}
        </div>
        <div className="editor-bottom">
          <div className="statusbar">{busy ? <Spin size="small" /> : <span className={`status-dot ${error ? 'error' : ''}`} />}
            <span role="status" aria-live="polite" data-testid="status">{busy || status}</span>
          </div>
          {generation && documentState?.dirty && <Button type="link" size="small" disabled={Boolean(busy)} loading={busy === 'Сохраняем ревизию…'} onClick={saveRevision}>Сохранить версию</Button>}
        </div>
        <details className="technical-panel">
          <summary><Icon name="code" size={16} /><span>Код и XML</span><Icon name="chevron-down" size={14} /></summary>
          <Tabs className="technical-tabs" size="small" activeKey={techTab} onChange={(key) => setTechTab(key as typeof techTab)}
            items={([['python', 'Python'], ['ir', 'IR'], ['xml', 'Текущий XML'], ['checks', 'Журнал проверок']] as const).map(([key, label]) => ({ key, label }))} />
          {documentState?.stale && (techTab === 'python' || techTab === 'ir') && <p className="technical-note">Исходная генерация. Ручные правки сохранены в XML.</p>}
          <pre role="tabpanel" data-testid={`technical-${techTab}`}>{techText}</pre>
        </details>
      </section>

      {showPanel && <aside id="review-panel" className="review-panel" aria-label="Проверки и замечания">
        <div className="review-heading"><h2>Проверки</h2><Button type="text" className="icon-button" aria-label="Закрыть проверки" onClick={closeInspector} icon={<Icon name="close" size={18} />} /></div>
        <div className="review-content">
          {lastResult?.questions.length ? <section className="review-section questions" data-testid="questions">
            <h3>Нужно уточнить</h3><ol>{lastResult.questions.map((question, index) => <li key={index}>
              <p>{question.question}</p>{question.source_quote && <blockquote>{question.source_quote}</blockquote>}
              <Input.TextArea data-testid={`clarification-answer-${index}`} aria-label={`Ответ на вопрос ${index + 1}`}
                autoSize={{ minRows: 2, maxRows: 5 }} maxLength={4000} placeholder="Ваш ответ"
                value={clarificationAnswers[index] || ''} disabled={Boolean(busy)} onChange={(event) => {
                  setClarificationAnswers((items) => lastResult.questions.map((_, item) => item === index ? event.target.value : items[item] || ''));
                }} />
            </li>)}</ol>
            <Button type="primary" block data-testid="clarification-continue" onClick={() => startGeneration(true)}
              disabled={Boolean(busy) || !lastResult.questions.every((_, index) => clarificationAnswers[index]?.trim())}>
              Продолжить построение
            </Button>
          </section> : null}
          {failedAttempt && failedAttempt.status !== 'needs_clarification' && <section className="review-section latest-attempt" data-testid="last-attempt">
            <h3>{STATUS_TEXT[failedAttempt.status]}</h3>
            {failedAttempt.checks.import === 'failed' && <p>Не удалось открыть схему.</p>}
            <ul className="issues">{failedAttempt.issues.map(renderIssue)}</ul>
          </section>}
          <section className="review-section"><h3>Технические проверки</h3>
            <ul className="check-list">{(Object.keys(CHECK_LABELS) as CheckName[]).map((key) => {
              const state = visibleChecks[key] || 'not_run';
              return <li key={key}><span>{CHECK_LABELS[key]}</span><span data-testid={`check-${key}`} className={`check-badge ${state}`}>
                {state === 'passed' && <Icon name="check" size={13} />}{CHECK_TEXT[state]}
              </span></li>;
            })}</ul>
            {technicalIssues.length > 0 && <ul className="issues">{technicalIssues.map(renderIssue)}</ul>}
            {documentState?.importWarnings.map((warning, index) => <p className="import-warning" key={index}>{warning}</p>)}
          </section>
          {(generation || modelIssues.length > 0) && <section className="review-section"><h3>Замечания</h3>
            {documentState?.stale && <p className="stale-note" data-testid="audit-stale">Аудит устарел после ручных правок.</p>}
            {modelIssues.length ? <ul className="issues">{modelIssues.map(renderIssue)}</ul>
              : <p className="review-empty">Автоматические правила не нашли замечаний.</p>}
            {generation?.ir?.assumptions?.map((assumption, index) => <p className="stale-note" key={index}>Допущение: {assumption}</p>)}
          </section>}
          <QualityReview review={documentState?.semanticReview} diff={documentState?.semanticDiff}
            stale={Boolean(documentState?.stale)} focus={focusElement} nameOf={nameOf} />
          {selected && <section className="review-section source-link"><h3>Источник элемента</h3>
            {selectedProvenance?.exact_quote ? <>{documentState?.stale && <p className="field-note">Исходная цитата; связь устарела</p>}
              <blockquote>{selectedProvenance.exact_quote}</blockquote></> : <p className="field-note">{selectedProvenance?.origin === 'structural' ? 'Структурный элемент BPMN.' : 'Цитата не указана.'}</p>}
          </section>}
          {generation && <details className="generation-info"><summary>О генерации <Icon name="chevron-down" size={14} /></summary>
            <p>{generation.mode === 'mock' ? 'Ручной синтетический пример' : 'Генерация моделью'}</p>
            <p>Попыток: {generation.attempt_count}{generation.timings.total_seconds !== undefined ? ` · ${generation.timings.total_seconds.toFixed(2)} с` : ''}</p>
            <p>Ревизия <code>{generation.revision_id.slice(0, 12)}</code></p>
          </details>}
        </div>
      </aside>}
    </div>
  </main>;
}
