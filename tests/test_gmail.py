import base64
import json

import pytest

from calendar_agent.errors import MailNotFound, ToolError
from calendar_agent.google_auth import granted_scopes, load_credentials, required_scopes
from calendar_agent.mail_backends import GmailMail
from calendar_agent.config import Config

from test_google import HttpError, Req


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


MESSAGES = {
    "abc1": {"id": "abc1", "threadId": "th1", "internalDate": "1794301200000", "labelIds": ["UNREAD", "INBOX"],
             "snippet": "Seriez-vous disponible", "payload": {"mimeType": "multipart/alternative", "headers": [
                 {"name": "From", "value": "Claire <claire@corp.com>"}, {"name": "Subject", "value": "Entretien"},
                 {"name": "Message-ID", "value": "<orig@corp.com>"}], "parts": [
                 {"mimeType": "text/plain", "body": {"data": b64("Jeudi 15h ?")}},
                 {"mimeType": "text/html", "body": {"data": b64("<p>Jeudi 15h ?</p>")}}]}},
}


class FakeMessages:
    def __init__(self):
        self.calls = []

    def list(self, **kw):
        self.calls.append(("list", kw))
        return Req({"messages": [{"id": "abc1"}]})

    def get(self, **kw):
        self.calls.append(("get", kw))
        if kw["id"] not in MESSAGES:
            return Req(error=HttpError(404))
        return Req(MESSAGES[kw["id"]])


class FakeDrafts:
    def __init__(self):
        self.bodies = []

    def create(self, **kw):
        self.bodies.append(kw["body"])
        return Req({"id": "draft42"})


class FakeUsers:
    def __init__(self):
        self.m, self.d = FakeMessages(), FakeDrafts()

    def messages(self):
        return self.m

    def drafts(self):
        return self.d


class FakeGmail:
    def __init__(self):
        self.u = FakeUsers()

    def users(self):
        return self.u


@pytest.fixture
def gmail(config):
    service = FakeGmail()
    return GmailMail(config, service=service), service


def test_search_requests_metadata_only(gmail):
    backend, service = gmail
    out = backend.search("is:unread", 5)
    assert out[0]["subject"] == "Entretien" and out[0]["unread"] and "body" not in out[0]
    kind, kw = service.u.m.calls[0]
    assert kw["q"] == "is:unread" and kw["maxResults"] == 5 and kw["userId"] == "me"
    assert service.u.m.calls[1][1]["format"] == "metadata"


def test_get_parses_body_and_maps_404(gmail):
    backend, _ = gmail
    message = backend.get("abc1")
    assert message["body"] == "Jeudi 15h ?" and message["message_id_header"] == "<orig@corp.com>"
    with pytest.raises(MailNotFound):
        backend.get("zzz")


def test_create_draft_builds_threaded_raw_message(gmail):
    backend, service = gmail
    draft_id = backend.create_draft({"to": ["claire@corp.com"], "cc": [], "subject": "Re: Entretien", "body": "Oui.",
                                     "in_reply_to": "<orig@corp.com>", "references": "", "thread_id": "th1"})
    assert draft_id == "draft42"
    message = service.u.d.bodies[0]["message"]
    raw = base64.urlsafe_b64decode(message["raw"]).decode()
    assert message["threadId"] == "th1" and "In-Reply-To: <orig@corp.com>" in raw and "To: claire@corp.com" in raw


# ---- scopes ------------------------------------------------------------------------------------
def cfg(tmp_path, **env):
    return Config.from_env({"DATA_DIR": str(tmp_path), **env})


def test_required_scopes_follow_enabled_features(tmp_path):
    assert required_scopes(cfg(tmp_path, CALENDAR_BACKEND="memory")) == []
    only_cal = required_scopes(cfg(tmp_path))
    both = required_scopes(cfg(tmp_path, MAIL_BACKEND="gmail"))
    assert len(only_cal) == 1 and len(both) == 3 and both[0] == only_cal[0]
    assert required_scopes(cfg(tmp_path, CALENDAR_BACKEND="memory", MAIL_BACKEND="gmail")) == both[1:]


def test_old_token_without_gmail_scope_asks_for_reauth_not_403(tmp_path):
    config = cfg(tmp_path, MAIL_BACKEND="gmail")
    config.token_file.write_text(json.dumps({"scopes": ["https://www.googleapis.com/auth/calendar"], "token": "t"}))
    assert granted_scopes(config.token_file) == {"https://www.googleapis.com/auth/calendar"}
    with pytest.raises(ToolError, match="calendar-agent auth"):
        load_credentials(config, interactive=False)


def test_missing_or_corrupt_token_has_no_scopes(tmp_path):
    config = cfg(tmp_path)
    assert granted_scopes(config.token_file) == set()
    config.token_file.write_text("{oops")
    assert granted_scopes(config.token_file) == set()
