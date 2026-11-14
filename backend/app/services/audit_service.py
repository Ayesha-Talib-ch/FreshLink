"""Write entries to the append-only audit log (FR24)."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User


@dataclass(frozen=True)
class ClientInfo:
    """Where an action came from (stored with every audit entry)."""

    ip: str | None
    user_agent: str | None


def record(
    db: Session,
    *,
    actor: User,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    client: ClientInfo,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to the CURRENT transaction (the caller commits).

    Being in the same transaction means: if the action fails and is rolled back,
    its audit entry disappears too, and if the action succeeds, the entry is
    guaranteed to exist.
    """
    db.add(
        AuditLog(
            actor_id=actor.id,
            actor_email=actor.email,
            actor_role=actor.role.value,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            before=before,
            after=after,
            ip=client.ip,
            user_agent=(client.user_agent or "")[:300] or None,
        )
    )
