"""
Client-onboarding rules shared by EVERY path that creates a client profile
(canonical /clients and the legacy /lending/borrowers alias), so the two can
never drift apart again.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.core.permissions import OFFICER_SCOPE_ROLES
from app.services.authz import user_can

# Front-line originators: they own what they create and cannot approve it.
_ORIGINATOR_ROLES = ("relationship_officer", "loan_officer")


def initial_profile_state(db, user) -> tuple[str, int | None]:
    """Return ``(profile_status, officer_staff_id)`` for a profile created by ``user``.

    Rules (maker-checker):
      * A front-line officer's profile always starts ``pending_approval`` and is
        owned by that officer. An officer with NO staff linkage is refused -
        previously such a user fell through and produced an already-approved,
        unowned profile.
      * Anyone else is only auto-approved when they could approve it anyway
        (``clients.approve``). Without that permission the profile is queued.
    """
    if user.role in _ORIGINATOR_ROLES or user.role in OFFICER_SCOPE_ROLES:
        if not user.staff_id:
            raise HTTPException(
                403, "Your account is not linked to a staff record, so you cannot "
                     "onboard clients. Ask an administrator to link it.")
        return "pending_approval", user.staff_id
    if user_can(db, user, "clients.approve"):
        return "approved", None
    return "pending_approval", None
