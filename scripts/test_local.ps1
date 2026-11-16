$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not $env:RRHH_TEST_DATABASE_URL) { throw 'RRHH_TEST_DATABASE_URL is required' }
@'
import os
from sqlalchemy.engine import make_url
value=make_url(os.environ['RRHH_TEST_DATABASE_URL'])
assert value.host in {'127.0.0.1','localhost'} and value.database.startswith('rrhh_api_test_'), 'Disposable loopback test database required'
'@ | python -
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:DATABASE_URL = $env:RRHH_TEST_DATABASE_URL
python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m alembic check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pytest -q
exit $LASTEXITCODE
