from datetime import datetime, timedelta

import pytest

from calendar_agent.errors import MailNotFound
from calendar_agent.mail_backends import InMemoryMail
from calendar_agent.registry import registry
from calendar_agent.tools.mail import UNTRUSTED_HEADER

from conftest import NOW


@pytest.fixture
def seeded(mail):
    mail.add("Claire <claire@corp.com>", "Entretien stage", "Disponible jeudi 15h ?", NOW - timedelta(hours=2),
             reply_to="Claire RH <rh@corp.com>")
    mail.add("Prof <prof@u.fr>", "Rendu projet", "Vendredi 23h59.", NOW - timedelta(days=1), unread=False,
             attachments=["consignes.pdf"])
    return mail


def test_search_lists_newest_first_and_marks_untrusted(call, seeded):
    out = call("search_mail", query="")
    assert out.startswith(UNTRUSTED_HEADER) and out.index("Entretien") < out.index("Rendu")
    assert "* id=m1" in out and "  id=m2" in out
    assert "2026-11-10 07:00" in out  # shown in local time


def test_search_filters(call, seeded):
    assert "Rendu" not in call("search_mail", query="is:unread")
    assert "Rendu" in call("search_mail", query="from:prof")
    assert call("search_mail", query="zzz") == "No messages found."
    assert "between 1 and 20" in call("search_mail", max_results=99)


def test_read_shows_attachments_names_and_header(call, seeded):
    out = call("read_mail", message_id="m2")
    assert out.startswith(UNTRUSTED_HEADER) and "consignes.pdf" in out and "Vendredi 23h59" in out


def test_read_cleans_and_truncates(call, mail):
    mail.add("x@y.com", "Long", "A" * 20000 + "​")
    out = call("read_mail", message_id="m1")
    assert "[...truncated]" in out and len(out) < 8000 and "​" not in out


def test_read_rejects_bad_and_unknown_ids(call, seeded):
    assert call("read_mail", message_id="../etc").startswith("Error")
    assert call("read_mail", message_id="m99").startswith("Error: ") or "not found" in call("read_mail", message_id="m99")
    with pytest.raises(MailNotFound):
        seeded.get("m99")


def test_mail_disabled_is_a_clear_error(call, ctx):
    ctx.mail = None
    assert "MAIL_BACKEND=gmail" in call("search_mail")


# ---- drafts ---------------------------------------------------------------------------------------
def test_draft_needs_approval_and_shows_recipient_and_text(call, seeded, store):
    out = call("draft_email", to="bob@x.com", subject="Hello", body="Hi Bob,\nsee you.")
    assert "APPROVAL REQUIRED" in out and seeded.drafts == []
    summary = store.pending_for_session("sess-0001")[0]["summary"]
    assert "To: bob@x.com" in summary and "Subject: Hello" in summary and "see you." in summary
    assert "will not be sent" in summary


def test_draft_created_only_when_approved(call, seeded):
    args = dict(to="bob@x.com", subject="Hello", body="Hi")
    assert "NOTHING was sent" in call("draft_email", approved=True, **args)
    assert seeded.drafts == [{"to": ["bob@x.com"], "cc": [], "subject": "Hello", "body": "Hi"}]


def test_reply_uses_original_sender_reply_to_and_thread(call, seeded, store):
    call("draft_email", reply_to_message_id="m1", body="Oui, jeudi 15h me convient.")
    assert "To: rh@corp.com" in store.pending_for_session("sess-0001")[0]["summary"]
    call("draft_email", approved=True, reply_to_message_id="m1", body="Oui, jeudi 15h me convient.")
    d = seeded.drafts[0]
    assert d["to"] == ["rh@corp.com"] and d["subject"] == "Re: Entretien stage"
    assert d["in_reply_to"] == "<m1@example.com>" and d["thread_id"] == "t1"


def test_draft_validation(call, seeded):
    assert "required" in call("draft_email", subject="x", body="y")
    assert "Invalid e-mail" in call("draft_email", to="nope", subject="x", body="y")
    assert "must be a single line" in call("draft_email", to="a@x.com\nBcc: e@x.com", subject="x", body="y")
    assert "empty" in call("draft_email", to="a@x.com", subject="x", body="  ")
    assert "too long" in call("draft_email", to="a@x.com", subject="x", body="z" * 20000)
    assert call("draft_email", reply_to_message_id="m99", body="hi").startswith("Error")


def test_subject_header_injection_is_flattened(call, seeded):
    call("draft_email", approved=True, to="a@x.com", subject="Hi\nBcc: evil@x.com", body="y")
    assert "\n" not in seeded.drafts[0]["subject"]


def test_there_is_no_send_capability(mail):
    from calendar_agent.mail_backends import GmailMail
    for backend in (InMemoryMail, GmailMail):
        assert not any("send" in name for name in dir(backend) if not name.startswith("_"))
    assert not any("send" in name for name in registry.names())


def test_sample_mailbox_contains_injection_demo(ctx):
    sample = InMemoryMail.sample(NOW)
    hits = sample.search("assistant", 10)
    assert hits and "attacker" in sample.get(hits[0]["id"])["body"]
