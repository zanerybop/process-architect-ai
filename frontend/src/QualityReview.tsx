import { Button, Collapse, Tag } from 'antd';
import type { SemanticDiff, SemanticReview } from './types';

const FIELDS: Record<string, string> = {
  name: 'Название', type: 'Тип элемента', lane_id: 'Исполнитель', process_id: 'Процесс',
  source: 'Начало связи', target: 'Конец связи', condition: 'Условие', is_default: 'Ветка по умолчанию',
};

export function QualityReview({ review, diff, stale, focus, nameOf }: {
  review?: SemanticReview | null; diff?: SemanticDiff; stale: boolean;
  focus: (id: string) => void; nameOf: (id: string) => string;
}) {
  return <>
    {review && <section className="review-section source-review" data-testid="source-review">
      <h3>Связь с описанием</h3>
      <p className="field-note">{review.linked_fragments} из {review.total_fragments} фрагментов связаны с элементами.
        {stale ? ' После правки проверьте связь заново.' : ' Смысл подтверждает аналитик.'}</p>
      <Collapse ghost size="small" items={review.coverage.map((row) => ({
        key: row.id,
        label: <span className="source-fragment-label" data-testid="source-coverage-row">
          <Tag variant="filled">{row.status === 'linked' ? 'Есть связь' : 'Проверить'}</Tag>{row.text}
        </span>,
        children: <div className="source-fragment-actions">
          {row.element_ids.length ? row.element_ids.map((id) => <Button key={id} size="small" onClick={() => focus(id)}>
            {nameOf(id)}
          </Button>) : <p className="field-note">Для этого фрагмента не указаны элементы. Проверьте, нужен ли он на схеме.</p>}
        </div>,
      }))} />
    </section>}
    {diff && diff.status !== 'unavailable' && <section className="review-section" data-testid="semantic-diff">
      <h3>Изменения относительно генерации</h3>
      {diff.status === 'unchanged' ? <p className="field-note">Действия, исполнители и связи сохранены. Геометрия может отличаться.</p>
        : <ul className="semantic-changes">{diff.changes.map((change) => <li key={change.element_id}>
          <strong>{change.kind === 'added' ? 'Добавлено' : change.kind === 'removed' ? 'Удалено' : 'Изменено'}: {change.name}</strong>
          {change.kind === 'changed' && change.fields.map((field) => <p key={field}>
            {FIELDS[field] || field}: {String(change.before?.[field] ?? '—')} → {String(change.after?.[field] ?? '—')}
          </p>)}
          {change.kind !== 'removed' && <Button type="link" size="small" onClick={() => focus(change.element_id)}>Показать</Button>}
        </li>)}</ul>}
    </section>}
  </>;
}
