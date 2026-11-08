import { useEffect, useState } from 'react';
import { LoadingOutlined } from '@ant-design/icons';
import { Spin } from 'antd';

export function GenerationActivity({ label }: { label: string }) {
  const [seconds, setSeconds] = useState(0);
  const generating = label === 'Генерируем схему';

  useEffect(() => {
    const started = performance.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((performance.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  return <div className="canvas-busy" data-testid="generation-progress" aria-busy="true">
    <div className="activity-card">
      <div className="activity-visual" aria-hidden="true">
        <span className="activity-node start" />
        <span className="activity-link" />
        <span className="activity-node task"><i /><i /></span>
        <span className="activity-link second" />
        <span className="activity-node gateway" />
      </div>
      <div className="activity-title">
        <Spin indicator={<LoadingOutlined spin aria-hidden="true" />} size="small" />
        <span role="status" aria-live="polite">{label}</span>
      </div>
      <p className="activity-detail" aria-live="polite">
        {generating && seconds >= 20 ? 'Модель ещё обрабатывает описание' : 'Операция выполняется'}
      </p>
      <span className="activity-elapsed" data-testid="generation-elapsed" aria-label="Прошло времени">
        {seconds} с
      </span>
    </div>
  </div>;
}
