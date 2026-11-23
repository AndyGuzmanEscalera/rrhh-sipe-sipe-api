#!/usr/bin/env python3
"""Herramienta administrativa opcional: vincula biometric_user_id por CI en lote.

El reporte GET /rrhh/attendance/employees/{id}/report ya resuelve vínculos con el
Bearer delegado de la petición; este CLI no es requerido para el flujo normal.

Uso (desde la raíz del API RRHH, con DATABASE_URL y CORRESPONDENCIA_API_*):

    python -m app.scripts.link_biometric_employees_by_document
    python -m app.scripts.link_biometric_employees_by_document --device-id alcaldia-central
    python -m app.scripts.link_biometric_employees_by_document --dry-run

Solo inserta en biometric_employee_links. No modifica biometric_records (RAW).
No reimporta ni resincroniza marcaciones. Idempotente.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Vincula marcaciones biométricas con funcionarios usando la CI/documento.')
    parser.add_argument(
        '--device-id',
        default=None,
        help='Limitar a un dispositivo; por defecto procesa todos los presentes en marcaciones.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Calcula e informa sin confirmar la transacción.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Imports diferidos: --help no requiere DATABASE_URL ni token Correspondencia.
    from app.core.config import get_settings
    from app.db.session import session_factory
    from app.external.correspondencia.client import CorrespondenciaClient
    from app.external.correspondencia.config import CorrespondenciaConfig
    from app.modules.rrhh.document_links import link_employees_by_document

    settings = get_settings()
    client = CorrespondenciaClient(CorrespondenciaConfig(
        settings.correspondencia_api_url,
        settings.correspondencia_api_token.get_secret_value(),
        settings.correspondencia_api_timeout,
    ))

    db = session_factory()
    try:
        result = link_employees_by_document(db, client, device_id=args.device_id)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    payload = result.as_dict()
    payload['dry_run'] = bool(args.dry_run)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
