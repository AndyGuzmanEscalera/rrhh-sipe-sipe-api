"""One-time, manual RAW biometric history backfill orchestrator.

This module deliberately does not expose an HTTP endpoint or schedule work.  It
splits a requested period into calendar months and delegates every monthly
download to the existing durable biometric sync workflow.
"""
import argparse
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

from sqlalchemy import select

from app.biometric.models import Sync
from app.biometric.worker import enqueue, run_once
from app.core.clock import local_today
from app.core.config import get_settings
from app.db.session import get_engine, session_factory


DEFAULT_FROM = date(2026, 1, 1)


@dataclass(frozen=True)
class MonthlyRange:
    start: date
    end: date

    @property
    def label(self) -> str:
        return self.start.strftime('%Y-%m')


@dataclass(frozen=True)
class BackfillSummary:
    start: date
    end: date
    months_processed: int
    months_completed: int
    received: int
    inserted: int
    duplicates: int
    errors: int


class BackfillFailed(RuntimeError):
    """A monthly sync failed; previous monthly commits remain intact."""


def monthly_ranges(start: date, end: date) -> Iterable[MonthlyRange]:
    """Yield inclusive calendar-month ranges clipped to ``start`` and ``end``."""
    if start > end:
        raise ValueError('--from no puede ser posterior a --to.')
    current = start
    while current <= end:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        month_end = date.fromordinal(next_month.toordinal() - 1)
        yield MonthlyRange(current, min(month_end, end))
        current = next_month


def run_backfill(start: date, end: date, *, factory=session_factory, bind=None,
                 service_factory=None, output: Callable[[str], None] = print) -> BackfillSummary:
    """Synchronize one complete calendar range at a time, sequentially.

    The existing ``enqueue``/``run_once`` pair owns API calls, RAW normalization,
    SHA-256 record IDs, deduplication, inserts, per-range transactions, sync
    history, error persistence, and coverage. ``force=True`` intentionally
    downloads the requested historical month even if coverage was previously
    marked complete; inserts remain idempotent through ``record_id``.
    """
    settings = get_settings()
    ranges = tuple(monthly_ranges(start, end))
    completed = received = inserted = duplicates = errors = 0
    execution_bind = bind if bind is not None else get_engine()

    for interval in ranges:
        output(f'[{interval.label}] {interval.start.isoformat()} -> {interval.end.isoformat()}')
        with factory() as db:
            job = enqueue(db, settings.biometric_device_id, interval.start, interval.end, force=True)
            job_id = job.id

        kwargs = dict(factory=factory, bind=execution_bind)
        if service_factory is not None:
            kwargs['service_factory'] = service_factory
        run_once(**kwargs)

        with factory() as db:
            job = db.scalar(select(Sync).where(Sync.id == job_id))
            if job is None:
                raise BackfillFailed(f'No se encontró el lote {interval.label}.')
            received += job.records_received
            inserted += job.records_inserted
            duplicates += job.records_duplicated
            errors += job.error_records
            output(f'received: {job.records_received}')
            output(f'inserted: {job.records_inserted}')
            output(f'duplicates: {job.records_duplicated}')
            output(f'errors: {job.error_records}')
            output(f'status: {job.status}')
            if job.status != 'completed':
                detail = job.error_code or 'biometric_sync_failed'
                raise BackfillFailed(f'Falló el lote {interval.label}: {detail}.')
        completed += 1

    return BackfillSummary(start, end, len(ranges), completed, received, inserted, duplicates, errors)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use YYYY-MM-DD.') from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='start', type=_parse_date, default=DEFAULT_FROM,
                        help='Inicio inclusivo (YYYY-MM-DD); por defecto 2026-01-01.')
    parser.add_argument('--to', dest='end', type=_parse_date, default=None,
                        help='Fin inclusivo (YYYY-MM-DD); por defecto hoy en America/La_Paz.')
    args = parser.parse_args(argv)
    end = args.end or local_today()
    try:
        summary = run_backfill(args.start, end)
    except (BackfillFailed, ValueError) as exc:
        print(f'BACKFILL FALLIDO: {exc}')
        return 1

    print('BACKFILL COMPLETADO')
    print(f'Periodo: {summary.start.isoformat()} -> {summary.end.isoformat()}')
    print(f'Meses procesados: {summary.months_processed}')
    print(f'Meses completados: {summary.months_completed}')
    print(f'Recibidos: {summary.received}')
    print(f'Nuevos: {summary.inserted}')
    print(f'Duplicados: {summary.duplicates}')
    print(f'Errores: {summary.errors}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
