import type { ActivityDetails } from './activityDetails';

export function ActivitySummary({ details }: { details: ActivityDetails | null }) {
  if (!details || !Object.values(details).some(values => values.length)) return null;
  return <dl className="activity-summary" data-testid="activity-summary">
    {([['performers', 'Исполнитель', details.performers], ['candidates', 'Возможный исполнитель', details.candidates],
      ['resources', 'Ресурс', details.resources], ['inputs', 'Вход', details.inputs], ['outputs', 'Выход', details.outputs],
      ['messages', 'Сообщение', details.messages], ['signals', 'Сигнал', details.signals], ['errors', 'Ошибка', details.errors],
      ['escalations', 'Эскалация', details.escalations], ['timers', 'Таймер', details.timers]] as const)
      .filter(([, , values]) => values.length).map(([key, label, values]) => <div key={key} data-testid={`activity-${key}`}>
        <dt>{label}:</dt><dd>{values.join(', ')}</dd>
      </div>)}
  </dl>;
}
