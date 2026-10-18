/**
 * The log search form's state, and the one place it becomes a §19.1 body.
 *
 * Separate from the page so the mapping is testable without rendering, and so the
 * page file exports only a component (fast refresh).
 */

import type { LogLevel, LogQueryRequest, PresetId, SourceType } from '@/api/logs';
import { DEFAULT_LOG_RANGE, logRangeById } from '@/pages/logs/prefill';

export const ENVIRONMENTS = ['development', 'staging', 'production', 'other'] as const;
export type Environment = (typeof ENVIRONMENTS)[number];
export const MAX_SERVERS = 50;
export const LIMITS = [50, 100, 200, 500, 1000] as const;

export interface Form {
  rangeId: string;
  servers: Record<string, string>;
  environment: '' | Environment;
  applicationId: string;
  service: string;
  sourceType: '' | SourceType;
  levels: LogLevel[];
  contains: string;
  preset: '' | PresetId;
  limit: number;
}

export const BLANK: Form = {
  rangeId: DEFAULT_LOG_RANGE,
  servers: {},
  environment: '',
  applicationId: '',
  service: '',
  sourceType: '',
  levels: [],
  contains: '',
  preset: '',
  limit: 200,
};

export const hasTarget = (form: Form): boolean =>
  Object.keys(form.servers).length > 0 || form.environment !== '' || form.applicationId !== '';

/** The §19.1 body for a form. Empty fields are left out, not sent as empty strings. */
export function bodyOf(form: Form, now: Date = new Date()): LogQueryRequest {
  const range = logRangeById(form.rangeId);
  const ids = Object.keys(form.servers);
  return {
    from: new Date(now.getTime() - range.seconds * 1000).toISOString(),
    // A minute of headroom, so a line stamped a moment ahead by a skewed clock shows.
    to: new Date(now.getTime() + 60_000).toISOString(),
    direction: 'backward',
    limit: form.limit,
    ...(ids.length > 0 ? { server_ids: ids } : {}),
    ...(form.environment !== '' ? { environment: form.environment } : {}),
    ...(form.applicationId !== '' ? { application_id: form.applicationId } : {}),
    ...(form.service.trim() !== '' ? { service: form.service.trim() } : {}),
    ...(form.sourceType !== '' ? { source_type: form.sourceType } : {}),
    ...(form.levels.length > 0 ? { level: form.levels } : {}),
    ...(form.contains !== '' ? { contains: form.contains } : {}),
    ...(form.preset !== '' ? { pattern_preset: form.preset } : {}),
  };
}
