/** Times and dates as people read them, in the reader's own time zone. */

const dateFormat = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const timeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const dayTimeFormat = new Intl.DateTimeFormat("en-GB", { weekday: "long", hour: "2-digit", minute: "2-digit" });

/** "2026-11-27" (a calendar date) → "Fri 27 Nov". */
export function formatDate(isoDate: string): string {
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  return dateFormat.format(new Date(y!, (m ?? 1) - 1, d ?? 1));
}

/** A moment → "Fri 27 Nov, 09:00". */
export function formatMoment(iso: string): string {
  const at = new Date(iso);
  return `${dateFormat.format(at)}, ${timeFormat.format(at)}`;
}

/** A moment → "Monday 08:00", as in "Next report Monday 08:00". */
export function formatDayTime(iso: string): string {
  return dayTimeFormat.format(new Date(iso)).replace(" at ", " ");
}

/** Today's calendar date in the reader's time zone, as "YYYY-MM-DD". */
export function todayIso(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function plural(count: number, one: string, many: string = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** Monday 00:00 of the reader's current week, and the Monday after. */
export function thisWeek(now: Date = new Date()): { start: Date; end: Date } {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  return { start, end };
}

/** Whether a calendar date ("YYYY-MM-DD") or a moment falls in the reader's current week. */
export function inThisWeek(value: string, now: Date = new Date()): boolean {
  const { start, end } = thisWeek(now);
  const at =
    value.length === 10
      ? new Date(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10)))
      : new Date(value);
  return at >= start && at < end;
}
