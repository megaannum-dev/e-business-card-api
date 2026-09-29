import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth

from app.core.firebase import is_firebase_initialized

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_user_id(
    credentials: HTTPAuthorizationCredentials | None,
    *,
    check_revoked: bool,
) -> str:
    if not is_firebase_initialized():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not configured.",
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Missing or invalid authorization header.")

    token = credentials.credentials
    try:
        decoded = firebase_auth.verify_id_token(token, check_revoked=check_revoked)
    except firebase_auth.RevokedIdTokenError as exc:
        # Subclass of InvalidIdTokenError, so this must stay above the generic
        # handler below or a revoked session reports as a plain bad token.
        logger.info("Rejected revoked ID token")
        raise _unauthorized("Session revoked. Please sign in again.") from exc
    except firebase_auth.UserDisabledError as exc:
        logger.info("Rejected ID token for disabled account")
        raise _unauthorized("This account has been disabled.") from exc
    except Exception as exc:
        logger.warning("Firebase token verification failed: %s", exc)
        detail = "Invalid or expired token."
        exc_text = str(exc).lower()
        if "aud" in exc_text or "audience" in exc_text:
            detail = "Firebase project mismatch between app token and API server."
        raise _unauthorized(detail) from exc

    user_id = decoded.get("uid")
    if not user_id:
        raise _unauthorized("Invalid token payload.")

    return str(user_id)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Verify the caller's Firebase ID token: signature and expiry only.

    The fast path, used by most routes. Verification is local against cached
    Google public certs, so there is no round trip per request -- but a session
    revoked server-side stays usable until the token expires (~1 hour). For
    routes where that window matters, use get_current_user_id_strict.
    """
    return _resolve_user_id(credentials, check_revoked=False)


def get_current_user_id_strict(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Verify the token AND that the session is still live.

    check_revoked=True makes firebase-admin fetch the user record from Google on
    every call, which catches revoked sessions and disabled accounts immediately
    at the cost of a network round trip and Admin SDK quota per request. Reserved
    for routes where a stolen token does lasting damage: deleting an account or a
    card, and minting or revoking public share links.

    Deliberately `def` rather than `async def`. The Firebase call is blocking
    I/O, and FastAPI runs sync dependencies in a threadpool -- declaring this
    async would stall the event loop for the duration of every such request.
    """
    return _resolve_user_id(credentials, check_revoked=True)
