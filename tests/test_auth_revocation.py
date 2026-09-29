"""Auth dependency behaviour: revocation checks and 401 detail messages.

The app relies on the exact `detail` strings here -- src/api/client.ts shows the
server's 401 reason to the user verbatim -- so these assert wording, not just
status codes.
"""

import unittest
from unittest import mock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth

from app.core.auth import get_current_user_id, get_current_user_id_strict


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/fast")
    async def fast(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
        return {"user_id": user_id}

    @app.get("/strict")
    async def strict(user_id: str = Depends(get_current_user_id_strict)) -> dict[str, str]:
        return {"user_id": user_id}

    return TestClient(app)


AUTH = {"Authorization": "Bearer fake-token"}


class AuthRevocationTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("app.core.auth.is_firebase_initialized", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _client()

    def test_fast_path_does_not_check_revocation(self) -> None:
        with mock.patch.object(
            firebase_auth, "verify_id_token", return_value={"uid": "u1"}
        ) as verify:
            response = self.client.get("/fast", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"user_id": "u1"})
        self.assertIs(verify.call_args.kwargs["check_revoked"], False)

    def test_strict_path_checks_revocation(self) -> None:
        with mock.patch.object(
            firebase_auth, "verify_id_token", return_value={"uid": "u1"}
        ) as verify:
            response = self.client.get("/strict", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertIs(verify.call_args.kwargs["check_revoked"], True)

    def test_revoked_token_reports_a_revoked_session(self) -> None:
        # RevokedIdTokenError subclasses InvalidIdTokenError: if the handlers are
        # ever reordered this silently degrades to "Invalid or expired token."
        error = firebase_auth.RevokedIdTokenError("The Firebase ID token has been revoked.")
        with mock.patch.object(firebase_auth, "verify_id_token", side_effect=error):
            response = self.client.get("/strict", headers=AUTH)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Session revoked. Please sign in again.")

    def test_disabled_account_is_reported_distinctly(self) -> None:
        error = firebase_auth.UserDisabledError("The user record is disabled.")
        with mock.patch.object(firebase_auth, "verify_id_token", side_effect=error):
            response = self.client.get("/strict", headers=AUTH)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "This account has been disabled.")

    def test_ordinary_invalid_token_keeps_the_generic_message(self) -> None:
        error = firebase_auth.InvalidIdTokenError("malformed token")
        with mock.patch.object(firebase_auth, "verify_id_token", side_effect=error):
            response = self.client.get("/strict", headers=AUTH)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid or expired token.")

    def test_audience_mismatch_names_the_firebase_project(self) -> None:
        error = firebase_auth.InvalidIdTokenError("Firebase ID token has incorrect aud claim.")
        with mock.patch.object(firebase_auth, "verify_id_token", side_effect=error):
            response = self.client.get("/fast", headers=AUTH)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Firebase project mismatch", response.json()["detail"])

    def test_missing_authorization_header_is_rejected(self) -> None:
        for path in ("/fast", "/strict"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json()["detail"], "Missing or invalid authorization header."
                )

    def test_token_without_uid_is_rejected(self) -> None:
        with mock.patch.object(firebase_auth, "verify_id_token", return_value={}):
            response = self.client.get("/strict", headers=AUTH)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid token payload.")

    def test_unconfigured_firebase_returns_503(self) -> None:
        with mock.patch("app.core.auth.is_firebase_initialized", return_value=False):
            response = self.client.get("/strict", headers=AUTH)
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
