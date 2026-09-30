import { Anchor, Button, Checkbox, Group, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Fragment, useState } from "react";

import type {
  AnalysisCandidate,
  CandidateAnalysisStatus,
  CandidateTable,
} from "./api/discovery";
import { detailVariants, rowVariants, standardTransition } from "./motion";
import { OperationalIcon } from "./OperationalIcon";

interface DiscoveryResultsProps {
  candidates: AnalysisCandidate[];
  selectedIds: string[];
  collecting: boolean;
  tablesRequired: boolean;
  onToggle: (id: string, checked: boolean) => void;
  onSelectAll: () => void;
  onClear: () => void;
  onCollect: () => void;
}

const DPI_SCALE = 150 / 72;

export function sourceHostname(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "Unknown source";
  }
}

function statusLabel(status: CandidateAnalysisStatus): string {
  const labels: Record<CandidateAnalysisStatus, string> = {
    queued: "Queued",
    downloading: "Downloading",
    validating: "Validating",
    converting: "Converting",
    parsing: "Analyzing",
    ready: "Valid",
    no_tables: "No tables",
    partial: "Partial",
    failed: "Failed",
    cancelled: "Cancelled",
    promoted: "Collected",
  };
  return labels[status];
}

function statusTone(status: CandidateAnalysisStatus): string {
  if (["ready", "promoted"].includes(status)) return "ready";
  if (["failed", "cancelled"].includes(status)) return "error";
  if (["queued", "downloading", "validating", "converting", "parsing"].includes(status)) {
    return "active";
  }
  return "neutral";
}

function statusIcon(status: CandidateAnalysisStatus) {
  if (status === "ready" || status === "promoted") {
    return <OperationalIcon name="check-circle" size={16} />;
  }
  if (status === "no_tables" || status === "partial") {
    return <OperationalIcon name="minus-circle" size={16} />;
  }
  return null;
}

function tableLabel(candidate: AnalysisCandidate): string {
  if (["queued", "downloading", "validating", "converting", "parsing"].includes(candidate.status)) {
    return "…";
  }
  if (["failed", "cancelled"].includes(candidate.status)) return "-";
  return `${candidate.table_count_lower_bound ? "≥" : ""}${candidate.table_count}`;
}

function isSelectable(candidate: AnalysisCandidate, tablesRequired: boolean): boolean {
  if (candidate.status === "ready" || candidate.status === "partial") return true;
  return !tablesRequired && candidate.status === "no_tables";
}

function overlayStyle(candidate: AnalysisCandidate, table: CandidateTable) {
  const width = candidate.preview_width;
  const height = candidate.preview_height;
  if (!width || !height) return undefined;
  const bounds = table.bounding_box;
  return {
    left: `${(bounds.x * DPI_SCALE * 100) / width}%`,
    top: `${(bounds.y * DPI_SCALE * 100) / height}%`,
    width: `${(bounds.width * DPI_SCALE * 100) / width}%`,
    height: `${(bounds.height * DPI_SCALE * 100) / height}%`,
  };
}

export function DiscoveryResults({
  candidates,
  selectedIds,
  collecting,
  tablesRequired,
  onToggle,
  onSelectAll,
  onClear,
  onCollect,
}: DiscoveryResultsProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const reducedMotion = useReducedMotion();
  const selectableIds = candidates
    .filter((candidate) => isSelectable(candidate, tablesRequired))
    .slice(0, 30)
    .map((candidate) => candidate.id);
  const selectedCount = selectableIds.filter((id) => selectedIds.includes(id)).length;
  const allSelected = selectableIds.length > 0 && selectedCount === selectableIds.length;
  const someSelected = selectedCount > 0 && !allSelected;

  return (
    <section
      className="discovery-results"
      role="region"
      aria-label="Discovered documents"
      aria-live="polite"
    >
      <Group className="result-toolbar" justify="space-between" align="center" wrap="nowrap">
        <Group className="result-count" gap="lg" wrap="nowrap">
          <Text className="result-toolbar-title">Discovered documents</Text>
          <Text className="machine-data">{candidates.length} results</Text>
        </Group>
        <Group className="result-actions" gap="xs" wrap="nowrap">
          <Text className="selected-count">{selectedIds.length} selected</Text>
          <Button
            type="button"
            className="collect-action"
            leftSection={<OperationalIcon name="download" size={17} />}
            aria-label="Collect selected"
            onClick={onCollect}
            loading={collecting}
            disabled={selectedIds.length === 0}
          >
            Collect selected
          </Button>
        </Group>
      </Group>

      <div className="result-table" role="table" aria-label="Discovered document results">
        <div role="rowgroup">
          <div className="result-grid result-grid-header" role="row">
            <div role="columnheader" className="result-select-all">
              <Checkbox
                aria-label={
                  allSelected
                    ? "Clear all analyzed documents"
                    : "Select all analyzed documents"
                }
                checked={allSelected}
                indeterminate={someSelected}
                disabled={selectableIds.length === 0}
                onChange={() => (allSelected ? onClear() : onSelectAll())}
              />
            </div>
            <Text role="columnheader">Document title</Text>
            <Text role="columnheader">Source</Text>
            <Text role="columnheader">Type</Text>
            <Text role="columnheader">Tables</Text>
            <Text role="columnheader">Validation</Text>
            <span role="columnheader" aria-label="Details" />
          </div>
        </div>

        <div className="result-list" role="rowgroup">
          {candidates.map((candidate, index) => {
            const label = candidate.title || candidate.source_url;
            const selected = selectedIds.includes(candidate.id);
            const expanded = expandedId === candidate.id;
            const selectable = isSelectable(candidate, tablesRequired);
            const previewTables = candidate.tables.filter(
              (table) => table.page_num === candidate.preview_page_num,
            );
            return (
              <Fragment key={candidate.id}>
                <motion.div
                  className="result-grid result-row"
                  role="row"
                  data-selected={selected ? "true" : "false"}
                  initial={reducedMotion ? false : "hidden"}
                  animate="visible"
                  variants={rowVariants}
                  transition={
                    reducedMotion
                      ? { duration: 0 }
                      : { ...standardTransition, delay: Math.min(index, 5) * 0.025 }
                  }
                >
                  <div role="cell" className="result-checkbox-cell">
                    <Checkbox
                      aria-label={`Select ${label}`}
                      checked={selected}
                      disabled={!selectable || (!selected && selectedIds.length >= 30)}
                      onChange={(event) =>
                        onToggle(candidate.id, event.currentTarget.checked)
                      }
                    />
                  </div>
                  <div className="result-document" role="cell">
                    <Tooltip
                      label={label}
                      openDelay={300}
                      withArrow
                      multiline
                      maw={420}
                      events={{ hover: true, focus: true, touch: false }}
                      classNames={{ tooltip: "document-name-tooltip" }}
                    >
                      <Anchor
                        className="result-title"
                        href={candidate.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {label}
                      </Anchor>
                    </Tooltip>
                  </div>
                  <Text className="result-source" size="sm" role="cell">
                    {sourceHostname(candidate.source_url)}
                  </Text>
                  <Text className="file-type" role="cell">
                    {candidate.document_type.toUpperCase()}
                  </Text>
                  <Text className="result-tables" role="cell">
                    {tableLabel(candidate)}
                  </Text>
                  <Text
                    className="result-validation"
                    role="cell"
                    data-tone={statusTone(candidate.status)}
                  >
                    {statusIcon(candidate.status)}
                    {statusLabel(candidate.status)}
                  </Text>
                  <div role="cell">
                    <UnstyledButton
                      type="button"
                      className="result-detail-toggle"
                      aria-label={
                        expanded ? `Hide details for ${label}` : `Show details for ${label}`
                      }
                      aria-expanded={expanded}
                      onClick={() =>
                        setExpandedId((current) =>
                          current === candidate.id ? null : candidate.id,
                        )
                      }
                    >
                      <OperationalIcon className="result-chevron" name="chevron" size={18} />
                    </UnstyledButton>
                  </div>
                </motion.div>
                <AnimatePresence initial={false}>
                  {expanded ? (
                    <motion.div
                      key={`${candidate.id}-details`}
                      className="result-details-row"
                      role="row"
                      initial={reducedMotion ? false : "hidden"}
                      animate="visible"
                      exit="exit"
                      variants={detailVariants}
                      transition={reducedMotion ? { duration: 0 } : standardTransition}
                    >
                      <div className="result-details" role="cell" aria-colspan={7}>
                        <div className="result-preview">
                          <Text className="detail-heading">Document preview</Text>
                          {candidate.preview_available && candidate.preview_page_num ? (
                            <div className="analysis-preview-frame">
                              <img
                                src={`/api/v1/discovery/analyses/${encodeURIComponent(candidate.id)}/preview`}
                                alt={`Page ${candidate.preview_page_num} preview for ${label}`}
                              />
                              {previewTables.map((table) => (
                                <span
                                  key={table.id}
                                  className="analysis-table-overlay"
                                  data-testid={`table-overlay-${table.id}`}
                                  style={overlayStyle(candidate, table)}
                                />
                              ))}
                            </div>
                          ) : (
                            <div className="preview-placeholder">
                              <OperationalIcon name="document" size={30} />
                              <Text fw={600}>{statusLabel(candidate.status)}</Text>
                              <Text size="sm">
                                {candidate.error_detail ||
                                  "The local worker is preparing a validated preview."}
                              </Text>
                            </div>
                          )}
                        </div>
                        <div className="result-metadata">
                          <Text className="detail-heading">Document details</Text>
                          <dl>
                            <dt>Description</dt>
                            <dd>
                              {candidate.description ||
                                "No description was supplied by discovery."}
                            </dd>
                            <dt>Source</dt>
                            <dd>Firecrawl discovery</dd>
                            <dt>Host</dt>
                            <dd>{sourceHostname(candidate.source_url)}</dd>
                            <dt>Type</dt>
                            <dd>{candidate.document_type.toUpperCase()}</dd>
                            <dt>Pages</dt>
                            <dd>{candidate.page_count ?? "Analyzing"}</dd>
                            <dt>Tables</dt>
                            <dd>{tableLabel(candidate)}</dd>
                            <dt>URL</dt>
                            <dd className="machine-data">{candidate.source_url}</dd>
                          </dl>
                          {candidate.tables.length > 0 ? (
                            <div className="analysis-markdown-output">
                              <Text className="analysis-markdown-heading">
                                Extracted table Markdown
                              </Text>
                              {candidate.tables.map((table) => (
                                <pre key={table.id}>{table.markdown}</pre>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </Fragment>
            );
          })}
        </div>
      </div>
    </section>
  );
}
