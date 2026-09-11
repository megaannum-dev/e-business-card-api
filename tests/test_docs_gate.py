import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings


def _app_for(deploy_env: str) -> FastAPI:
    settings = Settings(_env_file=None, deploy_env=deploy_env)
    app = FastAPI(
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


class DocsGateTests(unittest.TestCase):
    def test_enable_docs_off_only_for_prod(self) -> None:
        self.assertFalse(Settings(_env_file=None, deploy_env="prod").enable_docs)
        self.assertFalse(Settings(_env_file=None, deploy_env="PROD").enable_docs)

    def test_enable_docs_on_for_dev_and_local(self) -> None:
        self.assertTrue(Settings(_env_file=None, deploy_env="dev").enable_docs)
        self.assertTrue(Settings(_env_file=None, deploy_env="local").enable_docs)

    def test_prod_hides_swagger_but_keeps_health(self) -> None:
        client = TestClient(_app_for("prod"))
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/docs").status_code, 404)
        self.assertEqual(client.get("/openapi.json").status_code, 404)
        self.assertEqual(client.get("/redoc").status_code, 404)

    def test_dev_keeps_swagger_and_health(self) -> None:
        client = TestClient(_app_for("dev"))
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/docs").status_code, 200)
        self.assertEqual(client.get("/openapi.json").status_code, 200)


if __name__ == "__main__":
    unittest.main()
