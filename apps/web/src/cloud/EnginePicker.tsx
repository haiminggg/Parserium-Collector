import { useId } from "react";
import type { ParseEngine } from "./api";

export const engineLabel = (engines: ParseEngine[] | undefined, id: string | null | undefined) =>
  id ? engines?.find(engine => engine.id === id)?.label || id : null;

interface EnginePickerProps {
  engines: ParseEngine[];
  value: string;
  disabled?: boolean;
  onChange: (id: string) => void;
}

// Parser engines come from the server, so adding one never needs a web release.
export function EnginePicker({ engines, value, disabled, onChange }: EnginePickerProps) {
  const id = useId();
  const chosen = engines.find(engine => engine.id === value) || engines[0];
  if (!chosen) return null;
  return <div className="cloud-engine-picker">
    <label htmlFor={id}>Parser</label>
    <select id={id} value={chosen.id} disabled={disabled} onChange={event => onChange(event.currentTarget.value)}>
      {engines.map(engine => <option key={engine.id} value={engine.id}>{engine.label}</option>)}
    </select>
    <p className="cloud-engine-description" aria-live="polite">{chosen.description}</p>
    <p className="cloud-engine-facts"><span>{chosen.ocr ? "Reads scanned pages (OCR)" : "No OCR"}</span><span>{chosen.license}</span></p>
  </div>;
}
