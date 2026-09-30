import {
  Button,
  Checkbox,
  Drawer,
  Group,
  NumberInput,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import type { ReactNode } from "react";

import type { DocumentType } from "./api/discovery";
import { OperationalIcon } from "./OperationalIcon";

interface DiscoveryCommandBarProps {
  query: string;
  limit: number;
  documentTypes: DocumentType[];
  includeDomains: string;
  excludeDomains: string;
  tablesRequired: boolean;
  pending: boolean;
  connectionSelector?: ReactNode;
  submitBlocked?: boolean;
  submitBlockedReason?: string;
  onQueryChange: (value: string) => void;
  onLimitChange: (value: number) => void;
  onDocumentTypeChange: (type: DocumentType, checked: boolean) => void;
  onIncludeDomainsChange: (value: string) => void;
  onExcludeDomainsChange: (value: string) => void;
  onTablesRequiredChange: (checked: boolean) => void;
}

export function DiscoveryCommandBar({
  query,
  limit,
  documentTypes,
  includeDomains,
  excludeDomains,
  tablesRequired,
  pending,
  connectionSelector,
  submitBlocked = false,
  submitBlockedReason,
  onQueryChange,
  onLimitChange,
  onDocumentTypeChange,
  onIncludeDomainsChange,
  onExcludeDomainsChange,
  onTablesRequiredChange,
}: DiscoveryCommandBarProps) {
  const [filtersOpened, { close, open }] = useDisclosure(false);
  const documentTypeSummary = documentTypes.length
    ? documentTypes.map((type) => type.toUpperCase()).join(" + ")
    : "No file type";
  const sourceSummary = includeDomains.trim()
    ? "Included sources"
    : excludeDomains.trim()
      ? "Filtered sources"
      : "Any reachable source";

  return (
    <>
      <div className="collect-command-heading">
        <span><OperationalIcon name="globe" size={17} />Search online</span>
        <Text size="xs">Powered by Firecrawl</Text>
      </div>
      <div
        className="discovery-command"
        data-has-connection-selector={connectionSelector ? true : undefined}
      >
        <TextInput
          className="discovery-query"
          label="Search query"
          classNames={{ label: "visually-hidden", input: "command-input" }}
          leftSection={<OperationalIcon name="search" size={21} />}
          value={query}
          onChange={(event) => onQueryChange(event.currentTarget.value)}
          placeholder="Search reports, research, manuals..."
          required
        />
        <Button
          type="submit"
          className="discovery-primary-action"
          loading={pending}
          disabled={!query.trim() || pending || submitBlocked}
          title={submitBlocked ? submitBlockedReason : undefined}
          aria-keyshortcuts="Enter"
          leftSection={<OperationalIcon name="search" size={19} />}
        >
          Discover
        </Button>
      </div>
      <div className="collect-options">
        {connectionSelector}
        <Button
          type="button"
          variant="default"
          className="command-segment document-type-summary"
          leftSection={<OperationalIcon name="document" size={18} />}
          rightSection={<OperationalIcon name="chevron" size={16} />}
          aria-label={`Document types ${documentTypeSummary}`}
          onClick={open}
        >
          {documentTypeSummary}
        </Button>
        <Switch
          className="tables-required-control"
          label={
            <span className="tables-required-label">
              Tables required
              <OperationalIcon name="info" size={17} />
            </span>
          }
          checked={tablesRequired}
          onChange={(event) => onTablesRequiredChange(event.currentTarget.checked)}
          disabled={pending}
        />
        <Button
          type="button"
          variant="subtle"
          className="collect-settings-button"
          onClick={open}
          leftSection={<OperationalIcon name="filter" size={19} />}
        >
          Collection settings
        </Button>
      </div>
      <Text className="command-summary" size="xs" aria-live="polite">
        {sourceSummary} • up to {limit} results
      </Text>
      <Drawer
        opened={filtersOpened}
        onClose={close}
        title="Discovery filters"
        closeButtonProps={{ "aria-label": "Close discovery filters" }}
        position="right"
        size="md"
        classNames={{ content: "parserium-drawer", header: "parserium-drawer-header" }}
      >
        <Stack gap="lg">
          <div>
            <Text className="drawer-section-title">Document types</Text>
            <Group mt="sm">
              <Checkbox
                label="PDF"
                checked={documentTypes.includes("pdf")}
                onChange={(event) =>
                  onDocumentTypeChange("pdf", event.currentTarget.checked)
                }
              />
              <Checkbox
                label="DOCX"
                checked={documentTypes.includes("docx")}
                onChange={(event) =>
                  onDocumentTypeChange("docx", event.currentTarget.checked)
                }
              />
            </Group>
          </div>
          <NumberInput
            label="Result limit"
            value={limit}
            onChange={(value) => onLimitChange(typeof value === "number" ? value : 20)}
            min={1}
            max={30}
            allowDecimal={false}
          />
          <TextInput
            label="Include domains"
            value={includeDomains}
            onChange={(event) => onIncludeDomainsChange(event.currentTarget.value)}
            placeholder="Comma-separated, for example bank.example"
          />
          <TextInput
            label="Exclude domains"
            value={excludeDomains}
            onChange={(event) => onExcludeDomainsChange(event.currentTarget.value)}
            placeholder="Comma-separated, for example aggregator.example"
          />
          <Button type="button" variant="light" onClick={close}>
            Close filters
          </Button>
        </Stack>
      </Drawer>
    </>
  );
}
