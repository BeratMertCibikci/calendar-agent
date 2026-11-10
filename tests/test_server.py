import base64


def _all(cal):
    return cal.list_events("2026-01-01T00:00:00+01:00", "2027-01-01T00:00:00+01:00")


def chat(client, msg, **extra):
    return client.jpost("/api/chat", {"session_id": client.sid, "message": msg, **extra})


def test_index_injects_token_and_is_not_cached(client):
    r = client.get("/")
    assert r.status_code == 200 and b"__CSRF__" not in r.data
    assert r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_post_requires_csrf_token(client):
    r = client.jpost("/api/chat", {"session_id": client.sid, "message": "help"}, token_=False)
    assert r.status_code == 403


def test_foreign_host_rejected(client):
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403


def test_foreign_origin_rejected(client):
    r = client.jpost("/api/chat", {"session_id": client.sid, "message": "help"},
                     headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_requires_json_content_type(client):
    r = client.post("/api/chat", data="message=hi", headers={"X-CSRF-Token": client.token})
    assert r.status_code == 415


def test_bad_session_id(client):
    assert client.jpost("/api/chat", {"session_id": "../x", "message": "help"}).status_code == 400
    assert client.get("/api/session/%24%24").status_code in (400, 404)


def test_empty_message(client):
    assert chat(client, "  ").status_code == 400


def test_image_validation(client):
    bad_type = {"mime_type": "text/html", "data": "AAAA"}
    assert chat(client, "x", image=bad_type).status_code == 400
    assert chat(client, "x", image={"mime_type": "image/png", "data": "***"}).status_code == 400
    big = base64.b64encode(b"0" * (6 * 1024 * 1024 + 1)).decode()
    assert chat(client, "x", image={"mime_type": "image/png", "data": big}).status_code == 400


def test_create_and_list_via_demo_engine(client):
    r = chat(client, "add Lab on 2026-11-12 14:00 for 90 min")
    assert r.status_code == 200 and "Created" in r.get_json()["reply"]
    assert "Lab" in chat(client, "events").get_json()["reply"]


def test_delete_needs_approval_then_executes_once(client, calendar):
    chat(client, "add Lab on 2026-11-12 14:00")
    body = chat(client, "delete evt1").get_json()
    assert len(body["pending"]) == 1 and len(_all(calendar)) == 1  # nothing deleted yet
    aid = body["pending"][0]["id"]
    ok = client.jpost(f"/api/actions/{aid}/approve", {"session_id": client.sid})
    assert ok.status_code == 200 and ok.get_json()["ok"] and not _all(calendar)
    again = client.jpost(f"/api/actions/{aid}/approve", {"session_id": client.sid})
    assert again.status_code == 409  # double click / replay is refused


def test_reject_does_not_execute_and_is_reported_to_model(client, calendar, store):
    chat(client, "add Lab on 2026-11-12 14:00")
    aid = chat(client, "delete evt1").get_json()["pending"][0]["id"]
    assert client.jpost(f"/api/actions/{aid}/reject", {"session_id": client.sid}).status_code == 200
    assert len(_all(calendar)) == 1
    assert store.pop_notes(client.sid) and not store.pop_notes(client.sid)


def test_other_session_cannot_approve(client, calendar):
    chat(client, "add Lab on 2026-11-12 14:00")
    aid = chat(client, "delete evt1").get_json()["pending"][0]["id"]
    r = client.jpost(f"/api/actions/{aid}/approve", {"session_id": "zzzzzzzz99999999"})
    assert r.status_code == 409 and len(_all(calendar)) == 1


def test_expired_action_cannot_be_approved(client, store, monkeypatch):
    chat(client, "add Lab on 2026-11-12 14:00")
    aid = chat(client, "delete evt1").get_json()["pending"][0]["id"]
    real = store.clock()
    monkeypatch.setattr(store, "clock", lambda: real + store.action_ttl + 5)
    assert client.jpost(f"/api/actions/{aid}/approve", {"session_id": client.sid}).status_code == 409


def test_session_endpoint_restores_transcript_and_pending(client):
    chat(client, "add Lab on 2026-11-12 14:00")
    chat(client, "delete evt1")
    body = client.get(f"/api/session/{client.sid}").get_json()
    assert [m["role"] for m in body["transcript"]][:2] == ["user", "assistant"]
    assert len(body["pending"]) == 1


def test_notice_prefix_is_hidden_from_demo_engine(client):
    chat(client, "add Lab on 2026-11-12 14:00")
    aid = chat(client, "delete evt1").get_json()["pending"][0]["id"]
    client.jpost(f"/api/actions/{aid}/reject", {"session_id": client.sid})
    assert "Created" in chat(client, "add Next on 2026-11-13 10:00").get_json()["reply"]
