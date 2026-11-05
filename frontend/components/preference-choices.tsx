type Props<T extends string> = {
  name: string;
  legend: string;
  options: readonly (readonly [T, string])[];
  value: T | null;
  onChange: (value: T | null) => void;
  error?: string;
};

export function PreferenceChoices<T extends string>({
  name,
  legend,
  options,
  value,
  onChange,
  error,
}: Props<T>) {
  return (
    <fieldset
      className="preference-group"
      aria-describedby={error ? `${name}-error` : undefined}
    >
      <legend>{legend}</legend>
      <div className="choice-grid">
        {options.map(([key, label]) => (
          <label className="choice" key={key}>
            <input
              type="radio"
              name={name}
              value={key}
              checked={value === key}
              onChange={() => onChange(key)}
            />
            <span>{label}</span>
          </label>
        ))}
        <label className="choice">
          <input
            type="radio"
            name={name}
            value=""
            checked={value === null}
            onChange={() => onChange(null)}
          />
          <span>No preference</span>
        </label>
      </div>
      {error && (
        <p className="field-error" id={`${name}-error`}>
          {error}
        </p>
      )}
    </fieldset>
  );
}
