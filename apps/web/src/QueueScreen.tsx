import { Alert, Button, Modal, Skeleton, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  cancelActivityJob,
  deleteActivityHistory,
  listActivity,
  retryActivityJob,
  type ActivityJob,
  type ActivityPage,
  type ActivityJobState,
  type ActivityJobType,
} from "./api/activity";


interface QueueScreenProps {
  csrfToken: string;
}

type DateWindow = "all" | "day" | "week" | "month";

const typeLabels: Record<ActivityJobType, string> = {
  discovery: "Discovery",
  collection: "Collection",
  analysis: "Analysis",
  export: "Export",
};

const stateLabels: Record<ActivityJobState, string> = {
  queued: "Queued",
  active: "Active",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

function createdAfter(window: DateWindow): string | null {
  const durations: Record<Exclude<DateWindow, "all">, number> = {
    day: 24 * 60 * 60 * 1000,
    week: 7 * 24 * 60 * 60 * 1000,
    month: 30 * 24 * 60 * 60 * 1000,
  };
  if (window === "all") return null;
  return new Date(Date.now() - durations[window]).toISOString();
}

function formattedDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function readableStage(value: string): string {
  const normalized = value.replaceAll("_", " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

export function QueueScreen({ csrfToken }: QueueScreenProps) {
  const queryClient = useQueryClient();
  const [jobType, setJobType] = useState<ActivityJobType | "all">("all");
  const [state, setState] = useState<ActivityJobState | "all">("all");
  const [creatorId, setCreatorId] = useState("all");
  const [dateWindow, setDateWindow] = useState<DateWindow>("all");
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorTrail, setCursorTrail] = useState<Array<string | null>>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ActivityJob | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const filterKey = [jobType, state, creatorId, dateWindow] as const;
  const activity = useQuery({
    queryKey: ["activity", ...filterKey, cursor],
    queryFn: () =>
      listActivity({
        limit: 50,
        cursor,
        jobType: jobType === "all" ? null : jobType,
        state: state === "all" ? null : state,
        creatorId: creatorId === "all" ? null : creatorId,
        createdAfter: createdAfter(dateWindow),
      }),
    placeholderData: (previous) => previous,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && (data.summary.active > 0 || data.summary.queued > 0) ? 3_000 : false;
    },
  });

  const creators = useMemo(() => {
    const found = new Map<string, string>();
    for (const item of activity.data?.items ?? []) {
      if (item.created_by_user_id) {
        found.set(item.created_by_user_id, item.created_by_name ?? "Unknown member");
      }
    }
    return [...found.entries()];
  }, [activity.data?.items]);

  function resetPage() {
    setCursor(null);
    setCursorTrail([]);
  }

  const refreshActivity = async () => {
    await queryClient.invalidateQueries({ queryKey: ["activity"] });
  };

  const deleteHistory = useMutation({
    mutationFn: (job: ActivityJob) =>
      deleteActivityHistory(job.job_type, job.id, csrfToken),
    onSuccess: async (_value, deletedJob) => {
      setDeleteTarget(null);
      queryClient.setQueriesData<ActivityPage>({ queryKey: ["activity"] }, (current) => {
        if (!current) return current;
        const items = current.items.filter((item) => item.id !== deletedJob.id);
        return items.length === current.items.length
          ? current
          : { ...current, items, total: Math.max(0, current.total - 1) };
      });
      await refreshActivity();
    },
    onError: () => setActionError("The selected job history could not be deleted."),
  });
  const cancelJob = useMutation({
    mutationFn: (job: ActivityJob) => cancelActivityJob(job, csrfToken),
    onSuccess: refreshActivity,
    onError: () => setActionError("The selected job could not be cancelled."),
  });
  const retryJob = useMutation({
    mutationFn: (job: ActivityJob) => retryActivityJob(job, csrfToken),
    onSuccess: refreshActivity,
    onError: () => setActionError("The selected job could not be retried."),
  });

  const summary = activity.data?.summary;

  return (
    <main className="activity-page" aria-labelledby="activity-title">
      <header className="activity-heading">
        <div>
          <Text className="section-kicker">Workspace operations</Text>
          <Title order={1} id="activity-title">Activity</Title>
          <Text className="activity-introduction">
            Track discovery, downloads, table analysis, and exports in one place.
          </Text>
        </div>
        <Button
          variant="subtle"
          className="activity-refresh"
          loading={activity.isFetching}
          onClick={() => void activity.refetch()}
        >
          Refresh
        </Button>
      </header>

      <section className="activity-summary" aria-label="Activity summary">
        {(["active", "queued", "failed", "completed"] as const).map((key) => (
          <div className="activity-summary-item" data-state={key} key={key}>
            <span className="activity-summary-label">{stateLabels[key]}</span>
            <strong className="activity-summary-value">{summary?.[key] ?? 0}</strong>
          </div>
        ))}
      </section>

      <section className="activity-toolbar" aria-label="Activity filters">
        <label>
          <span>Job type</span>
          <select
            value={jobType}
            onChange={(event) => {
              setJobType(event.currentTarget.value as ActivityJobType | "all");
              resetPage();
            }}
          >
            <option value="all">All jobs</option>
            {Object.entries(typeLabels).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Status</span>
          <select
            value={state}
            onChange={(event) => {
              setState(event.currentTarget.value as ActivityJobState | "all");
              resetPage();
            }}
          >
            <option value="all">All statuses</option>
            {Object.entries(stateLabels).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Creator</span>
          <select
            value={creatorId}
            onChange={(event) => {
              setCreatorId(event.currentTarget.value);
              resetPage();
            }}
          >
            <option value="all">All creators</option>
            {creators.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label>
          <span>Created</span>
          <select
            value={dateWindow}
            onChange={(event) => {
              setDateWindow(event.currentTarget.value as DateWindow);
              resetPage();
            }}
          >
            <option value="all">Any time</option>
            <option value="day">Last 24 hours</option>
            <option value="week">Last 7 days</option>
            <option value="month">Last 30 days</option>
          </select>
        </label>
      </section>

      {actionError ? (
        <Alert role="alert" color="red" title="Queue action failed" withCloseButton onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      ) : null}
      {activity.isError ? (
        <Alert role="alert" color="red" title="Queue unavailable">
          Parserium could not load activity for this workspace.
        </Alert>
      ) : null}

      {activity.isLoading ? (
        <div className="activity-loading" role="status" aria-label="Loading queue">
          <Skeleton height={58} radius={0} />
          <Skeleton height={58} radius={0} />
          <Skeleton height={58} radius={0} />
        </div>
      ) : null}

      {activity.data && activity.data.items.length === 0 ? (
        <section className="activity-empty" aria-label="Empty queue">
          <strong>No jobs match these filters.</strong>
          <Text>New discovery and collection work will appear here automatically.</Text>
        </section>
      ) : null}

      {activity.data && activity.data.items.length > 0 ? (
        <section className="activity-ledger" aria-label="Workspace job history">
          <div className="activity-table-scroll">
            <table className="activity-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Stage</th>
                  <th>Progress</th>
                  <th>Creator</th>
                  <th>Updated</th>
                  <th><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {activity.data.items.map((job) => (
                  <JobRows
                    key={job.id}
                    job={job}
                    expanded={expandedId === job.id}
                    busy={deleteHistory.isPending || cancelJob.isPending || retryJob.isPending}
                    onToggle={() => setExpandedId((current) => current === job.id ? null : job.id)}
                    onCancel={() => cancelJob.mutate(job)}
                    onRetry={() => retryJob.mutate(job)}
                    onDelete={() => setDeleteTarget(job)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <footer className="activity-pagination">
            <Text className="machine-data">{activity.data.total} matching jobs</Text>
            <div>
              <Button
                variant="subtle"
                size="compact-sm"
                disabled={cursorTrail.length === 0}
                onClick={() => {
                  const trail = [...cursorTrail];
                  setCursor(trail.pop() ?? null);
                  setCursorTrail(trail);
                }}
              >
                Previous
              </Button>
              <Button
                variant="subtle"
                size="compact-sm"
                disabled={!activity.data.next_cursor}
                onClick={() => {
                  setCursorTrail((trail) => [...trail, cursor]);
                  setCursor(activity.data?.next_cursor ?? null);
                }}
              >
                Next
              </Button>
            </div>
          </footer>
        </section>
      ) : null}

      <Modal
        opened={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete job history?"
        centered
      >
        <Text size="sm">
          This removes the job from Activity. Stored documents and extracted artifacts are kept.
        </Text>
        <div className="activity-modal-actions">
          <Button variant="default" onClick={() => setDeleteTarget(null)}>Keep history</Button>
          <Button
            color="red"
            loading={deleteHistory.isPending}
            onClick={() => deleteTarget && deleteHistory.mutate(deleteTarget)}
          >
            Delete history
          </Button>
        </div>
      </Modal>
    </main>
  );
}

interface JobRowsProps {
  job: ActivityJob;
  expanded: boolean;
  busy: boolean;
  onToggle: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onDelete: () => void;
}

function JobRows({ job, expanded, busy, onToggle, onCancel, onRetry, onDelete }: JobRowsProps) {
  return (
    <>
      <tr className="activity-row" aria-label={`${job.title} ${stateLabels[job.state]}`}>
        <td>
          <div className="activity-job-identity">
            <span className="activity-type-mark" data-type={job.job_type}>{typeLabels[job.job_type].slice(0, 1)}</span>
            <span className="activity-job-copy">
              <strong title={job.title}>{job.title}</strong>
              <span>{typeLabels[job.job_type]}{job.subtitle ? ` / ${job.subtitle}` : ""}</span>
            </span>
          </div>
        </td>
        <td>
          <span className="activity-state" data-state={job.state}>{stateLabels[job.state]}</span>
          <span className="activity-stage">{readableStage(job.stage)}</span>
        </td>
        <td className="machine-data activity-progress-cell">
          {job.progress_percent === null ? "Not reported" : `${job.progress_percent}%`}
          {job.progress_percent !== null ? <span className="activity-progress-line" style={{ "--activity-progress": `${job.progress_percent}%` } as React.CSSProperties} /> : null}
        </td>
        <td>{job.created_by_name ?? "System"}</td>
        <td className="machine-data">{formattedDate(job.updated_at)}</td>
        <td>
          <div className="activity-row-actions">
            {job.can_cancel ? <Button variant="subtle" size="compact-xs" disabled={busy} onClick={onCancel}>Cancel</Button> : null}
            {job.can_retry ? <Button variant="subtle" size="compact-xs" disabled={busy} onClick={onRetry}>Retry</Button> : null}
            {job.can_delete ? <Button variant="subtle" size="compact-xs" color="red" disabled={busy} onClick={onDelete}>Delete history</Button> : null}
            <Button variant="subtle" size="compact-xs" onClick={onToggle} aria-label={expanded ? "Hide job details" : "Show job details"}>
              {expanded ? "Close" : "Details"}
            </Button>
          </div>
        </td>
      </tr>
      {expanded ? (
        <tr className="activity-detail-row">
          <td colSpan={6}>
            <dl className="activity-details">
              <div><dt>Job ID</dt><dd>{job.id}</dd></div>
              <div><dt>Created</dt><dd>{formattedDate(job.created_at)}</dd></div>
              <div><dt>Completed</dt><dd>{job.completed_at ? formattedDate(job.completed_at) : "Not completed"}</dd></div>
              <div><dt>Error code</dt><dd>{job.error_code?.replaceAll("_", " ") ?? "None"}</dd></div>
            </dl>
          </td>
        </tr>
      ) : null}
    </>
  );
}
