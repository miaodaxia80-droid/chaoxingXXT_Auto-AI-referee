from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_db,
    require_admin,
    require_admin_csrf,
    require_app_user,
    require_csrf,
)
from chaoxing_app.api.schemas import (
    CardKeyCreateRequest,
    CardKeyGenerateResponse,
    CardKeyIssueItem,
    CardKeyRedeemRequest,
    CardKeyRedeemResponse,
    CardKeyResponse,
    EntitlementResponse,
)
from chaoxing_app.infrastructure.db.models import AppUser, CardKey, ensure_utc, utc_now

router = APIRouter(prefix="/cards", tags=["cards"])

# Crockford-style alphabet (no 0/O/1/I); QLIT + 12 chars -> ~80-bit entropy.
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_PREFIX = "QLIT"

# Redeem rate limit (in-process; fine for single-instance): 10 per IP / 5 min.
_REDEEM_WINDOW_SECONDS = 300
_REDEEM_MAX_ATTEMPTS = 10
_redeem_attempts: dict[str, list[float]] = defaultdict(list)


def _normalize_code(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()


def _code_hint(canonical: str) -> str:
    return f"****-{canonical[-4:]}"


def _generate_code() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return f"{_CODE_PREFIX}-{body[:4]}-{body[4:8]}-{body[8:12]}"


def _check_redeem_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = [ts for ts in _redeem_attempts[ip] if now - ts < _REDEEM_WINDOW_SECONDS]
    if len(window) >= _REDEEM_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many redeem attempts"
        )
    window.append(now)
    _redeem_attempts[ip] = window


def _require_app_user(context: AuthContext) -> AppUser:
    if context.kind != "app_user" or context.app_user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin session cannot access app user resources",
        )
    return context.app_user


def _entitlement_active(user: AppUser) -> bool:
    expires = ensure_utc(user.plan_expires_at)
    if expires is not None and expires > utc_now():
        return True
    return user.task_credits > 0


def _to_response(card: CardKey) -> CardKeyResponse:
    return CardKeyResponse(
        id=card.id,
        code_hint=card.code_hint,
        kind=card.kind,
        value=card.value,
        batch=card.batch,
        status=card.status,
        used_by=card.used_by,
        used_at=card.used_at,
        created_at=card.created_at,
    )


# --- 管理端 ---


@router.post("", response_model=CardKeyGenerateResponse, status_code=status.HTTP_201_CREATED)
def generate_card_keys(
    payload: CardKeyCreateRequest,
    context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> CardKeyGenerateResponse:
    if payload.kind == "time" and payload.value > 3_650:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="time card value too large"
        )
    admin_username = context.admin.username if context.admin else ""
    items: list[CardKeyIssueItem] = []
    for _ in range(payload.count):
        code = _generate_code()
        canonical = _normalize_code(code)
        db.add(
            CardKey(
                code_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                code_hint=_code_hint(canonical),
                kind=payload.kind,
                value=payload.value,
                batch=payload.batch.strip(),
                created_by=admin_username,
            )
        )
        items.append(CardKeyIssueItem(code=code, kind=payload.kind, value=payload.value))
    db.flush()
    return CardKeyGenerateResponse(created=len(items), items=items)


@router.get("", response_model=list[CardKeyResponse])
def list_card_keys(
    status_filter: str | None = None,
    _context: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[CardKeyResponse]:
    query = select(CardKey).order_by(CardKey.id.desc()).limit(1_000)
    if status_filter in {"unused", "used", "revoked"}:
        query = query.where(CardKey.status == status_filter)
    return [_to_response(card) for card in db.scalars(query)]


@router.post("/{card_id}/revoke", response_model=CardKeyResponse)
def revoke_card_key(
    card_id: int,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> CardKeyResponse:
    card = db.get(CardKey, card_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card key not found")
    if card.status != "unused":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"card key is {card.status}"
        )
    card.status = "revoked"
    return _to_response(card)


# --- 用户端 ---


@router.get("/me", response_model=EntitlementResponse)
def my_entitlement(
    context: AuthContext = Depends(require_app_user),
) -> EntitlementResponse:
    user = _require_app_user(context)
    return EntitlementResponse(
        plan_expires_at=user.plan_expires_at,
        task_credits=user.task_credits,
        active=_entitlement_active(user),
    )


@router.post("/redeem", response_model=CardKeyRedeemResponse)
def redeem_card_key(
    payload: CardKeyRedeemRequest,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> CardKeyRedeemResponse:
    user = _require_app_user(context)
    _check_redeem_rate(request)

    canonical = _normalize_code(payload.code)
    card = db.scalar(
        select(CardKey).where(CardKey.code_hash == hashlib.sha256(canonical.encode()).hexdigest())
    )
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card key not found")
    if card.status == "used":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="card key already used")
    if card.status == "revoked":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="card key revoked")

    now = utc_now()
    if card.kind == "time":
        base = max(ensure_utc(user.plan_expires_at) or now, now)
        user.plan_expires_at = base + timedelta(days=card.value)
    else:
        user.task_credits += card.value
    card.status = "used"
    card.used_by = user.id
    card.used_at = now
    db.flush()
    return CardKeyRedeemResponse(
        kind=card.kind,
        value=card.value,
        plan_expires_at=user.plan_expires_at,
        task_credits=user.task_credits,
    )
