import pytest
from fastapi.testclient import TestClient

from backend import agent, feedback_store, main


@pytest.fixture
def client_and_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_memory.db"
    monkeypatch.setattr(feedback_store, "DB_PATH", test_db)
    feedback_store.init_feedback_db()

    # Keep API tests away from the real conversation database.
    monkeypatch.setattr(main, "add_interaction", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "get_active_conversation_id", lambda: None)

    return TestClient(main.app)


def test_create_and_list_feedback(client_and_test_db):
    client = client_and_test_db

    response = client.post(
        "/feedback",
        json={"transcript": "The API demo was helpful."},
    )

    assert response.status_code == 201
    record = response.json()["feedback"]
    assert record["transcript"] == "The API demo was helpful."
    assert record["id"]

    listed = client.get("/feedback")
    assert listed.status_code == 200
    assert any(
        item["id"] == record["id"]
        for item in listed.json()["feedback"]
    )


def test_get_feedback_by_id(client_and_test_db):
    client = client_and_test_db
    created = client.post(
        "/feedback",
        json={"transcript": "The registration process was confusing."},
    ).json()["feedback"]

    response = client.get(f"/feedback/{created['id']}")

    assert response.status_code == 200
    assert response.json()["feedback"]["transcript"] == (
        "The registration process was confusing."
    )


def test_update_metadata_preserves_original_transcript(client_and_test_db):
    client = client_and_test_db
    created = client.post(
        "/feedback",
        json={"transcript": "The API demo was helpful."},
    ).json()["feedback"]

    response = client.patch(
        f"/feedback/{created['id']}",
        json={
            "summary": "Positive demo feedback",
            "category": "demo",
            "transcript": "This must not replace the original.",
        },
    )

    assert response.status_code == 200
    updated = response.json()["feedback"]
    assert updated["summary"] == "Positive demo feedback"
    assert updated["category"] == "demo"
    assert updated["transcript"] == "The API demo was helpful."


def test_empty_metadata_update_is_rejected(client_and_test_db):
    client = client_and_test_db
    created = client.post(
        "/feedback",
        json={"transcript": "Keep this feedback."},
    ).json()["feedback"]

    response = client.patch(f"/feedback/{created['id']}", json={})

    assert response.status_code == 400


def test_feedback_validation_rejects_blank_transcript(client_and_test_db):
    response = client_and_test_db.post(
        "/feedback",
        json={"transcript": "   "},
    )

    assert response.status_code == 422


def test_missing_feedback_returns_404(client_and_test_db):
    client = client_and_test_db

    assert client.get("/feedback/not-a-real-id").status_code == 404
    assert client.patch(
        "/feedback/not-a-real-id",
        json={"summary": "Updated"},
    ).status_code == 404


def test_feedback_search(client_and_test_db):
    client = client_and_test_db
    client.post(
        "/feedback",
        json={"transcript": "The registration flow was confusing."},
    )

    response = client.get(
        "/feedback/search",
        params={"q": "registration"},
    )

    assert response.status_code == 200
    assert response.json()["total"] >= 1
    assert any(
        "registration" in item["transcript"].lower()
        for item in response.json()["feedback"]
    )


def test_delete_feedback(client_and_test_db):
    client = client_and_test_db
    created = client.post(
        "/feedback",
        json={"transcript": "Temporary feedback."},
    ).json()["feedback"]

    response = client.delete(f"/feedback/{created['id']}")

    assert response.status_code == 200
    assert client.get(f"/feedback/{created['id']}").status_code == 404


@pytest.mark.parametrize(
    ("command", "expected_transcript"),
    [
        (
            "Record feedback: The demo was helpful.",
            "The demo was helpful.",
        ),
        (
            "save this feedback: Registration was confusing.",
            "Registration was confusing.",
        ),
        (
            "Capture hackathon feedback: Judges gave useful advice.",
            "Judges gave useful advice.",
        ),
    ],
)
def test_voice_feedback_commands_save_without_llm(
    client_and_test_db, monkeypatch, command, expected_transcript
):
    def fail_if_llm_called(*args, **kwargs):
        raise AssertionError("Feedback command unexpectedly called the LLM")

    monkeypatch.setattr(agent, "ask_qwen", fail_if_llm_called)

    response = client_and_test_db.post("/chat", json={"message": command})

    assert response.status_code == 200
    assert "saved successfully" in response.json()["reply"].lower()

    records = feedback_store.list_feedback()
    assert any(item["transcript"] == expected_transcript for item in records)


def test_empty_voice_feedback_does_not_save(client_and_test_db, monkeypatch):
    def fail_if_llm_called(*args, **kwargs):
        raise AssertionError("Empty feedback command unexpectedly called the LLM")

    monkeypatch.setattr(agent, "ask_qwen", fail_if_llm_called)

    before = len(feedback_store.list_feedback())
    response = client_and_test_db.post(
        "/chat",
        json={"message": "Capture hackathon feedback:"},
    )

    assert response.status_code == 200
    assert "tell me the feedback" in response.json()["reply"].lower()
    assert len(feedback_store.list_feedback()) == before


def test_normal_chat_still_uses_regular_assistant_flow(
    client_and_test_db, monkeypatch
):
    monkeypatch.setattr(
        main,
        "ask_agent",
        lambda message: {
            "reply": "Normal assistant response.",
            "toolCall": None,
        },
    )

    response = client_and_test_db.post(
        "/chat",
        json={"message": "Explain what an API is."},
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Normal assistant response."