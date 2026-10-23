
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_document_list():
    response = client.get("/documents")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_unknown_document_version():
    response = client.post("/documents/ingest/99")
    assert response.status_code == 404


def test_sections_require_ingestion():
    response = client.get("/documents/sections?version=1")
    assert response.status_code == 409
