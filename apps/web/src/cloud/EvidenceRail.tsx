import { CloudOperationalIcon } from "./CloudPrimitives";

export type EvidenceDestination = "collect" | "documents" | "activity";

const stages = [
  { label: "Search", icon: "search" as const },
  { label: "Select", icon: "queue" as const },
  { label: "Parse", icon: "settings" as const },
  { label: "Read", icon: "file" as const },
];

const activeStages: Record<EvidenceDestination, readonly string[]> = {
  collect: ["Search", "Select"],
  documents: ["Parse", "Read"],
  activity: ["Search", "Select", "Parse", "Read"],
};

export function EvidenceRail({ active }: { active: EvidenceDestination }) {
  const activeLabels = activeStages[active];

  return <nav className="cloud-evidence-rail" aria-label="Research workflow">
    <ol>
      {stages.map((stage, index) => {
        const isActive = activeLabels.includes(stage.label);
        return <li key={stage.label} data-active={isActive || undefined}>
          <span className="cloud-evidence-rail-marker" aria-hidden="true"><CloudOperationalIcon name={stage.icon} size={15} /></span>
          <span>{stage.label}</span>
          {isActive && index === stages.findIndex(candidate => activeLabels.includes(candidate.label)) ? <span className="visually-hidden" aria-current="step">Current workflow area</span> : null}
        </li>;
      })}
    </ol>
  </nav>;
}
