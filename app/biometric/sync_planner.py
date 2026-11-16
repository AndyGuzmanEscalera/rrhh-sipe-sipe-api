from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class SyncRange:
    start: date
    end: date


def days_between(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


class SyncPlanner:
    def plan(self, start, end, coverage, today, *, force=False):
        if start > end or end > today or (end-start).days > 366:
            raise ValueError('Ordered inclusive range, maximum 367 days, no future days')
        selected = [day for day in days_between(start, end)
                    if force or day >= today or coverage.get(day) != 'COMPLETE']
        ranges = []
        for day in selected:
            if ranges and day == ranges[-1].end + timedelta(days=1):
                ranges[-1] = SyncRange(ranges[-1].start, day)
            else:
                ranges.append(SyncRange(day, day))
        return tuple(ranges)
