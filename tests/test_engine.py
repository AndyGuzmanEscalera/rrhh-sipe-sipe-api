import uuid
from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.attendance.evaluation import (calendar_days, evaluate_day, evaluate_period,
                                       period_summary, select_principal_marks)
from app.attendance.evaluation_models import (
    DaySchedule, DayStatus, EvaluationParameters, EvaluationPeriod, EventKind, ExpectedEvent,
    IncidentType, RawMark,
)

EMPLOYEE = uuid.UUID("10000000-0000-4000-8000-000000000001")
DAY = date(2026, 10, 1)


def stamp(value, day=DAY):
    return datetime.fromisoformat(f"{day.isoformat()}T{value}")


def plan(day=DAY):
    # All times/windows are explicitly synthetic test inputs, never engine defaults.
    specs = [(EventKind.MORNING_ENTRY, "08:00:00", "06:00:00", "09:30:00"),
              (EventKind.MORNING_EXIT, "12:00:00", "11:30:00", "12:39:59"),
              (EventKind.AFTERNOON_ENTRY, "14:00:00", "12:40:00", "15:30:00"),
              (EventKind.FINAL_EXIT, "18:00:00", "17:30:00", "23:59:59")]
    return DaySchedule(day=day, shift_id="synthetic", name="Synthetic test schedule", events=tuple(
        ExpectedEvent(kind=kind, expected_at=stamp(expected, day), window_start=stamp(start, day), window_end=stamp(end, day))
        for kind, expected, start, end in specs))


def params(**changes):
    return EvaluationParameters(**{**dict(morning_entry_tolerance=300, morning_exit_tolerance=0,
        afternoon_entry_tolerance=300, afternoon_exit_tolerance=0, minimum_return_interval=1800,
        duplicate_window=300), **changes})


def marks(*times, day=DAY, employee=EMPLOYEE):
    return tuple(RawMark(record_id=f"record-{i}", device_id="synthetic", user_id="007",
        funcionario_id=employee, timestamp=stamp(value, day), status=89, punch=42, uid=i, source="SOURCE_API")
        for i, value in enumerate(times))


def evaluate(times, parameters=None, schedule=None, coverage=True, now=None):
    return evaluate_day(EMPLOYEE, DAY, schedule or plan(), parameters or params(),
                        marks(*times), coverage, now)


def texts(day):
    return " | ".join(incident.description for incident in day.incidents)


# --------------------------------------------------------------------------
# 1. Jornada normal con cuatro marcas
# --------------------------------------------------------------------------
def test_jornada_normal_con_cuatro_marcas():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00", "18:00:00"])
    assert day.status == DayStatus.NORMAL
    assert day.morning_entry == stamp("08:00:00")
    assert day.morning_exit == stamp("12:00:00")
    assert day.afternoon_entry == stamp("14:00:00")
    assert day.final_exit == stamp("18:00:00")
    assert day.late_minutes == 0
    assert day.early_exit_minutes == 0
    assert day.absence_minutes == 0
    assert day.overtime_minutes == 0
    assert day.additional_records == 0
    assert len(day.original_records) == 4


# --------------------------------------------------------------------------
# 2. Atraso
# --------------------------------------------------------------------------
@pytest.mark.parametrize("entry,late_minutes", [("08:04:00", 0), ("08:05:00", 0),
                                               ("08:06:00", 1), ("08:15:00", 10)])
def test_atraso_contra_hora_de_entrada_y_tolerancia(entry, late_minutes):
    day = evaluate([entry, "12:05:00", "13:55:00", "18:10:00"])
    assert day.late_minutes == late_minutes
    assert isinstance(day.late_minutes, int)
    if late_minutes:
        assert day.status == DayStatus.OBSERVATION
        assert f"Atraso de {late_minutes} min" in texts(day)
    else:
        assert day.status == DayStatus.NORMAL


def test_el_atraso_no_devuelve_minutos_fractionales():
    day = evaluate(["08:06:30", "12:05:00", "13:55:00", "18:10:00"])
    assert day.late_minutes == 1
    assert day.late_seconds == 60
    assert isinstance(day.late_minutes, int)


# --------------------------------------------------------------------------
# 3. Salida anticipada
# --------------------------------------------------------------------------
def test_salida_anticipada():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00", "17:30:00"],
                   params(afternoon_exit_tolerance=300))
    # 30 minutos antes de las 18:00; 5 minutos de tolerancia perdonados.
    assert day.gross_early_exit_minutes == 30
    assert day.early_exit_minutes == 25
    assert "Salida anticipada de 25 min" in texts(day)
    assert day.status == DayStatus.OBSERVATION


def test_salida_dentro_de_la_tolerancia_no_penaliza():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00", "17:58:00"],
                   params(afternoon_exit_tolerance=300))
    assert day.early_exit_minutes == 0
    assert day.gross_early_exit_minutes == 2


def test_salir_despues_de_la_hora_esperada_no_penaliza():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00", "18:20:00"])
    assert day.early_exit_minutes == 0
    assert day.overtime_minutes == 20


def test_sin_salida_final_no_se_inventa_valor():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00"])
    assert day.early_exit_minutes is None
    assert day.overtime_minutes is None
    assert "Falta marcación de salida final" in texts(day)
    assert day.status == DayStatus.ANOMALY


# --------------------------------------------------------------------------
# 4. Horas extra
# --------------------------------------------------------------------------
def test_horas_extra_informativas():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00", "19:07:00"])
    assert day.overtime_minutes == 67
    assert day.early_exit_minutes == 0


# --------------------------------------------------------------------------
# 5. Salida intermedia y retorno
# --------------------------------------------------------------------------
def test_salida_intermedia_y_retorno():
    day = evaluate(["08:00:00", "09:00:00", "15:00:00", "18:00:00"])
    assert day.morning_exit == stamp("09:00:00")
    assert day.afternoon_entry == stamp("15:00:00")
    assert day.final_exit == stamp("18:00:00")
    assert day.status == DayStatus.OBSERVATION
    assert "Ausencia durante jornada: 240 min" in texts(day)


# --------------------------------------------------------------------------
# 6. Ausencia atravesando el almuerzo
# --------------------------------------------------------------------------
def test_ausencia_atraviesa_el_aluerzo_no_cuenta():
    day = evaluate(["08:00:00", "09:00:00", "15:00:00", "18:00:00"])
    # 09:00-12:00 = 180 min y 14:00-15:00 = 60 min. El descanso 12:00-14:00 no cuenta.
    assert day.absence_minutes == 240
    assert "Ausencia durante jornada: 240 min" in texts(day)


def test_ausencia_nula_cuando_la_jornada_esta_cubierta():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00", "18:00:00"])
    assert day.absence_minutes == 0


# --------------------------------------------------------------------------
# 7. Multiples salidas y retornos
# --------------------------------------------------------------------------
def test_multiples_salidas_y_retornos_conservan_todas_las_marcas():
    day = evaluate(["08:00:00", "10:00:00", "11:00:00", "15:00:00", "16:00:00", "18:00:00"])
    assert len(day.original_records) == 6
    assert day.additional_records == 2
    assert day.status == DayStatus.OBSERVATION


# --------------------------------------------------------------------------
# 8. Mas de cuatro marcaciones
# --------------------------------------------------------------------------
def test_mas_de_cuatro_marcaciones_conserva_todas_las_raw():
    day = evaluate(["07:55:00", "12:05:00", "13:55:00", "18:01:00", "18:03:00", "18:05:00", "18:30:00"])
    assert len(day.original_records) == 7
    assert day.morning_entry == stamp("07:55:00")
    assert day.morning_exit == stamp("12:05:00")
    assert day.afternoon_entry == stamp("13:55:00")
    # La salida principal es la PRIMERA salida valida, no la ultima del dia.
    assert day.final_exit == stamp("18:01:00")
    assert day.additional_records == 3


# --------------------------------------------------------------------------
# 9. Multiples marcaciones despues de las 18:00
# --------------------------------------------------------------------------
def test_multiples_marcaciones_despues_de_la_salida_son_informativas():
    day = evaluate(["07:55:00", "12:05:00", "13:55:00", "18:01:00", "18:03:00", "18:05:00"])
    assert day.status == DayStatus.NORMAL
    assert "Múltiples marcaciones registradas en salida" in texts(day)
    assert not any(incident.type == IncidentType.MISSING_MARK for incident in day.incidents)


# --------------------------------------------------------------------------
# 10. Falta una marcacion
# --------------------------------------------------------------------------
def test_falta_una_marcacion_es_anomalia():
    day = evaluate(["08:00:00", "12:00:00", "14:00:00"])
    assert day.status == DayStatus.ANOMALY
    assert "Falta marcación de salida final" in texts(day)
    assert sum(i.type == IncidentType.MISSING_MARK for i in day.incidents) == 1


# --------------------------------------------------------------------------
# 11-14. Dia actual (jornada en curso)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("times,expected", [
    (["07:55:00"], {"morning_entry"}),
    (["07:55:00", "12:05:00"], {"morning_entry", "morning_exit"}),
    (["07:55:00", "12:05:00", "13:57:00"], {"morning_entry", "morning_exit", "afternoon_entry"}),
    (["07:55:00", "12:05:00", "13:57:00", "18:01:00"], {"morning_entry", "morning_exit",
                                                             "afternoon_entry", "final_exit"}),
])
def test_dia_actual_en_curso_muestra_lo_ya_clasificable(times, expected):
    day = evaluate(times, now=stamp("14:30:00"))
    assert day.status == DayStatus.IN_PROGRESS
    for kind, attribute in ((EventKind.MORNING_ENTRY, "morning_entry"),
                             (EventKind.MORNING_EXIT, "morning_exit"),
                             (EventKind.AFTERNOON_ENTRY, "afternoon_entry"),
                             (EventKind.FINAL_EXIT, "final_exit")):
        if kind.value in expected:
            assert getattr(day, attribute) is not None
        else:
            assert getattr(day, attribute) is None
    # Un dia en curso no se convierte en anomalia por falta de marcaciones.
    assert not any(incident.type == IncidentType.MISSING_MARK for incident in day.incidents)


def test_dia_actual_con_cuatro_marcas_cierra_la_jornada():
    day = evaluate(["07:55:00", "12:05:00", "13:57:00", "18:01:00"], now=stamp("20:00:00"))
    assert day.status == DayStatus.NORMAL
    assert day.final_exit == stamp("18:01:00")


def test_dia_actual_sin_marcaciones_es_sin_datos():
    day = evaluate([], now=stamp("14:30:00"))
    assert day.status == DayStatus.NO_DATA
    assert len(day.original_records) == 0


# --------------------------------------------------------------------------
# 15. Cero marcaciones
# --------------------------------------------------------------------------
def test_cero_marcaciones_es_sin_datos():
    day = evaluate([])
    assert day.status == DayStatus.NO_DATA
    assert day.absence_minutes is None
    assert day.late_minutes is None
    assert day.original_records == ()


def test_sin_marcaciones_y_sin_cobertura_no_es_ausencia():
    day = evaluate([], coverage=False)
    assert day.status == DayStatus.NO_DATA
    assert day.reason == "outside_confirmed_coverage"


def test_las_marcaciones_existen_no_se_declara_sin_datos():
    day = evaluate(["07:55:00", "12:05:00", "13:55:00", "18:10:00"], coverage=False)
    assert day.status is not DayStatus.NO_DATA
    assert len(day.original_records) == 4


# --------------------------------------------------------------------------
# 16. Totales del periodo
# --------------------------------------------------------------------------
def test_suma_correcta_de_totales_del_periodo():
    period = EvaluationPeriod(start_date=DAY, end_exclusive=DAY + timedelta(days=3))
    schedules = {DAY: plan(), DAY + timedelta(days=1): plan(DAY + timedelta(days=1))}
    records = (marks("08:06:00", "12:00:00", "14:00:00", "17:30:00", day=DAY)
               + marks("09:00:00", "11:00:00", "15:00:00", "19:07:00", day=DAY + timedelta(days=1)))
    days = evaluate_period(EMPLOYEE, period, schedules, params(afternoon_exit_tolerance=300),
                           records, {DAY: True, DAY + timedelta(days=1): True})
    summary = period_summary(days)
    assert summary.total_records == 8
    assert summary.days_evaluated == 2
    assert summary.total_late_minutes == 6
    assert summary.total_early_exit_minutes == 5
    assert summary.total_absence_minutes == 240
    assert summary.total_overtime_minutes == 67
    assert isinstance(summary.total_late_minutes, int)
    assert summary.totals_complete


def test_totales_en_cero_no_son_nulos():
    period = EvaluationPeriod(start_date=DAY, end_exclusive=DAY + timedelta(days=1))
    days = evaluate_period(EMPLOYEE, period, {DAY: plan()}, params(),
                           marks("08:00:00", "12:00:00", "14:00:00", "18:00:00"), {DAY: True})
    summary = period_summary(days)
    assert summary.total_late_minutes == 0
    assert summary.total_early_exit_minutes == 0
    assert summary.total_absence_minutes == 0
    assert summary.total_overtime_minutes == 0


# --------------------------------------------------------------------------
# Garantias que se conservan: evidencia inmutable y sin datos ficticios
# --------------------------------------------------------------------------
def test_las_marcaciones_raw_nunca_se_modifican():
    original = marks("07:55:00", "12:05:00", "13:55:00", "18:10:00")
    snapshot = tuple(mark.model_dump_json() for mark in original)
    day = evaluate_day(EMPLOYEE, DAY, plan(), params(), tuple(reversed(original)), True)
    assert day.original_records == original
    assert tuple(mark.model_dump_json() for mark in original) == snapshot
    assert day.final_exit == stamp("18:10:00")
    with pytest.raises(ValidationError):
        original[0].timestamp = stamp("08:00:00")


def test_los_codigos_raw_no_se_interpretan():
    original = marks("07:55:00", "12:05:00", "13:55:00", "18:10:00")
    changed = tuple(mark.model_copy(update={"punch": i * 87, "status": 255 - i}) for i, mark in enumerate(original))
    assert evaluate_day(EMPLOYEE, DAY, plan(), params(), changed, True).status == DayStatus.NORMAL


def test_marcas_duplicadas_se_sugieren_pero_no_se_asignan():
    day = evaluate(["07:55:00", "12:05:00", "13:55:00", "18:10:00", "18:12:00", "18:15:00"])
    duplicate = next(i for i in day.incidents if i.type == IncidentType.POSSIBLE_DUPLICATE)
    assert len(duplicate.record_ids) == 3
    assert len(day.original_records) == 6
    assert duplicate.suggestion.suggested_record_id == "record-3"
    assert day.status == DayStatus.OBSERVATION


def test_la_ventana_de_duplicados_no_encadena():
    day = evaluate(["07:55:00", "12:05:00", "13:55:00", "18:10:00", "18:14:00", "18:18:00"])
    groups = [i.record_ids for i in day.incidents if i.type == IncidentType.POSSIBLE_DUPLICATE]
    assert groups == [("record-3", "record-4")]


def test_una_marcacion_adicional_es_informativa():
    day = evaluate(["07:55:00", "10:30:00", "12:05:00", "13:55:00", "18:10:00"])
    assert day.status == DayStatus.NORMAL
    assert [i.type for i in day.incidents] == [IncidentType.EXTRA_MARK]
    assert len(day.original_records) == 5


def test_sin_jornada_o_sin_parametros_es_explicito():
    records = marks("07:55:00")
    missing_schedule = evaluate_day(EMPLOYEE, DAY, None, params(), records)
    assert missing_schedule.pending_configuration == ("employee_shift_source",)
    missing_parameters = evaluate_day(EMPLOYEE, DAY, plan(), None, records)
    assert missing_parameters.pending_configuration == ("attendance_parameters",)


def test_los_parametros_cambian_el_atraso_sin_tocar_las_marcas():
    times = ["08:10:00", "12:05:00", "14:10:00", "18:10:00"]
    assert evaluate(times).late_minutes == 5
    assert evaluate(times, params(morning_entry_tolerance=600, afternoon_entry_tolerance=600)).late_minutes == 0
    assert evaluate(times, params(morning_entry_tolerance=0, afternoon_entry_tolerance=0)).late_minutes == 20


def test_otras_registros_o_dias_se_rechazan():
    with pytest.raises(ValueError):
        evaluate_day(uuid.uuid4(), DAY, plan(), params(), marks("07:55:00"))
    with pytest.raises(ValueError):
        evaluate_day(EMPLOYEE, DAY + timedelta(days=1), plan(), params(), marks("07:55:00"))


def test_no_se_aceptan_turnos_nocturnos_sin_adapter():
    with pytest.raises(ValidationError):
        DaySchedule(day=DAY, shift_id="invalid", name="Invalid", events=plan().events[:1])
    with pytest.raises(ValidationError):
        DaySchedule(day=DAY + timedelta(days=1), shift_id="invalid", name="Invalid", events=plan().events)


def test_el_resumen_y_el_calendario_reflejan_el_dia():
    period = EvaluationPeriod(start_date=DAY, end_exclusive=DAY + timedelta(days=2))
    tomorrow = DAY + timedelta(days=1)
    schedules = {DAY: plan(), tomorrow: DaySchedule(day=tomorrow, shift_id="rest", name="Confirmed non-working day")}
    days = evaluate_period(EMPLOYEE, period, schedules, params(),
        marks("08:10:00", "12:05:00", "13:55:00", "18:10:00"), {DAY: True})
    summary = period_summary(days)
    assert summary.days_evaluated == 1 and summary.observation_days == 1 and summary.no_data_days == 1
    assert summary.total_late_minutes == 5 and summary.total_records == 4 and summary.totals_complete
    calendar = calendar_days(days)
    assert calendar[0].incident_count == 1 and calendar[0].total_records == 4
    assert calendar[1].status == DayStatus.NO_DATA


def test_la_seleccion_principal_es_determinista():
    units = [[mark] for mark in marks("07:55:00", "12:05:00", "13:55:00", "18:01:00", "18:03:00")]
    selected, additional = select_principal_marks(units, plan().events)
    assert selected[EventKind.FINAL_EXIT].timestamp == stamp("18:01:00")
    assert len(additional) == 1
