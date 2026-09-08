from __future__ import annotations

import uuid
from typing import Any

from app.core.storage import store


def save_checkpoint(user_id: str, session_id: str, payload: dict[str, Any]) -> str:
    checkpoint_id = str(uuid.uuid4())
    store.execute(
        "INSERT INTO workflow_checkpoints(id,user_id,session_id,payload,created_at) VALUES (?,?,?,?,?)",
        (checkpoint_id, user_id, session_id, store.dumps(payload), store.now()),
    )
    return checkpoint_id


def claim_checkpoint(checkpoint_id: str, user_id: str, session_id: str) -> dict[str, Any] | None:
    # A checkpoint is consumed once, including failed or interrupted resumes.
    with store.connect() as conn:
        row = conn.execute(
            "UPDATE workflow_checkpoints SET status='consumed' "
            "WHERE id=? AND user_id=? AND session_id=? AND status='waiting' RETURNING payload",
            (checkpoint_id, user_id, session_id),
        ).fetchone()
    return store.loads(row["payload"], {}) if row else None
