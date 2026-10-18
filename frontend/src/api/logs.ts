/**
 * Log search client (API_CONTRACTS.md §19).
 *
 * Shapes are the generated ones; the transport is `session.request` and the
 * error ladder is `inventory.ts`'s `resultOf`, with **one addition that is the
 * point of this file**: a `503` or `504` is its own result, `unavailable`, and not
 * `unreachable`.
 *
 * Why it matters: `unreachable` means "we could not ask the backend", and
 * `ErrorState` says so. Here the backend *answered* — that the log store could not.
 * Rendering that as a backend outage would send the operator after the wrong
 * system, and rendering it as an empty list would be the dangerous lie the
 * contract is written against ("Loki down … never 'no logs found'").
 *
 * **No query ever leaves this file.** The request is the structured filter of
 * §19.1; there is deliberately no parameter here that could carry LogQL (AD-05).
 */

import type { components } from '@/api/schema';
import { resultOf, type Result } from '@/api/inventory';
import { request } from '@/api/session';

type Schemas = components['schemas'];

export type LogQueryRequest = Schemas['LogQueryRequest'];
export type LogSearchResponse = Schemas['LogSearchResponse'];
export type LogEntry = Schemas['LogEntry'];
export type LogSources = Schemas['LogSources'];
export type ServerIngestion = Schemas['ServerIngestion'];
export type IngestionState = ServerIngestion['state'];
export type SourceType = LogEntry['source_type'];
export type LogLevel = LogEntry['level'];
export type PresetId = NonNullable<LogQueryRequest['pattern_preset']>;

export type UnavailableCode = 'not_configured' | 'provider_unavailable' | 'provider_timeout';

export interface LogUnavailable {
  kind: 'unavailable';
  code: UnavailableCode;
  message: string;
}

export type LogResult<T> = Result<T> | LogUnavailable;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

async function logCall<T>(
  path: string,
  init: RequestInit,
  isExpected: (value: unknown) => boolean,
): Promise<LogResult<T>> {
  try {
    const response = await request(path, init);
    if (response.status === 503 || response.status === 504) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        // An unreadable body still means "the store could not answer".
      }
      const error = isRecord(body) && isRecord(body.error) ? body.error : {};
      const code: UnavailableCode =
        error.code === 'not_configured' || error.code === 'provider_timeout'
          ? error.code
          : 'provider_unavailable';
      return {
        kind: 'unavailable',
        code,
        message: typeof error.message === 'string' ? error.message : 'Log search is unavailable.',
      };
    }
    return await resultOf<T>(response, isExpected);
  } catch (error) {
    return {
      kind: 'unreachable',
      reason: error instanceof Error ? error.message : 'Request failed',
    };
  }
}

export function searchLogs(body: LogQueryRequest): Promise<LogResult<LogSearchResponse>> {
  return logCall(
    '/logs/query',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    (value) => isRecord(value) && Array.isArray(value.entries) && isRecord(value.page),
  );
}

export function readLogSources(): Promise<LogResult<LogSources>> {
  return logCall(
    '/logs/sources',
    {},
    (value) => isRecord(value) && Array.isArray(value.source_types) && Array.isArray(value.presets),
  );
}
