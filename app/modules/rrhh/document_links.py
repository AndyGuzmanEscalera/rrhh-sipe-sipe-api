"""Vinculación biométrica por CI vía API Correspondencia.

RRHH es dueño de ``biometric_records`` y ``biometric_employee_links``.
Correspondencia es la fuente maestra de funcionarios (HTTP client; sin DB remota).

La CI del reloj vive en ``Record.biometric_user_id`` (no existe un campo legacy
``user_id`` en el modelo RRHH). Se compara textual y exacta contra
``Employee.document_number``. Solo se insertan filas en
``biometric_employee_links``; nunca se modifican marcaciones RAW.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.biometric.models import Device, EmployeeLink, Record
from app.external.correspondencia.errors import CorrespondenciaError


@dataclass(frozen=True)
class LinkByDocumentResult:
    biometric_ids_found: int
    matches_by_document: int
    links_created: int
    links_already_existing: int
    unmatched: int
    ambiguous_documents: int
    unmatched_ids: tuple[str, ...] = ()
    ambiguous_ids: tuple[str, ...] = ()

    def as_dict(self):
        return asdict(self)


def fetch_all_employees(client, *, page_size: int = 100, page_limit: int = 1000):
    """Pagina todos los funcionarios vía el cliente Correspondencia existente."""
    employees, page = [], 1
    while page <= page_limit:
        rows, current, pages = client.list_employees(
            page=page, page_size=page_size, include_deleted=True)
        if current != page:
            raise CorrespondenciaError('correspondencia_pagination_invalid')
        employees.extend(rows)
        if page >= pages:
            return employees
        page += 1
    raise CorrespondenciaError('correspondencia_pagination_limit')


def _document_indexes(employees):
    """Separa CI únicas (vinculables) de CI ambiguas (varios funcionarios)."""
    candidates = defaultdict(list)
    for employee in employees:
        if employee.document_number:
            candidates[employee.document_number].append(employee.id)
    unique = {doc: ids[0] for doc, ids in candidates.items() if len(ids) == 1}
    ambiguous = {doc for doc, ids in candidates.items() if len(ids) > 1}
    return unique, ambiguous


def link_employees_by_document(
    db: Session,
    client,
    *,
    device_id: str | None = None,
) -> LinkByDocumentResult:
    """Herramienta administrativa opcional: vincula en lote por CI.

    El reporte normal no depende de este CLI. Idempotente: no recrea vínculos
    existentes ni toca ``biometric_records``.
    """
    # Serialize bulk reconciliation so the composite primary key is never raced
    # by a worker, an import request and the administrative CLI at once.
    db.execute(text('SELECT pg_advisory_xact_lock(73611005)'))
    pairs_query = select(Record.device_id, Record.biometric_user_id).distinct()
    if device_id:
        pairs_query = pairs_query.where(Record.device_id == device_id)
    pairs = list(db.execute(pairs_query.order_by(Record.device_id, Record.biometric_user_id)))

    biometric_ids = sorted({bio_id for _, bio_id in pairs if bio_id})
    employees = fetch_all_employees(client)
    by_document, ambiguous_docs = _document_indexes(employees)

    created = 0
    already = 0
    matches = 0
    unmatched_ids: list[str] = []
    ambiguous_ids: list[str] = []

    for device, bio_id in pairs:
        if not bio_id:
            continue
        if db.get(EmployeeLink, (device, bio_id)) is not None:
            already += 1
            if bio_id in by_document:
                matches += 1
            continue
        if bio_id in ambiguous_docs:
            ambiguous_ids.append(bio_id)
            continue
        funcionario_id = by_document.get(bio_id)
        if funcionario_id is None or db.get(Device, device) is None:
            unmatched_ids.append(bio_id)
            continue
        matches += 1
        db.add(EmployeeLink(
            device_id=device,
            biometric_user_id=bio_id,
            funcionario_id_external=funcionario_id,
            is_active=True,
        ))
        created += 1

    db.flush()
    return LinkByDocumentResult(
        biometric_ids_found=len(biometric_ids),
        matches_by_document=matches,
        links_created=created,
        links_already_existing=already,
        unmatched=len(set(unmatched_ids)),
        ambiguous_documents=len(set(ambiguous_ids)),
        unmatched_ids=tuple(sorted(set(unmatched_ids))),
        ambiguous_ids=tuple(sorted(set(ambiguous_ids))),
    )
