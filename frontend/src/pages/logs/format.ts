/** A timestamp for a log row: local date and time to the millisecond. UTC is in the `title`. */
export function stampOf(iso: string): string {
  const moment = new Date(iso);
  const ms = String(moment.getMilliseconds()).padStart(3, '0');
  return `${moment.toLocaleDateString()} ${moment.toLocaleTimeString([], { hour12: false })}.${ms}`;
}
