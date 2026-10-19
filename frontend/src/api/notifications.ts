/**
 * Notification client (API_CONTRACTS.md §26.8 - §26.10, §20.6).
 *
 * A channel is described by a **reference** to its destination, never the destination: for a
 * webhook channel it is `env://NAME`, for email the (non-secret) address. Nothing here ever
 * receives a resolved URL, and the create form refuses to let one be typed in the first place
 * (the backend refuses it anyway and does not echo it).
 *
 * Reason codes (`last_error`) are a closed set from the backend, shown as words; an unknown
 * code is shown as itself rather than hidden.
 */

import type { components } from '@/api/schema';
import { call, type Result } from '@/api/inventory';

type Schemas = components['schemas'];

export type Channel = Schemas['ChannelOut'];
export type ChannelList = Schemas['ChannelList'];
export type ChannelType = Channel['channel_type'];
export type ChannelStatus = Channel['status'];
export type Delivery = Schemas['DeliveryOut'];
export type RoutingRules = Schemas['RoutingRules'];

export interface ChannelInput {
  name: string;
  channel_type: ChannelType;
  destination_reference: string;
  routing_rules: RoutingRules;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

const json = (method: string, body: object): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export function listChannels(): Promise<Result<ChannelList>> {
  return call<ChannelList>(
    '/notification-channels',
    {},
    (value) => isRecord(value) && Array.isArray(value.items),
  );
}

export function createChannel(input: ChannelInput): Promise<Result<Channel>> {
  return call<Channel>(
    '/notification-channels',
    json('POST', input),
    (value) => isRecord(value) && typeof value.id === 'string',
  );
}

/** The type cannot change; only these fields can. */
export function updateChannel(
  id: string,
  changes: Partial<Pick<ChannelInput, 'name' | 'destination_reference' | 'routing_rules'>> & {
    status?: ChannelStatus;
  },
): Promise<Result<Channel>> {
  return call<Channel>(
    `/notification-channels/${encodeURIComponent(id)}`,
    json('PATCH', changes),
    (value) => isRecord(value) && typeof value.id === 'string',
  );
}

export function retryDelivery(alertId: string, deliveryId: string): Promise<Result<Delivery>> {
  return call<Delivery>(
    `/alerts/${encodeURIComponent(alertId)}/notifications/${encodeURIComponent(deliveryId)}/retry`,
    { method: 'POST' },
    (value) => isRecord(value) && typeof value.delivery_id === 'string',
  );
}

/** Closed reason codes from the backend, as words. */
const REASONS: Record<string, string> = {
  timeout: 'the destination did not answer in time',
  connect_failed: 'could not connect to the destination',
  dns_failed: 'the destination name did not resolve',
  destination_refused: 'the destination is not an address DCP may connect to',
  url_not_allowed: 'the destination is not on the allowed list',
  redirect_refused: 'the destination answered with a redirect, which is not followed',
  secret_unresolvable: 'the destination secret could not be read',
  channel_disabled: 'the channel was disabled',
  channel_rules_invalid: 'the channel’s routing rules are not valid',
  alert_missing: 'the alert is no longer stored',
  smtp_busy: 'the mail server asked to try again later',
  smtp_rejected: 'the mail server refused the message',
  smtp_auth_failed: 'the mail server refused the login',
  smtp_tls_unavailable: 'the mail server does not offer TLS',
  smtp_not_configured: 'outgoing mail is not configured',
  tls_failed: 'the destination’s certificate could not be verified',
};

export function reasonText(code: string | null | undefined): string {
  if (code == null || code === '') return '';
  const http = /^http_(\d{3})$/.exec(code);
  if (http !== null) return `the destination answered HTTP ${http[1] ?? ''}`;
  return REASONS[code] ?? code;
}

/** Words for a rule set. An empty one is a catch-all, and is called that. */
export function describeRules(rules: Record<string, unknown>): string {
  const parts = (['severity', 'environment', 'target_type'] as const)
    .map((key) => {
      const value = rules[key];
      return Array.isArray(value) && value.length > 0
        ? `${key.replace('_', ' ')}: ${value.map(String).join(', ')}`
        : null;
    })
    .filter((part): part is string => part !== null);
  return parts.length === 0 ? 'Every alert' : parts.join(' · ');
}
