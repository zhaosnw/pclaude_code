"""Terminal activity heatmap — port of `heatmap.ts`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass
class DailyActivity:
    date: str  # YYYY-MM-DD
    message_count: int


@dataclass
class HeatmapOptions:
    terminal_width: int = 80
    show_month_labels: bool = True


def _to_date_string(d: date) -> str:
    return d.isoformat()


@dataclass
class _Percentiles:
    p25: int
    p50: int
    p75: int


def _calculate_percentiles(daily_activity: list[DailyActivity]) -> _Percentiles | None:
    counts = sorted([a.message_count for a in daily_activity if a.message_count > 0])
    if not counts:
        return None
    n = len(counts)
    return _Percentiles(
        p25=counts[int(n * 0.25)],
        p50=counts[int(n * 0.5)],
        p75=counts[int(n * 0.75)],
    )


def _intensity(count: int, p: _Percentiles | None) -> int:
    if count == 0 or not p:
        return 0
    if count >= p.p75:
        return 4
    if count >= p.p50:
        return 3
    if count >= p.p25:
        return 2
    return 1


CLAUDE_ORANGE = "\x1b[38;2;218;117;86m"
GRAY = "\x1b[90m"
RESET = "\x1b[0m"
BLUE = "\x1b[34m"


def _char(intensity: int) -> str:
    if intensity == 0:
        return GRAY + "·" + RESET
    ch = "░▒▓█"[intensity - 1]
    return CLAUDE_ORANGE + ch + RESET


def generate_heatmap(
    daily_activity: list[DailyActivity], options: HeatmapOptions | None = None
) -> str:
    opt = options or HeatmapOptions()
    day_w = 4
    avail = opt.terminal_width - day_w
    width = min(52, max(10, avail))

    activity_map = {a.date: a for a in daily_activity}
    p = _calculate_percentiles(daily_activity)

    today = datetime.now().date()

    def _js_get_day(d: date) -> int:
        return (d.weekday() + 1) % 7  # Sun=0 .. Sat=6 (JS convention)

    current_week_start = today - timedelta(days=_js_get_day(today))
    start = current_week_start - timedelta(weeks=width - 1)

    grid: list[list[str]] = [[""] * width for _ in range(7)]
    month_starts: list[tuple[int, int]] = []
    last_m = -1
    cur = start
    for week in range(width):
        for day in range(7):
            if cur > today:
                grid[day][week] = " "
            else:
                ds = _to_date_string(cur)
                act = activity_map.get(ds)
                if day == 0:
                    m = cur.month
                    if m != last_m:
                        month_starts.append((m - 1, week))
                        last_m = m
                grid[day][week] = _char(_intensity(act.message_count if act else 0, p))
            cur += timedelta(days=1)

    lines: list[str] = []
    if opt.show_month_labels:
        month_names = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        uniq_m = [month_names[m] for m, _ in month_starts]
        lw = max(1, width // max(len(uniq_m), 1))
        labels = "".join(m[:3].ljust(lw) for m in uniq_m)
        lines.append("    " + labels)
    day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    for day in range(7):
        label = day_labels[day].ljust(3) if day in (1, 3, 5) else "   "
        lines.append(label + " " + "".join(grid[day]))
    lines.append("")
    legend = "    Less " + " ".join(CLAUDE_ORANGE + c + RESET for c in "░▒▓█") + " More"
    lines.append(legend)
    return "\n".join(lines)
