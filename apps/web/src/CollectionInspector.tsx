import { Alert, Button, Text, Title, Tooltip } from "@mantine/core";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import type { CollectionJob } from "./api/collection";
import { rowVariants, standardTransition } from "./motion";
import { OperationalIcon } from "./OperationalIcon";

interface CollectionInspectorProps {
  jobs: CollectionJob[] | undefined;
  totalCount?: number;
  queryFailed: boolean;
  retryFailed: boolean;
  retryPending: boolean;
  retryJobId: string | undefined;
  onRetry: (jobId: string) => void;
  clearFailed: boolean;
  clearPending: boolean;
  onClearCompleted: () => void;
  embedded?: boolean;
}

const activeCollectionStatuses = new Set(["queued", "downloading", "validating"]);

const statusPresentation: Record<string, { tone: string; label: string }> = {
  queued: { tone: "neutral", label: "Waiting" },
  downloading: { tone: "active", label: "Downloading" },
  validating: { tone: "active", label: "Validating" },
  completed: { tone: "ready", label: "Stored" },
  duplicate: { tone: "ready", label: "Already stored" },
  failed: { tone: "error", label: "Failed" },
};

function progressText(downloaded: number, total: number | null): string {
  if (total === null || total <= 0) return `${downloaded} bytes downloaded`;
  const percentage = Math.min(100, Math.round((downloaded / total) * 100));
  return `${downloaded} of ${total} bytes (${percentage}%)`;
}

function jobPercentage(job: CollectionJob): number {
  if (job.status === "completed" || job.status === "duplicate") return 100;
  if (job.status === "validating") return 75;
  if (job.content_length && job.content_length > 0) {
    return Math.min(100, (job.bytes_downloaded / job.content_length) * 100);
  }
  return 0;
}

function sourceHostname(sourceUrl: string): string {
  try {
    return new URL(sourceUrl).hostname;
  } catch {
    return "Unknown source";
  }
}

type StageState = "complete" | "active" | "pending";

function collectionStages(status: string): Array<{
  label: string;
  state: StageState;
  detail: string;
}> {
  const finished = status === "completed" || status === "duplicate";
  return [
    {
      label: "Fetch",
      state: finished || status === "validating" ? "complete" : status === "downloading" ? "active" : "pending",
      detail:
        finished || status === "validating"
          ? "Completed"
          : status === "downloading"
            ? "In progress"
            : "Pending",
    },
    {
      label: "Validate",
      state: finished ? "complete" : status === "validating" ? "active" : "pending",
      detail: finished ? "Completed" : status === "validating" ? "In progress" : "Pending",
    },
    {
      label: "Store",
      state: finished ? "complete" : "pending",
      detail: finished ? "Completed" : "Pending",
    },
  ];
}

export function CollectionInspector({
  jobs,
  totalCount,
  queryFailed,
  retryFailed,
  retryPending,
  retryJobId,
  onRetry,
  clearFailed,
  clearPending,
  onClearCompleted,
  embedded = false,
}: CollectionInspectorProps) {
  const reducedMotion = useReducedMotion();
  const featuredJob = jobs?.find((job) => activeCollectionStatuses.has(job.status)) ?? jobs?.[0];
  const remainingJobs = jobs?.filter((job) => job.id !== featuredJob?.id) ?? [];
  const featuredLabel = featuredJob?.title || featuredJob?.source_url;
  const featuredPresentation = featuredJob
    ? (statusPresentation[featuredJob.status] ?? {
        tone: "neutral",
        label: featuredJob.status,
      })
    : undefined;
  const percentage = featuredJob ? jobPercentage(featuredJob) : 0;
  const hasCompletedHistory = jobs?.some(
    (job) => job.status === "completed" || job.status === "duplicate",
  );
  const displayedTotal = totalCount ?? jobs?.length ?? 0;

  return (
    <section
      id={embedded ? undefined : "queue"}
      className={embedded ? "queue-panel queue-panel-embedded" : "queue-panel"}
      aria-labelledby={embedded ? "embedded-queue-title" : "queue-title"}
      tabIndex={embedded ? undefined : -1}
    >
      <div className="queue-heading">
        <Title
          order={2}
          id={embedded ? "embedded-queue-title" : "queue-title"}
          aria-label="Collection queue"
        >
          Collection
        </Title>
        <Text className="queue-total" aria-label={`${displayedTotal} collection jobs`}>
          {String(displayedTotal).padStart(2, "0")}
        </Text>
      </div>

      {queryFailed ? (
        <Alert role="alert" color="red" title="Collection status unavailable">
          Parserium could not load collection status. Try again shortly.
        </Alert>
      ) : null}
      {retryFailed ? (
        <Alert role="alert" color="red">
          The collection job could not be retried.
        </Alert>
      ) : null}
      {clearFailed ? (
        <Alert role="alert" color="red">
          Completed collection history could not be cleared.
        </Alert>
      ) : null}

      {!featuredJob && jobs?.length === 0 ? (
        <div className="queue-empty">
          <OperationalIcon name="queue" size={26} />
          <Text fw={600}>No collection jobs yet</Text>
          <Text size="sm">Select discovered documents to start local collection.</Text>
        </div>
      ) : null}

      <AnimatePresence initial={false} mode="popLayout">
        {featuredJob && featuredPresentation ? (
        <motion.article
          key={featuredJob.id}
          className="queue-featured-job"
          aria-label={`Collection job ${featuredLabel}`}
          layout="position"
          initial={reducedMotion ? false : "hidden"}
          animate="visible"
          exit="exit"
          variants={rowVariants}
          transition={reducedMotion ? { duration: 0 } : standardTransition}
        >
          <div className="queue-featured-primary">
            <span className="queue-index" aria-hidden="true">1</span>
            <span
              className={`file-glyph file-glyph-${featuredJob.expected_document_type}`}
              aria-hidden="true"
            >
              <OperationalIcon
                name={featuredJob.expected_document_type === "pdf" ? "file-pdf" : "file-docx"}
                size={23}
              />
            </span>
            <div className="queue-featured-identity">
              <Tooltip
                label={featuredLabel}
                openDelay={300}
                withArrow
                multiline
                maw={420}
                events={{ hover: true, focus: true, touch: false }}
                classNames={{ tooltip: "document-name-tooltip" }}
              >
                <Text className="queue-featured-title" tabIndex={0}>
                  {featuredLabel}
                </Text>
              </Tooltip>
              <Text className="queue-source">{sourceHostname(featuredJob.source_url)}</Text>
            </div>
            <Text className="queue-percentage">{Math.round(percentage)}%</Text>
          </div>
          <div
            className="queue-progress"
            role="progressbar"
            aria-label={`Download progress for ${featuredLabel}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(percentage)}
          >
            <motion.span
              className="queue-progress-value"
              initial={false}
              animate={{ scaleX: percentage / 100 }}
              transition={reducedMotion ? { duration: 0 } : standardTransition}
            />
          </div>
          <div className="queue-featured-footer">
            <span className="queue-stage" data-tone={featuredPresentation.tone}>
              {featuredPresentation.label}
            </span>
            <Text className="machine-data queue-bytes">
              {progressText(featuredJob.bytes_downloaded, featuredJob.content_length)}
            </Text>
          </div>

          {featuredJob.error_detail ? (
            <Text className="queue-error" size="sm">
              {featuredJob.error_detail}
            </Text>
          ) : null}
          {featuredJob.status === "failed" && featuredJob.error_retryable ? (
            <Button
              variant="subtle"
              color="red.4"
              onClick={() => onRetry(featuredJob.id)}
              loading={retryPending && retryJobId === featuredJob.id}
              aria-label={`Retry ${featuredLabel}`}
            >
              Retry
            </Button>
          ) : null}

          {featuredJob.status !== "failed" ? (
            <ol className="collection-stages" aria-label="Collection progress stages">
              {collectionStages(featuredJob.status).map((stage) => (
                <li key={stage.label} data-state={stage.state}>
                  <span className="stage-marker" aria-hidden="true">
                    {stage.state === "complete" ? (
                      <OperationalIcon name="check" size={14} />
                    ) : null}
                  </span>
                  <span>
                    <Text>{stage.label}</Text>
                    <Text className="stage-detail">{stage.detail}</Text>
                  </span>
                </li>
              ))}
            </ol>
          ) : null}
        </motion.article>
        ) : null}
      </AnimatePresence>

      {remainingJobs.length > 0 ? (
        <div className="queue-list-region">
          <div className="queue-list-heading">
            <Text className="section-kicker">Queued and recent</Text>
            <Text className="machine-data">{remainingJobs.length}</Text>
          </div>
          <div className="queue-list">
            <AnimatePresence initial={false} mode="popLayout">
              {remainingJobs.map((job, index) => {
                const label = job.title || job.source_url;
                const presentation = statusPresentation[job.status] ?? {
                  tone: "neutral",
                  label: job.status,
                };
                return (
                <motion.article
                  key={job.id}
                  className="queue-item"
                  aria-label={`Collection job ${label}`}
                  layout="position"
                  initial={reducedMotion ? false : "hidden"}
                  animate="visible"
                  exit="exit"
                  variants={rowVariants}
                  transition={reducedMotion ? { duration: 0 } : standardTransition}
                >
                  <div className="queue-item-main">
                    <span className="queue-index" aria-hidden="true">{index + 2}</span>
                    <span
                      className={`file-glyph file-glyph-${job.expected_document_type}`}
                      aria-hidden="true"
                    >
                      <OperationalIcon
                        name={job.expected_document_type === "pdf" ? "file-pdf" : "file-docx"}
                        size={23}
                      />
                    </span>
                    <div className="queue-item-copy">
                      <Tooltip
                        label={label}
                        openDelay={300}
                        withArrow
                        multiline
                        maw={420}
                        events={{ hover: true, focus: true, touch: false }}
                        classNames={{ tooltip: "document-name-tooltip" }}
                      >
                        <Text className="queue-item-title" tabIndex={0}>
                          {label}
                        </Text>
                      </Tooltip>
                      <Text className="queue-source">{sourceHostname(job.source_url)}</Text>
                      <Text className="machine-data">
                        {progressText(job.bytes_downloaded, job.content_length)}
                      </Text>
                    </div>
                    <span className="queue-status" data-tone={presentation.tone}>
                      {presentation.label}
                    </span>
                  </div>
                  {job.error_detail ? (
                    <Text className="queue-error" size="sm">
                      {job.error_detail}
                    </Text>
                  ) : null}
                  {job.status === "failed" && job.error_retryable ? (
                    <Button
                      variant="subtle"
                      color="red.4"
                      onClick={() => onRetry(job.id)}
                      loading={retryPending && retryJobId === job.id}
                      aria-label={`Retry ${label}`}
                    >
                      Retry
                    </Button>
                  ) : null}
                </motion.article>
                );
              })}
            </AnimatePresence>
          </div>
        </div>
      ) : null}

      {hasCompletedHistory ? (
        <Button
          className="queue-clear-completed"
          variant="subtle"
          leftSection={<OperationalIcon name="trash" size={17} />}
          loading={clearPending}
          onClick={onClearCompleted}
        >
          Clear completed
        </Button>
      ) : null}
    </section>
  );
}
