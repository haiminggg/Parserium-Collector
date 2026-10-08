import { ActionIcon, Text, TextInput, UnstyledButton } from "@mantine/core";
import { useEffect, useState } from "react";

import { normalizeExportDirectory } from "./api/collection";
import { OperationalIcon } from "./OperationalIcon";

export const exportSubfolderStorageKey = "parserium.exportSubfolder.v1";

interface DocumentUtilityBarProps {
  documentCount: number | undefined;
  onOpenFolder: (normalizedSubfolder: string) => void;
  onPreferenceChange: (normalizedSubfolder: string) => void;
}

function readStoredExportSubfolder(): string {
  try {
    const stored = window.localStorage.getItem(exportSubfolderStorageKey);
    if (stored === null) return "";
    return normalizeExportDirectory(stored) ?? "";
  } catch {
    return "";
  }
}

function persistExportSubfolder(normalized: string): void {
  try {
    if (normalized) {
      window.localStorage.setItem(exportSubfolderStorageKey, normalized);
    } else {
      window.localStorage.removeItem(exportSubfolderStorageKey);
    }
  } catch {
    // The preference remains usable for this session when storage is unavailable.
  }
}

function documentCountLabel(documentCount: number | undefined): string {
  if (documentCount === undefined) return "Stored documents loading";
  return `${documentCount} stored document${documentCount === 1 ? "" : "s"}`;
}

function openFolderLabel(documentCount: number | undefined): string {
  if (documentCount === undefined) return "Open folder, stored documents loading";
  return `Open folder, ${documentCount} stored document${documentCount === 1 ? "" : "s"}`;
}

export function DocumentUtilityBar({
  documentCount,
  onOpenFolder,
  onPreferenceChange,
}: DocumentUtilityBarProps) {
  const [exportSubfolder, setExportSubfolder] = useState(readStoredExportSubfolder);
  const [copyFeedback, setCopyFeedback] = useState("");
  const normalizedSubfolder = normalizeExportDirectory(exportSubfolder);
  const invalid = normalizedSubfolder === null;

  useEffect(() => {
    onPreferenceChange(normalizeExportDirectory(exportSubfolder) ?? "");
  }, [exportSubfolder, onPreferenceChange]);

  function updatePreference(value: string) {
    setExportSubfolder(value);
    setCopyFeedback("");
    const normalized = normalizeExportDirectory(value);
    if (normalized === null) return;
    persistExportSubfolder(normalized);
    onPreferenceChange(normalized);
  }

  function normalizeVisiblePreference() {
    const normalized = normalizeExportDirectory(exportSubfolder);
    if (normalized === null) return;
    setExportSubfolder(normalized);
  }

  async function copyPreference() {
    if (!normalizedSubfolder) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(normalizedSubfolder);
      setCopyFeedback("Export subfolder copied");
    } catch {
      setCopyFeedback("Copy unavailable. Select the subfolder and copy it manually.");
    }
  }

  return (
    <div className="documents-utility" id="documents">
      <div className="documents-utility-summary">
        <OperationalIcon name="folder" size={20} />
        <Text className="documents-utility-title">{documentCountLabel(documentCount)}</Text>
      </div>

      <div className="documents-utility-preference">
        <Text component="label" htmlFor="export-subfolder-preference">
          Export subfolder
        </Text>
        <div className="documents-utility-input-row">
          <TextInput
            id="export-subfolder-preference"
            className="documents-utility-input"
            aria-label="Export subfolder"
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? "export-subfolder-error" : undefined}
            value={exportSubfolder}
            placeholder="investment-docs"
            onChange={(event) => updatePreference(event.currentTarget.value)}
            onBlur={normalizeVisiblePreference}
          />
          <ActionIcon
            className="documents-utility-copy"
            variant="subtle"
            aria-label="Copy export subfolder"
            disabled={!normalizedSubfolder}
            onClick={() => void copyPreference()}
          >
            <OperationalIcon name="copy" size={17} />
          </ActionIcon>
        </div>
        {invalid ? (
          <span id="export-subfolder-error" className="visually-hidden">
            Use a relative subfolder without drive letters or parent paths.
          </span>
        ) : null}
        <span className="visually-hidden" role="status" aria-live="polite">
          {copyFeedback}
        </span>
      </div>

      <UnstyledButton
        className="documents-utility-open"
        aria-label={openFolderLabel(documentCount)}
        disabled={invalid}
        onClick={() => onOpenFolder(normalizedSubfolder ?? "")}
      >
        Open folder
        <OperationalIcon name="external-link" size={17} />
      </UnstyledButton>
    </div>
  );
}
