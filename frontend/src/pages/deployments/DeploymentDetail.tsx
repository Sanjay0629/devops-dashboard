/**
 * One deployment: its progress and its result (API_CONTRACTS.md §21.3, §21.6; PRD §4.10, §8).
 *
 * The page polls at the `poll_after_seconds` the backend names until the deployment is terminal,
 * then stops. What it shows, and the care taken over the wording:
 *
 * - **A success says exactly what backs it.** With no post-deployment health checks configured
 *   (the only case until M5.5) it says that success means *only that the pipeline succeeded*, in
 *   words, beside the result. It never says "healthy" or "verified".
 * - **An outcome DCP could not establish says so.** `TRIGGER_OUTCOME_UNKNOWN`, `QUEUE_ITEM_LOST` and
 *   `DEPLOYMENT_TIMED_OUT` mean a build may exist that DCP cannot see or stop; the page tells the
 *   person to check Jenkins before requesting again, because a confident "failed" there would
 *   invite a second deployment on top of a first that worked.
 * - **A Jenkins outage is shown as one, with how stale the picture is.** `last_poll_error` is a
 *   closed reason and `last_polled_at` says when DCP last got an answer: the status above them is
 *   "as last known", never presented as current.
 * - A failure is a closed code in words. Jenkins' own text never arrives here: the backend never
 *   stores it. There is no Jenkins URL anywhere on the page, and no build log (M5.3) or rollback
 *   (M5.5) yet, which it says rather than leaving blank.
 * - Cancel is offered only while the row is cancellable, and a `409` is shown as "a worker got
 *   there first", not retried.
 *
 * Nothing here authorises anything; the backend decides every request.
 */

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  CANCELLABLE,
  STATUS_TEXT,
  TERMINAL,
  UNKNOWN_OUTCOME,
  cancelDeployment,
  failureText,
  pollErrorText,
  readDeployment,
  type DeploymentDetail,
} from '@/api/deploy';
import { useIsReadOnly } from '@/auth/useSession';
import { formatInstant } from '@/components/chart/ticks';
import { LoadingState } from '@/components/EmptyState';
import { ErrorState } from '@/components/ErrorState';
import { StatusBadge } from '@/components/StatusBadge';
import { useLatestResponse } from '@/components/useLatestResponse';

const NO_CHECKS =
  'No post-deployment health checks are configured for this target; success means only that the pipeline succeeded.';

function Row({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <div
      className="flex justify-between gap-4 border-t py-2"
      style={{ borderColor: 'var(--c-border)' }}
    >
      <dt style={{ color: 'var(--c-text-muted)' }}>{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function when(value: string | null | undefined): React.ReactNode {
  return value == null ? (
    <span style={{ color: 'var(--c-text-muted)' }}>—</span>
  ) : (
    formatInstant(new Date(value).getTime())
  );
}

/** What backs a success, in words. Never "verified" and never "healthy" without checks. */
function HealthStatement({ deployment }: { deployment: DeploymentDetail }): React.JSX.Element {
  const result = deployment.health_check_result as {
    configured?: unknown;
    passed?: unknown;
    checks?: unknown;
  };
  if (result.configured === false) {
    return <p data-testid="health-statement">{NO_CHECKS}</p>;
  }
  const checks = Array.isArray(result.checks)
    ? (result.checks as { name?: unknown; healthy?: unknown }[])
    : [];
  return (
    <div data-testid="health-statement">
      <p>Post-deployment health checks {result.passed === true ? 'passed' : 'did not pass'}.</p>
      {checks.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {checks.map((check) => (
            <li key={String(check.name)}>
              {String(check.name)}: {check.healthy === true ? 'healthy' : 'not healthy'}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DeploymentDetailPage(): React.JSX.Element {
  const { deploymentId = '' } = useParams();
  const readOnly = useIsReadOnly();
  const { value, reload } = useLatestResponse(() => readDeployment(deploymentId), [deploymentId]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const deployment = value?.kind === 'ok' ? value.value : null;
  const wait = deployment?.poll_after_seconds ?? null;

  // Ask again after the interval the backend named, and never once it is terminal. Keyed on the
  // deployment object so each fresh answer re-arms the timer and a stale one never does.
  useEffect(() => {
    if (deployment === null || wait === null) return undefined;
    const timer = setTimeout(() => void reload(), wait * 1000);
    return () => {
      clearTimeout(timer);
    };
  }, [deployment, wait, reload]);

  if (value === null) return <LoadingState what="this deployment" />;
  if (value.kind !== 'ok') return <ErrorState result={value} onRetry={() => void reload()} />;
  const d = value.value;
  const terminal = TERMINAL.includes(d.execution_status);
  const failed = d.execution_status === 'FAILED';
  const reason = failureText(d.failure_reason);
  const unknownOutcome = d.failure_reason != null && UNKNOWN_OUTCOME.includes(d.failure_reason);
  const pollError = pollErrorText(d.last_poll_error);

  const cancel = async (): Promise<void> => {
    setBusy(true);
    setProblem(null);
    const result = await cancelDeployment(d.deployment_id);
    setBusy(false);
    if (result.kind === 'ok') {
      void reload();
      return;
    }
    setProblem(
      result.kind === 'conflict'
        ? 'A worker has already picked this up, so it can no longer be cancelled.'
        : result.kind === 'forbidden'
          ? 'You do not have permission to cancel this deployment.'
          : result.kind === 'read_only'
            ? 'The portal is in read-only mode. State-changing actions are disabled.'
            : result.kind === 'not_found'
              ? 'This deployment does not exist, or is outside your access scope.'
              : result.kind === 'invalid'
                ? result.message
                : `Could not reach the backend: ${result.reason}`,
    );
    void reload();
  };

  return (
    <section aria-labelledby="deployment-heading">
      <p className="mb-2 text-sm">
        <Link to="/deployments" className="underline">
          ← Deployments
        </Link>
      </p>
      <h1 id="deployment-heading" className="text-xl font-semibold">
        Deploy {d.target.application_name} to {d.environment}
      </h1>
      <p className="mt-1 text-sm" style={{ color: 'var(--c-text-muted)' }}>
        <code>{d.requested_ref}</code>
        {d.commit_sha != null && (
          <>
            {' '}
            · commit <code>{d.commit_sha.slice(0, 12)}</code>
          </>
        )}
      </p>

      <p
        role="status"
        className="mt-2 flex items-center gap-2 text-sm font-medium"
        style={{
          color:
            d.execution_status === 'SUCCEEDED'
              ? 'var(--c-ok)'
              : failed
                ? 'var(--c-critical)'
                : 'var(--c-text)',
        }}
      >
        <StatusBadge value={d.execution_status} />
        {STATUS_TEXT[d.execution_status]}
      </p>
      {!terminal && (
        <p className="text-sm" style={{ color: 'var(--c-text-muted)' }}>
          Checking again every {String(d.poll_after_seconds ?? 0)} seconds.
        </p>
      )}

      {!terminal && d.last_poll_error != null && (
        <p
          role="alert"
          className="mt-3 rounded border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--c-warning)' }}
          data-testid="poll-error"
        >
          The last attempt to ask Jenkins failed: {pollError} What is shown here is as last known,
          not current. DCP keeps trying until the deadline.
        </p>
      )}

      {d.execution_status === 'SUCCEEDED' && (
        <div
          className="mt-3 rounded border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--c-ok)' }}
        >
          <p>
            Jenkins reported the pipeline <strong>{d.pipeline_result ?? 'SUCCESS'}</strong>.
          </p>
          <div className="mt-1">
            <HealthStatement deployment={d} />
          </div>
        </div>
      )}

      {failed && (
        <div
          role="alert"
          className="mt-3 rounded border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--c-critical)' }}
          data-testid="failure"
        >
          {reason !== '' && <p>{reason}</p>}
          {d.pipeline_result != null && (
            <p className="mt-1">
              Pipeline result: <strong>{d.pipeline_result}</strong>.
            </p>
          )}
          {unknownOutcome && (
            <p className="mt-1 font-medium">
              A build may be running, or may have run, in Jenkins. Check Jenkins before requesting
              this again.
            </p>
          )}
        </div>
      )}

      {d.execution_status === 'CANCELLED' && (
        <p
          className="mt-3 rounded border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Cancelled. No build of this deployment ran to completion.
        </p>
      )}

      {problem !== null && (
        <p role="alert" className="mt-3 text-sm" style={{ color: 'var(--c-critical)' }}>
          {problem}
        </p>
      )}

      {CANCELLABLE.includes(d.execution_status) && (
        <button
          type="button"
          disabled={busy || readOnly}
          onClick={() => void cancel()}
          className="mt-3 rounded border px-3 py-1 text-sm"
          style={{ borderColor: 'var(--c-border)' }}
        >
          Cancel this deployment
        </button>
      )}

      <dl className="mt-4 text-sm">
        <Row label="Application">
          <Link to={`/applications/${d.application_id}`} className="underline">
            {d.target.application_name}
          </Link>
        </Row>
        <Row label="Environment">{d.environment}</Row>
        <Row label="Pipeline">
          <code>{d.target.pipeline_reference}</code>
        </Row>
        <Row label="Ref">
          <code>{d.requested_ref}</code>
        </Row>
        <Row label="Commit">{d.commit_sha ?? 'Not reported yet'}</Row>
        <Row label="Requested by">{d.requested_by.display_name}</Row>
        <Row label="Reason">{d.requested_reason ?? 'None given'}</Row>
        <Row label="Approval">
          {d.approval_status === 'NOT_REQUIRED' ? 'Not required' : d.approval_status.toLowerCase()}
        </Row>
        <Row label="Build number">{d.external_execution_id ?? 'No build yet'}</Row>
        <Row label="Pipeline result">{d.pipeline_result ?? 'Not finished'}</Row>
        <Row label="Requested at">{when(d.created_at)}</Row>
        <Row label="Deadline">{when(d.deadline_at)}</Row>
        <Row label="Started">{when(d.started_at)}</Row>
        <Row label="Finished">{when(d.completed_at)}</Row>
        <Row label="Last asked Jenkins">{when(d.last_polled_at)}</Row>
      </dl>

      <div className="mt-4 text-sm" style={{ color: 'var(--c-text-muted)' }}>
        <p>Build logs are not available yet.</p>
        <p>Rollback is not available yet.</p>
      </div>
    </section>
  );
}
