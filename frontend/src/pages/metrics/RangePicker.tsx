import { RANGES } from '@/pages/metrics/range';

export function RangePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}): React.JSX.Element {
  return (
    <label className="text-sm">
      <span className="mr-2" style={{ color: 'var(--c-text-muted)' }}>
        Time range
      </span>
      <select
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        className="rounded border px-2 py-1"
        style={{ borderColor: 'var(--c-border)', background: 'var(--c-surface)' }}
      >
        {RANGES.map((choice) => (
          <option key={choice.id} value={choice.id}>
            {choice.label}
          </option>
        ))}
      </select>
    </label>
  );
}
