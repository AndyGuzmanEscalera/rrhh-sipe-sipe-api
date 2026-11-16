import hashlib
import json
from datetime import date


def attendance_record(item, device_id):
    timestamp = item.timestamp.isoformat(timespec="seconds")
    record = {
        "device_id": device_id,
        "user_id": str(item.user_id),
        "timestamp": timestamp,
        "date": item.timestamp.date().isoformat(),
        "time": item.timestamp.time().isoformat(timespec="seconds"),
        "status": getattr(item, "status", None),
        "punch": getattr(item, "punch", None),
        "uid": getattr(item, "uid", None),
    }
    # uid puede variar entre descarga y eventos; no es un ID universal de evento.
    identity = [record[k] for k in ("device_id", "user_id", "timestamp", "status", "punch")]
    record["record_id"] = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return record
