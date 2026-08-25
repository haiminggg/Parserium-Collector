import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  NumberInput,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  type DocumentDiscoveryRequest,
  type DocumentType,
  searchDocuments,
} from "./api/discovery";

interface DiscoveryScreenProps {
  csrfToken: string;
}

function domains(value: string): string[] {
  return value
    .split(",")
    .map((domain) => domain.trim())
    .filter(Boolean);
}

export function DiscoveryScreen({ csrfToken }: DiscoveryScreenProps) {
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(20);
  const [documentTypes, setDocumentTypes] = useState<DocumentType[]>(["pdf", "docx"]);
  const [includeDomains, setIncludeDomains] = useState("");
  const [excludeDomains, setExcludeDomains] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const search = useMutation({
    mutationFn: (request: DocumentDiscoveryRequest) => searchDocuments(request, csrfToken),
  });

  function toggleType(documentType: DocumentType, checked: boolean) {
    setDocumentTypes((current) =>
      checked
        ? [...current, documentType].filter(
            (value, index, values) => values.indexOf(value) === index,
          )
        : current.filter((value) => value !== documentType),
    );
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    search.reset();
    if (documentTypes.length === 0) {
      setValidationError("Select at least one document type.");
      return;
    }
    const included = domains(includeDomains);
    const excluded = domains(excludeDomains);
    if (included.length > 0 && excluded.length > 0) {
      setValidationError("Use either include domains or exclude domains, not both.");
      return;
    }
    setValidationError(null);
    search.mutate({
      query: query.trim(),
      limit,
      document_types: documentTypes,
      include_domains: included,
      exclude_domains: excluded,
    });
  }

  return (
    <Card component="section" withBorder p="lg">
      <form onSubmit={submit}>
        <Stack gap="md">
          <div>
            <Title order={2}>Document discovery</Title>
            <Text size="sm">
              Search metadata through local Firecrawl. Parserium does not download candidates in
              this step.
            </Text>
          </div>
          {validationError ? <Alert role="alert">{validationError}</Alert> : null}
          {search.isError ? (
            <Alert role="alert" color="red" title="Search failed">
              Document search is unavailable. Check the local Firecrawl connection and try again.
            </Alert>
          ) : null}
          <TextInput
            label="Search query"
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
            placeholder="bank investment reports containing tables"
            required
          />
          <Group align="end">
            <Checkbox
              label="PDF"
              checked={documentTypes.includes("pdf")}
              onChange={(event) => toggleType("pdf", event.currentTarget.checked)}
            />
            <Checkbox
              label="DOCX"
              checked={documentTypes.includes("docx")}
              onChange={(event) => toggleType("docx", event.currentTarget.checked)}
            />
            <NumberInput
              label="Result limit"
              value={limit}
              onChange={(value) => setLimit(typeof value === "number" ? value : 20)}
              min={1}
              max={30}
              allowDecimal={false}
            />
          </Group>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput
              label="Include domains"
              value={includeDomains}
              onChange={(event) => setIncludeDomains(event.currentTarget.value)}
              placeholder="Comma-separated, for example bank.example"
            />
            <TextInput
              label="Exclude domains"
              value={excludeDomains}
              onChange={(event) => setExcludeDomains(event.currentTarget.value)}
              placeholder="Comma-separated, for example aggregator.example"
            />
          </SimpleGrid>
          <Button
            type="submit"
            color="blue.9"
            loading={search.isPending}
            disabled={!query.trim()}
          >
            Search documents
          </Button>
          {search.data ? (
            <Stack gap="sm" aria-live="polite">
              <Text fw={600}>{search.data.candidates.length} direct document links found</Text>
              {search.data.candidates.length === 0 ? (
                <Text>No direct PDF or DOCX links were found.</Text>
              ) : (
                search.data.candidates.map((candidate) => (
                  <Card key={candidate.url} withBorder>
                    <Group justify="space-between" align="start" wrap="nowrap">
                      <Stack gap={4}>
                        <Anchor
                          href={candidate.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          fw={600}
                        >
                          {candidate.title || candidate.url}
                        </Anchor>
                        {candidate.description ? (
                          <Text size="sm">{candidate.description}</Text>
                        ) : null}
                        <Text size="xs" c="gray.8" lineClamp={1}>
                          {candidate.url}
                        </Text>
                      </Stack>
                      <Badge>{candidate.document_type.toUpperCase()}</Badge>
                    </Group>
                  </Card>
                ))
              )}
              {search.data.rejected_non_document_results > 0 ? (
                <Text size="xs" c="gray.8">
                  Ignored {search.data.rejected_non_document_results} results that were not direct
                  selected document links.
                </Text>
              ) : null}
            </Stack>
          ) : null}
        </Stack>
      </form>
    </Card>
  );
}
