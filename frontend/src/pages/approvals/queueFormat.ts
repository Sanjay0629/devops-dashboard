/** Small pure helpers for the approver queue, kept out of the component file (fast refresh). */

import type { ApprovalItem } from '@/api/approvals';

/** How long is left, in words. Never negative: a lapsed window says so. */
export function timeLeft(expiresAt: string, now: number): string {
  const seconds = Math.floor((new Date(expiresAt).getTime() - now) / 1000);
  if (seconds <= 0) return 'expired';
  if (seconds < 60) return 'less than a minute left';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 120) return `${String(minutes)} min left`;
  return `${String(Math.floor(minutes / 60))} h left`;
}

/** The control is advice-disabled when this is your own request and the policy forbids it. */
export function blockedAsOwn(item: ApprovalItem): boolean {
  return item.self_requested && !item.self_approval_allowed;
}
