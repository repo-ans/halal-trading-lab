// Formats a bar's epoch-seconds timestamp for display in the given IANA timezone --
// used as lightweight-charts' tickMarkFormatter/timeFormatter. Never shifts the
// stored timestamp: Intl.DateTimeFormat with `timeZone` does the conversion, the
// number itself (UTC epoch seconds) is untouched.
export function formatBarTime(epochSeconds: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(epochSeconds * 1000));
}

export function formatBarDate(epochSeconds: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(new Date(epochSeconds * 1000));
}

// "Tue 17 Nov 2026" -- for the chart page title. en-GB ordering gives day-before-
// month; the comma en-GB inserts after the weekday is stripped to match the exact
// requested format.
export function formatSessionDate(epochSeconds: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  })
    .format(new Date(epochSeconds * 1000))
    .replace(",", "");
}
