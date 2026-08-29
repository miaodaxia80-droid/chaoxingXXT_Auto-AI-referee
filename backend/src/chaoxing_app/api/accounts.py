import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_db,
    get_fingerprint_key,
    get_secret_box,
    require_auth,
    require_csrf,
)
from chaoxing_app.api.ownership import owned_account_or_404, scoped_user_id
from chaoxing_app.api.quotas import ensure_account_quota
from chaoxing_app.api.schemas import (
    AccountCreateRequest,
    AccountImportResponse,
    AccountImportRowResponse,
    AccountResponse,
    AccountUpdateRequest,
)
from chaoxing_app.infrastructure.db.accounts import (
    AccountRepository,
    AccountUpdate,
    DuplicateAccountError,
    NewAccount,
    mask_username,
)
from chaoxing_app.infrastructure.db.models import Account
from chaoxing_app.infrastructure.security.secrets import SecretBox

router = APIRouter(prefix="/accounts", tags=["accounts"])

_MAX_IMPORT_BYTES = 1_048_576
_MAX_IMPORT_ROWS = 500
_IMPORT_FIELDS = {
    "username",
    "password",
    "cookies",
    "remark",
    "user_agent",
    "speed",
    "chapter_concurrency",
    "unopened_policy",
}


def to_response(account: Account) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        remark=account.remark,
        username_hint=account.username_hint,
        enabled=account.enabled,
        user_agent=account.user_agent,
        speed=account.speed,
        chapter_concurrency=account.chapter_concurrency,
        unopened_policy=account.unopened_policy,
        has_password=bool(account.secret.password_encrypted),
        has_cookies=bool(account.secret.cookies_encrypted),
        answer_profile_override=account.answer_profile_override,
    )


@router.get("", response_model=list[AccountResponse])
def list_accounts(
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
    fingerprint_key: bytes = Depends(get_fingerprint_key),
) -> list[AccountResponse]:
    repository = AccountRepository(secret_box=secret_box, fingerprint_key=fingerprint_key)
    return [
        to_response(account) for account in repository.list(db, user_id=scoped_user_id(context))
    ]


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreateRequest,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
    fingerprint_key: bytes = Depends(get_fingerprint_key),
) -> AccountResponse:
    if context.kind == "app_user":
        ensure_account_quota(db, context.app_user)
    repository = AccountRepository(secret_box=secret_box, fingerprint_key=fingerprint_key)
    try:
        account = repository.create(
            db,
            NewAccount(
                username=payload.username,
                password=payload.password,
                cookies=payload.cookies,
                remark=payload.remark,
                user_agent=payload.user_agent,
                speed=payload.speed,
                chapter_concurrency=payload.chapter_concurrency,
                unopened_policy=payload.unopened_policy,
                answer_profile_override=payload.answer_profile_override,
                user_id=scoped_user_id(context),
            ),
        )
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return to_response(account)


def _import_payload(row: dict[str | None, object]) -> AccountCreateRequest:
    if None in row:
        raise ValueError("row has more values than headers")
    raw: dict[str, object] = {}
    for field in _IMPORT_FIELDS:
        value = row.get(field)
        if not isinstance(value, str):
            continue
        if field in {"username", "remark", "user_agent", "unopened_policy"}:
            value = value.strip()
        if value != "" or field == "username":
            raw[field] = value
    return AccountCreateRequest.model_validate(raw)


@router.post("/import", response_model=AccountImportResponse)
async def import_accounts(
    request: Request,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
    fingerprint_key: bytes = Depends(get_fingerprint_key),
) -> AccountImportResponse:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if content_type not in {"text/csv", "text/plain", "application/vnd.ms-excel"}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="CSV content type is required",
        )
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="CSV file is too large",
        )
    body = await request.body()
    if len(body) > _MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="CSV file is too large",
        )
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="CSV file must be UTF-8",
        ) from None

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames is None:
            raise ValueError("missing header")
        fieldnames = [field.strip().casefold() for field in reader.fieldnames]
        if len(fieldnames) != len(set(fieldnames)):
            raise ValueError("duplicate header")
        if "username" not in fieldnames or set(fieldnames) - _IMPORT_FIELDS:
            raise ValueError("invalid header")
        reader.fieldnames = fieldnames
        rows = []
        for row in reader:
            rows.append((reader.line_num, row))
    except (csv.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="CSV structure is invalid",
        ) from None
    rows = [
        (line, row)
        for line, row in rows
        if any(isinstance(value, str) and value.strip() for value in row.values())
    ]
    if len(rows) > _MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="CSV has too many rows",
        )

    repository = AccountRepository(secret_box=secret_box, fingerprint_key=fingerprint_key)
    owner_user_id = scoped_user_id(context)
    results: list[AccountImportRowResponse] = []
    for line, row in rows:
        username = row.get("username")
        username_hint = (
            mask_username(username)
            if isinstance(username, str) and username.strip()
            else None
        )
        try:
            payload = _import_payload(row)
            if context.kind == "app_user":
                ensure_account_quota(db, context.app_user)
            with db.begin_nested():
                account = repository.create(
                    db,
                    NewAccount(
                        username=payload.username,
                        password=payload.password,
                        cookies=payload.cookies,
                        remark=payload.remark,
                        user_agent=payload.user_agent,
                        speed=payload.speed,
                        chapter_concurrency=payload.chapter_concurrency,
                        unopened_policy=payload.unopened_policy,
                        answer_profile_override=payload.answer_profile_override,
                        user_id=owner_user_id,
                    ),
                )
            results.append(
                AccountImportRowResponse(
                    line=line,
                    status="created",
                    username_hint=account.username_hint,
                    account_id=account.id,
                )
            )
        except DuplicateAccountError:
            results.append(
                AccountImportRowResponse(
                    line=line,
                    status="duplicate",
                    username_hint=username_hint,
                    error_code="account_exists",
                )
            )
        except HTTPException as exc:
            results.append(
                AccountImportRowResponse(
                    line=line,
                    status="invalid",
                    username_hint=username_hint,
                    error_code=(
                        "account_quota_exceeded"
                        if exc.detail == "account quota exceeded"
                        else "invalid_row"
                    ),
                )
            )
        except (ValidationError, ValueError):
            results.append(
                AccountImportRowResponse(
                    line=line,
                    status="invalid",
                    username_hint=username_hint,
                    error_code="invalid_row",
                )
            )

    return AccountImportResponse(
        total=len(results),
        created=sum(result.status == "created" for result in results),
        duplicate=sum(result.status == "duplicate" for result in results),
        invalid=sum(result.status == "invalid" for result in results),
        results=results,
    )


@router.patch("/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int,
    payload: AccountUpdateRequest,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
    fingerprint_key: bytes = Depends(get_fingerprint_key),
) -> AccountResponse:
    owned_account_or_404(db, account_id, context)
    repository = AccountRepository(secret_box=secret_box, fingerprint_key=fingerprint_key)
    try:
        account = repository.update(
            db,
            account_id,
            AccountUpdate(
                username=payload.username,
                password=payload.password,
                cookies=payload.cookies,
                remark=payload.remark,
                user_agent=payload.user_agent,
                speed=payload.speed,
                chapter_concurrency=payload.chapter_concurrency,
                unopened_policy=payload.unopened_policy,
                enabled=payload.enabled,
                clear_password=payload.clear_password,
                clear_cookies=payload.clear_cookies,
                answer_profile_override=payload.answer_profile_override,
                clear_answer_profile_override=payload.clear_answer_profile_override,
            ),
        )
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return to_response(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: int,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
    fingerprint_key: bytes = Depends(get_fingerprint_key),
) -> Response:
    owned_account_or_404(db, account_id, context)
    repository = AccountRepository(secret_box=secret_box, fingerprint_key=fingerprint_key)
    if not repository.delete(db, account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
