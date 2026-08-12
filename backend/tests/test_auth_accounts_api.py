import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.api import auth as auth_api
from chaoxing_app.infrastructure.db.models import Account, AccountSecret, WebSession
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_client(temp_dir: str):
    data_dir = Path(temp_dir) / "data"
    database_path = data_dir / "app.db"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{database_path.as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


def bootstrap_and_login(client: TestClient) -> str:
    setup = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert setup.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    return login.json()["csrf_token"]


def test_first_run_setup_can_only_happen_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            assert client.get("/api/v1/auth/setup").json() == {"required": True}
            bootstrap_and_login(client)
            assert client.get("/api/v1/auth/setup").json() == {"required": False}
            duplicate = client.post(
                "/api/v1/auth/setup",
                json={"username": "other", "password": "another-secure-password"},
            )
            assert duplicate.status_code == 409


@pytest.mark.parametrize("password", ["abcdefgh", "A" * 256])
def test_first_run_setup_accepts_valid_password_boundaries(password: str) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            setup = client.post(
                "/api/v1/auth/setup",
                json={"username": "admin", "password": password},
            )
            assert setup.status_code == 201
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": password},
            )
            assert login.status_code == 200


@pytest.mark.parametrize(
    "password",
    [
        "abcdefg",
        "abcd efgh",
        "abcd\tefgh",
        "密码abcdef",
        "abcdefg\x7f",
        "A" * 257,
    ],
)
def test_first_run_setup_rejects_passwords_outside_visible_ascii(password: str) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            rejected = client.post(
                "/api/v1/auth/setup",
                json={"username": "admin", "password": password},
            )
            assert rejected.status_code == 422
            assert password not in rejected.text
            detail = rejected.json()["detail"]
            assert any(issue["loc"] == ["body", "password"] for issue in detail)
            assert all(set(issue) == {"type", "loc", "msg"} for issue in detail)
            assert client.get("/api/v1/auth/setup").json() == {"required": True}


@pytest.mark.parametrize(
    "password",
    ["abcdefg", "abcd efgh", "abcd\tefgh", "密码abcdef", "abcdefg\x7f", "A" * 257],
)
def test_login_rejects_passwords_outside_the_admin_policy(password: str) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            bootstrap_and_login(client)
            rejected = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": password},
            )
            assert rejected.status_code == 422
            assert password not in rejected.text
            assert any(
                issue["loc"] == ["body", "password"]
                for issue in rejected.json()["detail"]
            )


def test_concurrent_first_run_setup_creates_exactly_one_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        barrier = Barrier(2)
        original_hash = auth_api.password_service.hash

        def synchronized_hash(password: str) -> str:
            barrier.wait(timeout=10)
            return original_hash(password)

        monkeypatch.setattr(auth_api.password_service, "hash", synchronized_hash)
        with client, ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    client.post,
                    "/api/v1/auth/setup",
                    json={"username": username, "password": "correct-horse-battery-staple"},
                )
                for username in ("admin-one", "admin-two")
            ]
            responses = [future.result(timeout=15) for future in futures]

            assert sorted(response.status_code for response in responses) == [201, 409]
            with Session(app.state.engine) as db:
                admins = list(db.scalars(select(auth_api.AdminUser)))
                assert len(admins) == 1
                assert admins[0].id == 1


def test_login_cookie_is_opaque_and_only_digest_is_stored() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            bootstrap_and_login(client)
            plaintext_cookie = client.cookies.get("cx_session")
            assert plaintext_cookie
            with Session(app.state.engine) as db:
                session = db.scalar(select(WebSession))
                assert session is not None
                assert session.token_digest != plaintext_cookie
                assert len(session.token_digest) == 64


def test_custom_session_cookie_name_is_honored() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        settings = AppSettings(
            environment="test",
            data_dir=data_dir,
            database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
            session_cookie_name="private_session",
        )
        app = create_app(settings)
        with TestClient(app) as client:
            setup = client.post(
                "/api/v1/auth/setup",
                json={"username": "admin", "password": "correct-horse-battery-staple"},
            )
            assert setup.status_code == 201
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "correct-horse-battery-staple"},
            )
            assert login.status_code == 200
            assert client.cookies.get("private_session")
            current = client.get("/api/v1/auth/me")
            assert current.status_code == 200
            assert current.headers["Cache-Control"] == "no-store"
            assert current.json()["username"] == "admin"
            assert current.json()["csrf_token"] == login.json()["csrf_token"]


def test_authenticated_session_can_recover_csrf_and_logout() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            current = client.get("/api/v1/auth/me")
            assert current.status_code == 200
            assert current.headers["Cache-Control"] == "no-store"
            assert current.json() == {"username": "admin", "csrf_token": csrf_token}

            logout = client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": current.json()["csrf_token"]},
            )
            assert logout.status_code == 204
            assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            bootstrap_and_login(client)
            with Session(app.state.engine) as db:
                web_session = db.scalar(select(WebSession))
                assert web_session is not None
                web_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                db.commit()
            assert client.get("/api/v1/auth/me").status_code == 401


def test_accounts_require_auth_and_csrf_and_never_return_secrets() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            assert client.get("/api/v1/accounts").status_code == 401
            csrf_token = bootstrap_and_login(client)
            without_csrf = client.post(
                "/api/v1/accounts",
                json={"username": "13800138000", "password": "upstream-password"},
            )
            assert without_csrf.status_code == 403
            created = client.post(
                "/api/v1/accounts",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "username": "13800138000",
                    "password": "upstream-password",
                    "cookies": "_uid=123; token=cookie-secret",
                    "remark": "Primary account",
                },
            )
            assert created.status_code == 201
            payload = created.json()
            assert payload["username_hint"] == "138****8000"
            assert payload["has_password"] is True
            assert payload["has_cookies"] is True
            serialized = created.text
            assert "13800138000" not in serialized
            assert "upstream-password" not in serialized
            assert "cookie-secret" not in serialized

            listed = client.get("/api/v1/accounts")
            assert listed.status_code == 200
            assert listed.json() == [payload]

            with Session(app.state.engine) as db:
                secret = db.scalar(select(AccountSecret))
                assert secret is not None
                assert "13800138000" not in secret.username_encrypted
                assert "upstream-password" not in (secret.password_encrypted or "")
                assert "cookie-secret" not in (secret.cookies_encrypted or "")


def test_csrf_rejects_cross_origin_requests() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            rejected = client.post(
                "/api/v1/accounts",
                headers={
                    "Origin": "https://attacker.example",
                    "X-CSRF-Token": csrf_token,
                },
                json={"username": "cross-origin-user"},
            )
            assert rejected.status_code == 403
            assert rejected.json() == {"detail": "untrusted request origin"}

            accepted = client.post(
                "/api/v1/accounts",
                headers={
                    "Origin": "http://testserver",
                    "X-CSRF-Token": csrf_token,
                },
                json={"username": "same-origin-user"},
            )
            assert accepted.status_code == 201


def test_csrf_accepts_explicit_reverse_proxy_origin() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        settings = AppSettings(
            environment="test",
            data_dir=data_dir,
            database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
            allowed_origins=("https://console.example.com/",),
        )
        app = create_app(settings)
        with TestClient(app) as client:
            csrf_token = bootstrap_and_login(client)
            accepted = client.post(
                "/api/v1/accounts",
                headers={
                    "Origin": "https://console.example.com",
                    "X-CSRF-Token": csrf_token,
                },
                json={"username": "proxy-user"},
            )
            assert accepted.status_code == 201


def test_duplicate_account_is_rejected_without_revealing_username() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            request = {
                "username": "13800138000",
                "password": "upstream-password",
            }
            first = client.post(
                "/api/v1/accounts",
                headers={"X-CSRF-Token": csrf_token},
                json=request,
            )
            second = client.post(
                "/api/v1/accounts",
                headers={"X-CSRF-Token": csrf_token},
                json=request,
            )
            assert first.status_code == 201
            assert second.status_code == 409


def test_csv_import_is_bounded_partial_and_never_returns_secrets() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            csv_body = (
                "\ufeffusername,password,cookies,remark,user_agent,speed,"
                "chapter_concurrency,unopened_policy\r\n"
                "13800138000,first-secret,token=first,Primary,,1.5,2,retry\r\n"
                "13800138000,duplicate-secret,token=duplicate,Duplicate,,1,1,retry\r\n"
                "13900139000,invalid-secret,token=invalid,Invalid,,9,1,retry\r\n"
                "\r\n"
                "13700137000,second-secret,,Secondary,,1,1,skip\r\n"
            )
            assert client.post(
                "/api/v1/accounts/import",
                headers={"Content-Type": "text/csv"},
                content=csv_body,
            ).status_code == 403

            imported = client.post(
                "/api/v1/accounts/import",
                headers={
                    "Content-Type": "text/csv; charset=utf-8",
                    "X-CSRF-Token": csrf_token,
                },
                content=csv_body,
            )
            assert imported.status_code == 200
            payload = imported.json()
            assert payload["total"] == 4
            assert payload["created"] == 2
            assert payload["duplicate"] == 1
            assert payload["invalid"] == 1
            assert [(item["line"], item["status"]) for item in payload["results"]] == [
                (2, "created"),
                (3, "duplicate"),
                (4, "invalid"),
                (6, "created"),
            ]
            serialized = imported.text
            for secret in (
                "13800138000",
                "first-secret",
                "duplicate-secret",
                "invalid-secret",
                "token=first",
                "token=duplicate",
                "token=invalid",
            ):
                assert secret not in serialized

            listed = client.get("/api/v1/accounts")
            assert listed.status_code == 200
            assert len(listed.json()) == 2
            with Session(app.state.engine) as db:
                assert len(list(db.scalars(select(Account)))) == 2


def test_csv_import_rejects_unsafe_shape_encoding_and_row_count() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            headers = {"Content-Type": "text/csv", "X-CSRF-Token": csrf_token}

            assert client.post(
                "/api/v1/accounts/import",
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
                content='{"username":"secret"}',
            ).status_code == 415
            invalid_encoding = client.post(
                "/api/v1/accounts/import",
                headers=headers,
                content=b"username,password\r\n13800138000,\xff\r\n",
            )
            assert invalid_encoding.status_code == 422
            invalid_header = client.post(
                "/api/v1/accounts/import",
                headers=headers,
                content="username,password,unexpected\r\n13800138000,secret,value\r\n",
            )
            assert invalid_header.status_code == 422
            assert "secret" not in invalid_header.text

            too_many_rows = "username\r\n" + "\r\n".join(
                f"user-{index}" for index in range(501)
            )
            assert client.post(
                "/api/v1/accounts/import",
                headers=headers,
                content=too_many_rows,
            ).status_code == 413


def test_account_update_requires_auth_and_csrf_and_returns_only_masked_secrets() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            assert client.patch("/api/v1/accounts/1", json={"remark": "No auth"}).status_code == 401
            csrf_token = bootstrap_and_login(client)
            created = client.post(
                "/api/v1/accounts",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "username": "13800138000",
                    "password": "old-password",
                    "cookies": "old-cookie",
                },
            )
            assert created.status_code == 201
            account_id = created.json()["id"]

            without_csrf = client.patch(
                f"/api/v1/accounts/{account_id}",
                json={"remark": "No CSRF"},
            )
            assert without_csrf.status_code == 403

            updated = client.patch(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "username": "13900139000",
                    "password": "new-password",
                    "cookies": "new-cookie",
                    "remark": "Updated",
                    "user_agent": "Agent/Two",
                    "speed": 1.8,
                    "chapter_concurrency": 4,
                    "unopened_policy": "skip",
                    "enabled": False,
                },
            )
            assert updated.status_code == 200
            payload = updated.json()
            assert payload["username_hint"] == "139****9000"
            assert payload["remark"] == "Updated"
            assert payload["user_agent"] == "Agent/Two"
            assert payload["speed"] == 1.8
            assert payload["chapter_concurrency"] == 4
            assert payload["unopened_policy"] == "skip"
            assert payload["enabled"] is False
            assert payload["has_password"] is True
            assert payload["has_cookies"] is True
            for secret in ("13900139000", "new-password", "new-cookie"):
                assert secret not in updated.text
            for forbidden_field in (
                "username",
                "password",
                "cookies",
                "username_encrypted",
                "password_encrypted",
                "cookies_encrypted",
            ):
                assert forbidden_field not in payload

            with Session(app.state.engine) as db:
                account = db.get(Account, account_id)
                assert account is not None
                assert (
                    app.state.secret_box.decrypt(
                        account.secret.username_encrypted,
                        purpose=f"account:{account_id}:username",
                    )
                    == "13900139000"
                )
                assert (
                    app.state.secret_box.decrypt(
                        account.secret.password_encrypted,
                        purpose=f"account:{account_id}:password",
                    )
                    == "new-password"
                )
                assert (
                    app.state.secret_box.decrypt(
                        account.secret.cookies_encrypted,
                        purpose=f"account:{account_id}:cookies",
                    )
                    == "new-cookie"
                )


def test_empty_secret_updates_are_noops_and_clear_flags_are_explicit() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            created = client.post(
                "/api/v1/accounts",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "username": "13800138000",
                    "password": "keep-password",
                    "cookies": "keep-cookie",
                },
            )
            account_id = created.json()["id"]
            with Session(app.state.engine) as db:
                original = db.get(AccountSecret, account_id)
                assert original is not None
                original_password = original.password_encrypted
                original_cookies = original.cookies_encrypted

            unchanged = client.patch(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_token},
                json={"password": "", "cookies": ""},
            )
            assert unchanged.status_code == 200
            assert unchanged.json()["has_password"] is True
            assert unchanged.json()["has_cookies"] is True
            with Session(app.state.engine) as db:
                persisted = db.get(AccountSecret, account_id)
                assert persisted is not None
                assert persisted.password_encrypted == original_password
                assert persisted.cookies_encrypted == original_cookies

            contradictory = client.patch(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_token},
                json={"password": "replacement", "clear_password": True},
            )
            assert contradictory.status_code == 422
            assert "replacement" not in contradictory.text

            cleared = client.patch(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_token},
                json={"clear_password": True, "clear_cookies": True},
            )
            assert cleared.status_code == 200
            assert cleared.json()["has_password"] is False
            assert cleared.json()["has_cookies"] is False


def test_account_update_rejects_duplicate_username_without_changing_account() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            headers = {"X-CSRF-Token": csrf_token}
            first = client.post(
                "/api/v1/accounts", headers=headers, json={"username": "first-user"}
            )
            second = client.post(
                "/api/v1/accounts", headers=headers, json={"username": "second-user"}
            )
            second_id = second.json()["id"]

            conflict = client.patch(
                f"/api/v1/accounts/{second_id}",
                headers=headers,
                json={"username": " FIRST-USER ", "remark": "Must roll back"},
            )
            assert conflict.status_code == 409
            assert "first-user" not in conflict.text.lower()

            accounts = client.get("/api/v1/accounts").json()
            persisted = next(account for account in accounts if account["id"] == second_id)
            assert persisted["username_hint"] == "sec****user"
            assert persisted["remark"] == ""
            assert first.status_code == 201


def test_account_delete_requires_csrf_removes_account_and_handles_missing() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            assert client.delete("/api/v1/accounts/1").status_code == 401
            csrf_token = bootstrap_and_login(client)
            created = client.post(
                "/api/v1/accounts",
                headers={"X-CSRF-Token": csrf_token},
                json={"username": "delete-me", "password": "private"},
            )
            account_id = created.json()["id"]

            assert client.delete(f"/api/v1/accounts/{account_id}").status_code == 403
            deleted = client.delete(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_token},
            )
            assert deleted.status_code == 204
            assert deleted.content == b""
            assert client.get("/api/v1/accounts").json() == []

            missing_patch = client.patch(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_token},
                json={"remark": "missing"},
            )
            missing_delete = client.delete(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_token},
            )
            assert missing_patch.status_code == 404
            assert missing_delete.status_code == 404
