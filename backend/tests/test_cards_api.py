from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_client(temp_dir: str) -> tuple[FastAPI, TestClient]:
    data_dir = Path(temp_dir) / "data"
    database_path = data_dir / "app.db"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{database_path.as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


ADMIN_PASSWORD = "correct-horse-battery-staple"
USER_NAME = "student01"
USER_PASSWORD = "user-password-01"


def bootstrap_admin(client: TestClient) -> str:
    assert (
        client.post(
            "/api/v1/auth/setup", json={"username": "admin", "password": ADMIN_PASSWORD}
        ).status_code
        == 201
    )
    return login_admin(client)


def login_admin(client: TestClient) -> str:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert login.status_code == 200
    return login.json()["csrf_token"]


def create_app_user(client: TestClient, csrf: str, username: str = USER_NAME) -> dict[str, object]:
    response = client.post(
        "/api/v1/app-users",
        headers={"X-CSRF-Token": csrf},
        json={"username": username, "password": USER_PASSWORD},
    )
    assert response.status_code == 201
    return response.json()


def login_app_user(client: TestClient, username: str = USER_NAME) -> str:
    login = client.post(
        "/api/v1/auth/login", json={"username": username, "password": USER_PASSWORD}
    )
    assert login.status_code == 200
    return login.json()["csrf_token"]


def create_owned_account(client: TestClient, csrf: str) -> int:
    response = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={"username": "13800001234", "password": "cx-secret"},
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def task_payload(account_id: int) -> dict[str, object]:
    return {
        "account_id": account_id,
        "course_id": "246831735",
        "class_id": "107515845",
        "cpi": "338350298",
        "course_title": "Fixture Course",
        "chapters": [{"chapter_id": "913820156", "title": "Chapter One", "position": 0}],
    }


def generate_card(
    client: TestClient, csrf: str, kind: str, value: int, count: int = 1
) -> list[dict[str, object]]:
    response = client.post(
        "/api/v1/cards",
        headers={"X-CSRF-Token": csrf},
        json={"kind": kind, "value": value, "count": count, "batch": "test-batch"},
    )
    assert response.status_code == 201
    return response.json()["items"]


def test_admin_creates_user_and_user_logs_in() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            admin_csrf = bootstrap_admin(client)
            created = create_app_user(client, admin_csrf)
            assert created["username"] == USER_NAME
            assert created["has_password"] is True
            assert created["task_credits"] == 0

            # duplicate username and admin-name collision are both rejected
            assert (
                client.post(
                    "/api/v1/app-users",
                    headers={"X-CSRF-Token": admin_csrf},
                    json={"username": USER_NAME, "password": USER_PASSWORD},
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/v1/app-users",
                    headers={"X-CSRF-Token": admin_csrf},
                    json={"username": "admin", "password": USER_PASSWORD},
                ).status_code
                == 409
            )

            user_csrf = login_app_user(client)
            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["kind"] == "app_user"
            assert me.json()["user"]["username"] == USER_NAME
            assert user_csrf  # 会话可用


def test_task_creation_gated_by_entitlement_and_count_card() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            admin_csrf = bootstrap_admin(client)
            create_app_user(client, admin_csrf)
            user_csrf = login_app_user(client)
            account_id = create_owned_account(client, user_csrf)

            # no entitlement: 402
            blocked = client.post(
                "/api/v1/tasks", headers={"X-CSRF-Token": user_csrf}, json=task_payload(account_id)
            )
            assert blocked.status_code == 402

            # redeem three 1-credit cards
            admin_csrf = login_admin(client)
            cards = generate_card(client, admin_csrf, kind="count", value=1, count=3)
            user_csrf = login_app_user(client)
            for card in cards:
                redeemed = client.post(
                    "/api/v1/cards/redeem",
                    headers={"X-CSRF-Token": user_csrf},
                    json={"code": card["code"]},
                )
                assert redeemed.status_code == 200
            status = client.get("/api/v1/cards/me")
            assert status.status_code == 200
            assert status.json()["task_credits"] == 3
            assert status.json()["active"] is True

            # create then cancel three times: credits must be exhausted exactly
            for _ in range(3):
                task = client.post(
                    "/api/v1/tasks",
                    headers={"X-CSRF-Token": user_csrf},
                    json=task_payload(account_id),
                )
                assert task.status_code == 201, task.text
                cancel = client.post(
                    f"/api/v1/tasks/{task.json()['id']}/cancel",
                    headers={"X-CSRF-Token": user_csrf},
                )
                assert cancel.status_code == 200

            exhausted = client.post(
                "/api/v1/tasks", headers={"X-CSRF-Token": user_csrf}, json=task_payload(account_id)
            )
            assert exhausted.status_code == 402

            me = client.get("/api/v1/app-users/me")
            assert me.json()["task_credits"] == 0


def test_time_card_extends_plan_and_skips_credit_burn() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            admin_csrf = bootstrap_admin(client)
            create_app_user(client, admin_csrf)
            card = generate_card(client, admin_csrf, kind="time", value=7)[0]

            user_csrf = login_app_user(client)
            account_id = create_owned_account(client, user_csrf)
            redeemed = client.post(
                "/api/v1/cards/redeem",
                headers={"X-CSRF-Token": user_csrf},
                json={"code": card["code"]},
            )
            assert redeemed.status_code == 200
            expires_at = datetime.fromisoformat(redeemed.json()["plan_expires_at"])
            remaining = expires_at - datetime.now(UTC).replace(tzinfo=expires_at.tzinfo)
            assert timedelta(6) < remaining <= timedelta(7)
            assert redeemed.json()["task_credits"] == 0

            # with an active time card, task creation is allowed at 0 credits
            task = client.post(
                "/api/v1/tasks", headers={"X-CSRF-Token": user_csrf}, json=task_payload(account_id)
            )
            assert task.status_code == 201, task.text

            # time card still grants access after cancelling
            assert (
                client.post(
                    f"/api/v1/tasks/{task.json()['id']}/cancel",
                    headers={"X-CSRF-Token": user_csrf},
                ).status_code
                == 200
            )
            again = client.post(
                "/api/v1/tasks", headers={"X-CSRF-Token": user_csrf}, json=task_payload(account_id)
            )
            assert again.status_code == 201


def test_redeem_rejects_used_revoked_and_admin_sessions() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            admin_csrf = bootstrap_admin(client)
            create_app_user(client, admin_csrf)
            card = generate_card(client, admin_csrf, kind="count", value=5)[0]
            revoked_card = generate_card(client, admin_csrf, kind="count", value=5)[0]
            listed = client.get("/api/v1/cards?status_filter=unused")
            assert listed.status_code == 200
            target = next(
                item
                for item in listed.json()
                if item["code_hint"].endswith(revoked_card["code"][-4:])
            )
            assert (
                client.post(
                    f"/api/v1/cards/{target['id']}/revoke", headers={"X-CSRF-Token": admin_csrf}
                ).status_code
                == 200
            )

            user_csrf = login_app_user(client)

            # admin session cannot redeem (switch back to admin first)
            assert (
                client.post(
                    "/api/v1/cards/redeem",
                    headers={"X-CSRF-Token": login_admin(client)},
                    json={"code": card["code"]},
                ).status_code
                == 403
            )

            user_csrf = login_app_user(client)
            # redeem once (lenient input: lowercase, no dashes)
            assert (
                client.post(
                    "/api/v1/cards/redeem",
                    headers={"X-CSRF-Token": user_csrf},
                    json={"code": card["code"].lower().replace("-", "")},
                ).status_code
                == 200
            )
            # double redeem is rejected
            dup = client.post(
                "/api/v1/cards/redeem",
                headers={"X-CSRF-Token": user_csrf},
                json={"code": card["code"]},
            )
            assert dup.status_code == 409
            # 已作废
            revoked_attempt = client.post(
                "/api/v1/cards/redeem",
                headers={"X-CSRF-Token": user_csrf},
                json={"code": revoked_card["code"]},
            )
            assert revoked_attempt.status_code == 409


def test_admin_can_reset_password_and_adjust_entitlement() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            admin_csrf = bootstrap_admin(client)
            created = create_app_user(client, admin_csrf)
            user_id = int(created["id"])

            # separate client holds the old session to verify revocation
            user_client = TestClient(app)
            login_app_user(user_client)
            assert user_client.get("/api/v1/auth/me").status_code == 200

            patched = client.patch(
                f"/api/v1/app-users/{user_id}",
                headers={"X-CSRF-Token": admin_csrf},
                json={
                    "password": "brand-new-password-02",
                    "plan_extend_days": 30,
                    "task_credits_add": 10,
                },
            )
            assert patched.status_code == 200
            assert patched.json()["task_credits"] == 10
            assert patched.json()["plan_expires_at"] is not None

            # old session revoked, old password rejected, new password works
            assert user_client.get("/api/v1/auth/me").status_code == 401
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": USER_NAME, "password": USER_PASSWORD}
                ).status_code
                == 401
            )
            new_login = client.post(
                "/api/v1/auth/login",
                json={"username": USER_NAME, "password": "brand-new-password-02"},
            )
            assert new_login.status_code == 200

            # negative credits floor at zero; disabled user cannot log in
            assert (
                client.patch(
                    f"/api/v1/app-users/{user_id}",
                    headers={"X-CSRF-Token": login_admin(client)},
                    json={"task_credits_add": -999},
                ).status_code
                == 200
            )
            listed = client.get("/api/v1/app-users")
            assert listed.json()[0]["task_credits"] == 0
            assert (
                client.patch(
                    f"/api/v1/app-users/{user_id}",
                    headers={"X-CSRF-Token": login_admin(client)},
                    json={"disabled": True},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/v1/auth/login",
                    json={"username": USER_NAME, "password": "brand-new-password-02"},
                ).status_code
                == 401
            )
