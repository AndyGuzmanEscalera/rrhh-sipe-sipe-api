"""Pure, conservative attendance evaluator. No SQL, hardware or record mutations.

Reglas de la version 2 (documentadas y aplicadas de forma uniforme):

1. Las marcaciones RAW son evidencia inmutable: nunca se editan, no se
   reordenan entre si y no se descartan.
2. Las marcaciones principales se eligen de forma determinista: la salida
   principal es la PRIMERA marcacion valida desde la hora final esperada;
   entrada, salida y retorno son las siguientes marcas en orden cronologico.
   Las demas se conservan como marcaciones adicionales.
3. La jornada en curso (dia actual cuya hora local todavia no llega a la salida
   final esperada) se informa EN CURSO: se muestran las marcaciones ya
   clasificables y se dejan en blanco las que todavia no existen.
4. La tolerancia decide SI una desviacion cuenta, no cuanto se descuenta: dentro
   de la tolerancia el valor es 0; fuera de la tolerancia cuenta la desviacion
   real respecto de la hora esperada.
5. Los minutos son enteros (piso de los segundos exactos). Los segundos
   exactos se conservan para auditoria.
6. La ausencia se calcula como el horario de trabajo esperado que no esta
   cubierto por los intervalos de presencia. El descanso normal entre bloques no
   cuenta como ausencia.
7. Las horas extra son informativas: tiempo registrado despues de la hora
   final esperada. No implican autorizacion ni pago.
"""
from collections import Counter
from datetime import datetime, timedelta

from app.attendance.evaluation_models import (
    CalendarDay, DailyEvaluation, DayStatus, EventAssessment, EventKind,
    Incident, IncidentType, IntermediateInterval, PeriodSummary, Severity, Suggestion,
)


def _minutes(seconds: int) -> int:
    """Minutos enteros a partir de segundos exactos (piso, nunca decimal)."""
    return seconds // 60


def _deviation_after(expected_at: datetime, recorded_at: datetime, tolerance: int) -> int:
    """Segundos de retraso: la tolerancia perdona, el exceso se cuenta.

    Dentro de la tolerancia el valor es 0; fuera de ella cuenta el exceso
    sobre la tolerancia (los minutos de trabajo realmente perdidos).
    """
    delta = int((recorded_at - expected_at).total_seconds())
    return max(0, delta - tolerance)


def _deviation_before(expected_at: datetime, recorded_at: datetime, tolerance: int) -> int:
    """Segundos de salida anticipada: misma regla que el retraso.

    Salir despues de la hora esperada nunca penaliza; dentro de la tolerancia
    tampoco; fuera de ella cuenta el exceso sobre la tolerancia.
    """
    delta = int((expected_at - recorded_at).total_seconds())
    if delta <= 0:
        return 0
    return max(0, delta - tolerance)


def _event_metrics(event, mark, parameters):
    """Comparison directional; seconds are authoritative, no rounding policy invented."""
    tolerance = parameters.tolerance(event.kind)
    if event.kind.is_entry:
        late = _deviation_after(event.expected_at, mark.timestamp, tolerance)
        code = IncidentType.LATE_RETURN if event.kind == EventKind.AFTERNOON_ENTRY else IncidentType.LATE
        return {"status": code.value if late else "NORMAL", "late_seconds": late,
                "gross_early_exit_seconds": 0, "early_exit_seconds": 0,
                "exit_tolerance_policy_pending": False}
    gross = max(0, int((event.expected_at - mark.timestamp).total_seconds()))
    return {"status": IncidentType.EARLY_EXIT.value if gross else "NORMAL", "late_seconds": 0,
            "gross_early_exit_seconds": gross, "early_exit_seconds": None if gross == 0 else gross,
            "exit_tolerance_policy_pending": False}


def _incident(kind, severity, description, records=(), seconds=None, event=None, suggestion=None):
    return Incident(type=kind, severity=severity, description=description, seconds=seconds,
                    minutes=seconds / 60 if seconds is not None else None,
                    record_ids=tuple(mark.record_id for mark in records), event_kind=event, suggestion=suggestion)


def _candidate_slots(mark, events):
    return tuple(i for i, event in enumerate(events) if event.window_start <= mark.timestamp <= event.window_end)


def _units_and_duplicates(marks, events, window):
    """Flag repetitions; bounded span from the first mark, never transitive chaining.

    Matching device/user/raw codes and candidate windows is only a compatibility
    signal, never an interpretation of status/punch. A different expected event
    window breaks the group, so a scheduled break isn't treated as a duplicate.
    """
    units = []
    for mark in marks:
        if units and window > 0:
            anchor = units[-1][0]
            compatible = ((mark.device_id, mark.user_id, mark.status, mark.punch) ==
                          (anchor.device_id, anchor.user_id, anchor.status, anchor.punch))
            same_slots = _candidate_slots(mark, events) == _candidate_slots(anchor, events)
            spans_events = sum(anchor.timestamp <= e.expected_at <= mark.timestamp for e in events) > 1
            if compatible and same_slots and not spans_events and (mark.timestamp - anchor.timestamp).total_seconds() <= window:
                units[-1].append(mark)
                continue
        units.append([mark])
    return units


def select_principal_marks(units, events):
    """Elige las marcaciones principales de forma determinista y explicable.

    * La salida principal es la PRIMERA marca valida desde la hora final
      esperada, aunque forme parte de un grupo de marcaciones repetidas: las
      posteriores son marcaciones adicionales, nunca la salida.
    * Entrada, salida y retorno son las siguientes marcas en orden cronologico.
    * Los grupos de duplicados no se asignan como marca principal.

    Devuelve (selected, additional_units).
    """
    expected = {event.kind: event for event in events}
    selected: dict[EventKind, object] = {}
    used: set[int] = set()

    final_event = expected.get(EventKind.FINAL_EXIT)
    if final_event is not None:
        for index, unit in enumerate(units):
            for mark in unit:
                if mark.timestamp >= final_event.expected_at:
                    selected[EventKind.FINAL_EXIT] = mark
                    used.add(index)
                    break
            if EventKind.FINAL_EXIT in selected:
                break

    candidates = [index for index, unit in enumerate(units)
                  if index not in used and len(unit) == 1
                  and (final_event is None or unit[0].timestamp < final_event.expected_at)]
    present = [event.kind for event in events if event.kind is not EventKind.FINAL_EXIT]
    for position, kind in enumerate(present):
        if position >= len(candidates):
            break
        index = candidates[position]
        selected[kind] = units[index][0]
        used.add(index)

    additional = [unit for index, unit in enumerate(units) if index not in used]
    return selected, additional


def _expected_work_intervals(events):
    """Bloques de jornada esperada: (inicio, fin) de cada par de eventos."""
    intervals = []
    pending = None
    for event in events:
        if pending is None:
            pending = event.expected_at
        else:
            intervals.append((pending, event.expected_at))
            pending = None
    return intervals


def _presence_edge(mark, event, parameters):
    """Borde de presencia: la tolerancia perdona la desviacion si cabe entera.

    Si la desviacion cabe en la tolerancia, el funcionario se considera
    presente desde (o hasta) la hora esperada. Si la excede, no hay perdon: el
    tiempo que falta es real y cuenta como ausencia.
    """
    deviation = int((mark.timestamp - event.expected_at).total_seconds())
    if abs(deviation) <= parameters.tolerance(event.kind):
        return event.expected_at
    return mark.timestamp


def _presence_intervals(selected, events, parameters):
    """Intervalos de presencia declarados por las marcaciones principales."""
    expected = {event.kind: event for event in events}
    intervals = []
    entry = selected.get(EventKind.MORNING_ENTRY)
    exit_ = selected.get(EventKind.MORNING_EXIT)
    back = selected.get(EventKind.AFTERNOON_ENTRY)
    final = selected.get(EventKind.FINAL_EXIT)
    if entry is not None and exit_ is not None:
        start = _presence_edge(entry, expected[EventKind.MORNING_ENTRY], parameters)
        end = _presence_edge(exit_, expected[EventKind.MORNING_EXIT], parameters)
        intervals.append((start, end))
    if back is not None and final is not None:
        start = _presence_edge(back, expected[EventKind.AFTERNOON_ENTRY], parameters)
        end = _presence_edge(final, expected[EventKind.FINAL_EXIT], parameters)
        intervals.append((start, end))
    return intervals


def _absence_seconds(expected_intervals, presence_intervals):
    """Minutos de jornada esperada sin cobertura de presencia.

    Devuelve None cuando falta alguna marcacion para cubrir un bloque: no se
    inventa un valor que pueda subestimar la ausencia.
    """
    if not expected_intervals:
        return None
    total = 0
    for start, end in expected_intervals:
        covered = 0
        for p_start, p_end in presence_intervals:
            overlap_start = max(start, p_start)
            overlap_end = min(end, p_end)
            if overlap_end > overlap_start:
                covered += int((overlap_end - overlap_start).total_seconds())
        total += max(0, int((end - start).total_seconds()) - covered)
    return total


def evaluate_day(employee_id, day, schedule, parameters, records, coverage=None, now=None):
    marks = tuple(sorted(records, key=lambda mark: (mark.timestamp, mark.record_id)))
    if any(mark.funcionario_id != employee_id or mark.timestamp.date() != day for mark in marks):
        raise ValueError("Records must belong to the evaluated employee and local day")
    if len({mark.record_id for mark in marks}) != len(marks):
        raise ValueError("Repeated input record identity")
    if schedule is not None and schedule.day != day:
        raise ValueError("Schedule belongs to another day")
    base = {"date": day, "original_records": marks, "schedule": schedule, "coverage": coverage}
    if not marks:
        # Cero marcaciones nunca es ausencia ni observacion.
        reason = "no_records_not_proof_of_absence" if coverage is not False else "outside_confirmed_coverage"
        return DailyEvaluation(**base, status=DayStatus.NO_DATA, reason=reason,
                               pending_configuration=() if coverage is not False else ("biometric_coverage",))
    if schedule is None:
        # Hay evidencia pero no se puede clasificar sin jornada confirmada.
        return DailyEvaluation(**base, status=DayStatus.ANOMALY, reason="missing_shift_assignment",
                               pending_configuration=("employee_shift_source",))
    if parameters is None:
        return DailyEvaluation(**base, status=DayStatus.ANOMALY, reason="missing_parameters",
                               pending_configuration=("attendance_parameters",))
    if not schedule.events:
        return DailyEvaluation(**base, status=DayStatus.NO_DATA, reason="no_expected_work_session")

    events = schedule.events
    expected = {event.kind: event for event in events}
    units = _units_and_duplicates(marks, events, parameters.duplicate_window)
    incidents: list[Incident] = []
    suggestions: list[Suggestion] = []

    selected, additional = select_principal_marks(units, events)
    final_event = expected.get(EventKind.FINAL_EXIT)
    final_mark = selected.get(EventKind.FINAL_EXIT)
    # La jornada sigue abierta si la hora local todavia no llega a la salida esperada.
    in_progress = bool(now is not None and final_event is not None and now < final_event.expected_at)

    for unit in units:
        if len(unit) > 1:
            if final_mark is not None and any(mark.record_id == final_mark.record_id for mark in unit):
                # Varias marcaciones en la salida: la primera es la principal.
                incidents.append(_incident(IncidentType.MULTIPLE_MARKS, Severity.INFO,
                    "Múltiples marcaciones registradas en salida.", unit))
                continue
            compatible_events = [event for event in events if all(
                event.window_start <= mark.timestamp <= event.window_end for mark in unit)]
            principal = min(unit, key=lambda mark: (min(abs((mark.timestamp - event.expected_at).total_seconds())
                            for event in compatible_events), mark.record_id)) if len(compatible_events) == 1 else None
            suggestion = Suggestion(description="Revisar las marcas cercanas; la selección principal es solo una sugerencia.",
                                    record_ids=tuple(mark.record_id for mark in unit),
                                    suggested_record_id=principal.record_id if principal else None)
            suggestions.append(suggestion)
            incidents.append(_incident(IncidentType.POSSIBLE_DUPLICATE, Severity.OBSERVATION,
                f"Se detectaron {len(unit)} marcaciones cercanas entre {unit[0].timestamp:%H:%M:%S} y {unit[-1].timestamp:%H:%M:%S}.",
                unit, suggestion=suggestion))

    assessments = []
    for event in events:
        mark = selected.get(event.kind)
        fields = {}
        if mark is not None:
            fields = _event_metrics(event, mark, parameters)
        assessments.append(EventAssessment(expected=event, record_id=mark.record_id if mark else None,
            recorded_at=mark.timestamp if mark else None,
            candidate_record_ids=(mark.record_id,) if mark else (),
            association="ASSIGNED" if mark else ("PENDING" if in_progress else "MISSING"),
            **fields))

    def _missing_label(kind):
        return {EventKind.MORNING_ENTRY: "entrada", EventKind.MORNING_EXIT: "salida",
                EventKind.AFTERNOON_ENTRY: "retorno", EventKind.FINAL_EXIT: "salida final"}[kind]

    for event in events:
        if event.kind in selected:
            continue
        if in_progress:
            # Todavia no corresponde: no es una falta, solo una marca pendiente.
            continue
        incidents.append(_incident(IncidentType.MISSING_MARK, Severity.ANOMALY,
            f"Falta marcación de {_missing_label(event.kind)}.", event=event.kind))

    # Marcaciones adicionales: se conservan y se informan, no invalidan el dia.
    additional_marks = [mark for unit in additional for mark in unit]
    if additional_marks:
        incidents.append(_incident(IncidentType.EXTRA_MARK, Severity.INFO,
            "Marcación adicional conservada sin evento principal asignado.", additional_marks))
    after_final = [mark for mark in additional_marks
                   if final_event is not None and mark.timestamp >= final_event.expected_at]
    if len(after_final) > 1:
        incidents.append(_incident(IncidentType.MULTIPLE_MARKS, Severity.INFO,
            "Múltiples marcaciones registradas en salida.", after_final))

    late_seconds = 0
    for event in events:
        mark = selected.get(event.kind)
        if mark is None or not event.kind.is_entry:
            continue
        tolerance = parameters.tolerance(event.kind)
        deviation = _deviation_after(event.expected_at, mark.timestamp, tolerance)
        if not deviation:
            continue
        if event.kind is EventKind.AFTERNOON_ENTRY:
            # El retraso del retorno se informa aparte: no suma al atraso de entrada.
            incidents.append(_incident(IncidentType.LATE_RETURN, Severity.OBSERVATION,
                f"Retorno posterior a la tolerancia: {_minutes(deviation)} min.",
                (mark,), deviation, event.kind))
            continue
        late_seconds += deviation
        incidents.append(_incident(IncidentType.LATE, Severity.OBSERVATION,
            f"Atraso de {_minutes(deviation)} min.", (mark,), deviation, event.kind))

    early_seconds = None
    gross_early_seconds = None
    final_mark = selected.get(EventKind.FINAL_EXIT)
    if final_mark is not None and final_event is not None:
        tolerance = parameters.tolerance(EventKind.FINAL_EXIT)
        gross_early_seconds = max(0, int((final_event.expected_at - final_mark.timestamp).total_seconds()))
        early_seconds = _deviation_before(final_event.expected_at, final_mark.timestamp, tolerance)
        if early_seconds:
            incidents.append(_incident(IncidentType.EARLY_EXIT, Severity.OBSERVATION,
                f"Salida anticipada de {_minutes(early_seconds)} min.", (final_mark,), early_seconds,
                EventKind.FINAL_EXIT))

    overtime_seconds = None
    if final_mark is not None and final_event is not None:
        overtime_seconds = max(0, int((final_mark.timestamp - final_event.expected_at).total_seconds()))

    expected_intervals = _expected_work_intervals(events)
    presence_intervals = _presence_intervals(selected, events, parameters)
    absence_seconds = None
    if presence_intervals and len(presence_intervals) == len(expected_intervals):
        absence_seconds = _absence_seconds(expected_intervals, presence_intervals)
        if absence_seconds:
            incidents.append(_incident(IncidentType.ABSENCE, Severity.OBSERVATION,
                f"Ausencia durante jornada: {_minutes(absence_seconds)} min.", (), absence_seconds))

    severity = {incident.severity for incident in incidents}
    if in_progress:
        status = DayStatus.IN_PROGRESS
    elif Severity.ANOMALY in severity:
        status = DayStatus.ANOMALY
    elif Severity.OBSERVATION in severity:
        status = DayStatus.OBSERVATION
    else:
        status = DayStatus.NORMAL

    return DailyEvaluation(**base, status=status, events=tuple(assessments),
        morning_entry=selected[EventKind.MORNING_ENTRY].timestamp if EventKind.MORNING_ENTRY in selected else None,
        morning_exit=selected[EventKind.MORNING_EXIT].timestamp if EventKind.MORNING_EXIT in selected else None,
        afternoon_entry=selected[EventKind.AFTERNOON_ENTRY].timestamp if EventKind.AFTERNOON_ENTRY in selected else None,
        final_exit=selected[EventKind.FINAL_EXIT].timestamp if EventKind.FINAL_EXIT in selected else None,
        late_seconds=late_seconds, late_minutes=_minutes(late_seconds),
        gross_early_exit_seconds=gross_early_seconds,
        gross_early_exit_minutes=_minutes(gross_early_seconds) if gross_early_seconds is not None else None,
        early_exit_seconds=early_seconds,
        early_exit_minutes=_minutes(early_seconds) if early_seconds is not None else None,
        observed_late_seconds=late_seconds,
        observed_gross_early_exit_seconds=gross_early_seconds or 0,
        absence_seconds=absence_seconds,
        absence_minutes=_minutes(absence_seconds) if absence_seconds is not None else None,
        overtime_seconds=overtime_seconds,
        overtime_minutes=_minutes(overtime_seconds) if overtime_seconds is not None else None,
        additional_records=len(additional_marks),
        reconstruction_complete=all(event.association == "ASSIGNED" for event in assessments) and not additional_marks,
        reason=None,
        pending_configuration=(), incidents=tuple(incidents), suggestions=tuple(suggestions),
        intermediate_intervals=())


def period_summary(days):
    counts = Counter(day.status for day in days)
    evaluated = [day for day in days if day.status is not DayStatus.NO_DATA]

    def total(field):
        # Los dias evaluados suman su valor; los no computables no se inventan.
        values = [getattr(day, field) for day in evaluated if getattr(day, field) is not None]
        return sum(values)

    late, gross, early = (total(field) for field in
                          ("late_seconds", "gross_early_exit_seconds", "early_exit_seconds"))
    absence, overtime = (total(field) for field in ("absence_seconds", "overtime_seconds"))
    complete = all(
        getattr(day, field) is not None
        for day in evaluated
        for field in ("late_seconds", "early_exit_seconds", "absence_seconds", "overtime_seconds")
    )
    return PeriodSummary(days_evaluated=len(evaluated),
        normal_days=counts[DayStatus.NORMAL], observation_days=counts[DayStatus.OBSERVATION],
        anomaly_days=counts[DayStatus.ANOMALY], no_data_days=counts[DayStatus.NO_DATA],
        in_progress_days=counts[DayStatus.IN_PROGRESS],
        total_records=sum(len(day.original_records) for day in days),
        total_incidents=sum(len(day.incidents) for day in days), total_late_seconds=late,
        total_late_minutes=_minutes(late),
        total_gross_early_exit_seconds=gross,
        total_gross_early_exit_minutes=_minutes(gross),
        total_early_exit_seconds=early,
        total_early_exit_minutes=_minutes(early),
        total_absence_seconds=absence,
        total_absence_minutes=_minutes(absence),
        total_overtime_seconds=overtime,
        total_overtime_minutes=_minutes(overtime),
        observed_late_seconds=sum(day.observed_late_seconds for day in days),
        observed_gross_early_exit_seconds=sum(day.observed_gross_early_exit_seconds for day in days),
        totals_complete=complete and bool(evaluated))


def calendar_days(days):
    return tuple(CalendarDay(date=day.date, status=day.status, total_records=len(day.original_records),
        first_mark=day.original_records[0].timestamp if day.original_records else None,
        last_mark=day.original_records[-1].timestamp if day.original_records else None,
        incident_count=len(day.incidents)) for day in days)


def evaluate_period(employee_id, period, schedules, parameters, records, coverage=None, now=None):
    by_day = {}
    for mark in records:
        if mark.funcionario_id != employee_id or not period.start_date <= mark.timestamp.date() < period.end_exclusive:
            raise ValueError("Records outside evaluated employee or period")
        by_day.setdefault(mark.timestamp.date(), []).append(mark)
    days = []
    day = period.start_date
    while day < period.end_exclusive:
        days.append(evaluate_day(employee_id, day, schedules.get(day), parameters, by_day.get(day, ()),
                                  (coverage or {}).get(day), now))
        day += timedelta(days=1)
    return tuple(days)
