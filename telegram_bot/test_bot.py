"""Automated tests for the Telegram bot core functions."""

import datetime
import sys
import os

import pytest
import pytest_asyncio

# Ensure telegram_bot directory is on the path
sys.path.insert(0, os.path.dirname(__file__))

# Set required env vars before importing modules that read config
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("AI_API_KEY", "test-key")
os.environ.setdefault("OWNER_ID", "123456")
os.environ.setdefault("AUTH_PASSWORD", "testpass")

from handlers import (
    _parse_period,
    _format_timedelta,
    _is_authenticated,
    _strip_iris_prefix,
    _IRIS_PREFIX_RE,
    _IRIS_COMMANDS,
    _authenticated_users,
    OWNER_ID,
)
from ai_client import classify_intent


# ── _parse_period tests ──────────────────────────────────────────────


class TestParsePeriod:
    def test_minutes(self):
        td = _parse_period("5 минут")
        assert td == datetime.timedelta(minutes=5)

    def test_minutes_short(self):
        td = _parse_period("30 мин")
        assert td == datetime.timedelta(minutes=30)

    def test_minutes_m(self):
        td = _parse_period("10 м")
        assert td == datetime.timedelta(minutes=10)

    def test_hours(self):
        td = _parse_period("2 часа")
        assert td == datetime.timedelta(hours=2)

    def test_hours_short(self):
        td = _parse_period("1 ч")
        assert td == datetime.timedelta(hours=1)

    def test_days(self):
        td = _parse_period("3 дня")
        assert td == datetime.timedelta(days=3)

    def test_days_sutki(self):
        td = _parse_period("2 суток")
        assert td == datetime.timedelta(days=2)

    def test_weeks(self):
        td = _parse_period("1 неделя")
        assert td == datetime.timedelta(weeks=1)

    def test_months(self):
        td = _parse_period("2 месяца")
        assert td == datetime.timedelta(days=60)

    def test_years(self):
        td = _parse_period("1 год")
        assert td == datetime.timedelta(days=365)

    def test_no_match(self):
        assert _parse_period("привет") is None

    def test_empty(self):
        assert _parse_period("") is None

    def test_embedded_period(self):
        td = _parse_period("забанить на 7 дней за спам")
        assert td == datetime.timedelta(days=7)


# ── _format_timedelta tests ──────────────────────────────────────────


class TestFormatTimedelta:
    def test_minutes(self):
        assert _format_timedelta(datetime.timedelta(minutes=45)) == "45 мин."

    def test_hours(self):
        assert _format_timedelta(datetime.timedelta(hours=3)) == "3 ч."

    def test_days(self):
        assert _format_timedelta(datetime.timedelta(days=5)) == "5 дн."


# ── _is_authenticated tests ─────────────────────────────────────────


class TestIsAuthenticated:
    def setup_method(self):
        _authenticated_users.clear()

    def test_owner_always_authenticated(self):
        assert _is_authenticated(OWNER_ID) is True

    def test_unauthenticated_user(self):
        assert _is_authenticated(999999) is False

    def test_authenticated_user(self):
        _authenticated_users.add(42)
        assert _is_authenticated(42) is True


# ── Iris prefix and command tests ────────────────────────────────────


class TestIrisPrefix:
    def test_exclamation(self):
        assert _IRIS_PREFIX_RE.match("!варн спам") is not None

    def test_dot(self):
        assert _IRIS_PREFIX_RE.match(".бан 2 дня") is not None

    def test_slash(self):
        assert _IRIS_PREFIX_RE.match("/кик") is not None

    def test_iris_word(self):
        assert _IRIS_PREFIX_RE.match("Ирис варн") is not None

    def test_iriska_word(self):
        assert _IRIS_PREFIX_RE.match("Ириска мут 1 час") is not None

    def test_no_prefix(self):
        assert _IRIS_PREFIX_RE.match("привет как дела") is None

    def test_case_insensitive(self):
        assert _IRIS_PREFIX_RE.match("ИРИС бан") is not None


class TestStripIrisPrefix:
    def test_exclamation(self):
        assert _strip_iris_prefix("!варн спам") == "варн спам"

    def test_dot(self):
        assert _strip_iris_prefix(".бан") == "бан"

    def test_iris_word(self):
        assert _strip_iris_prefix("Ирис кик") == "кик"

    def test_iriska(self):
        assert _strip_iris_prefix("Ириска мут 1 час") == "мут 1 час"

    def test_no_prefix(self):
        assert _strip_iris_prefix("привет") == "привет"


class TestIrisCommands:
    """Test that Iris command patterns match expected inputs."""

    def _match_command(self, text: str) -> str | None:
        for pattern, ctype in _IRIS_COMMANDS:
            if pattern.search(text):
                return ctype
        return None

    def test_warn(self):
        assert self._match_command("варн спам") == "WARN"

    def test_warn_limit(self):
        assert self._match_command("варн лимит 5") == "WARN_LIMIT"

    def test_warnlist(self):
        assert self._match_command("варнлист") == "WARNLIST"

    def test_my_warns(self):
        assert self._match_command("мои варны") == "MY_WARNS"

    def test_unwarn(self):
        assert self._match_command("-варн") == "UNWARN"

    def test_clear_warns(self):
        assert self._match_command("снять все варны") == "CLEAR_WARNS"

    def test_mute(self):
        assert self._match_command("мут 1 час") == "MUTE"

    def test_mute_zaткни(self):
        assert self._match_command("заткни 30 минут") == "MUTE"

    def test_unmute(self):
        assert self._match_command("-мут") == "UNMUTE"
        assert self._match_command("размут") == "UNMUTE"

    def test_mutelist(self):
        assert self._match_command("муты") == "MUTELIST"

    def test_ban(self):
        assert self._match_command("бан 1 день реклама") == "BAN"

    def test_ban_no_args(self):
        assert self._match_command("бан") == "BAN"

    def test_unban(self):
        assert self._match_command("-бан") == "UNBAN"
        assert self._match_command("разбан") == "UNBAN"

    def test_banlist(self):
        assert self._match_command("банлист") == "BANLIST"

    def test_kick(self):
        assert self._match_command("кик") == "KICK"

    def test_kick_with_reason(self):
        assert self._match_command("кик спам") == "KICK"

    def test_who_admin(self):
        assert self._match_command("кто админ") == "WHO_ADMIN"
        assert self._match_command("а судьи кто") == "WHO_ADMIN"

    def test_call_admins(self):
        assert self._match_command("позвать админов") == "CALL_ADMINS"
        assert self._match_command("созвать модеров") == "CALL_ADMINS"

    def test_mod_log(self):
        assert self._match_command("модер лог") == "MOD_LOG"

    def test_no_match(self):
        assert self._match_command("привет как дела") is None


# ── classify_intent tests ────────────────────────────────────────────


class TestClassifyIntent:
    def test_dialog_read(self):
        assert classify_intent("что происходит в чате?") == "DIALOG_READ"
        assert classify_intent("кто писал?") == "DIALOG_READ"

    def test_priority(self):
        assert classify_intent("Срочно!") == "PRIORITY"
        assert classify_intent("приоритет чатов") == "PRIORITY"

    def test_note(self):
        assert classify_intent("Запомни купить молоко") == "NOTE"
        assert classify_intent("мои заметки") == "NOTE"

    def test_autoreply(self):
        assert classify_intent("включи автоответчик") == "AUTOREPLY"
        assert classify_intent("выключи автоответ") == "AUTOREPLY"

    def test_broadcast(self):
        assert classify_intent("напиши всем привет") == "BROADCAST"

    def test_deleted(self):
        assert classify_intent("покажи удалённые сообщения") == "DELETED"

    def test_pending(self):
        assert classify_intent("кому нужно ответить") == "PENDING"

    def test_stats(self):
        assert classify_intent("статистика") == "STATS"

    def test_remind_set(self):
        assert classify_intent("напомни мне через 30 минут") == "REMIND_SET"

    def test_remind_list(self):
        assert classify_intent("мои напоминания") == "REMIND_LIST"

    def test_search(self):
        assert classify_intent("найди сообщения про кота") == "SEARCH"

    def test_export(self):
        assert classify_intent("экспортируй чат") == "EXPORT"

    def test_summary(self):
        assert classify_intent("сводка за час") == "SUMMARY"

    def test_send_message(self):
        assert classify_intent("напиши @ivan привет") == "MESSAGE_SEND"

    def test_general(self):
        assert classify_intent("сколько будет 2+2?") == "GENERAL"
        assert classify_intent("расскажи анекдот") == "GENERAL"

    def test_contact(self):
        assert classify_intent("расскажи про @ivan") == "CONTACT_INFO"


# ── Database tests (in-memory SQLite) ────────────────────────────────


@pytest_asyncio.fixture
async def test_db(tmp_path):
    import database as db_mod

    db_mod.DB_PATH = str(tmp_path / "test.db")
    await db_mod.init_db()
    return db_mod


@pytest.mark.asyncio
async def test_save_and_get_message(test_db):
    now = datetime.datetime.now().isoformat()
    await test_db.save_message(
        business_connection_id="bc1",
        chat_id=100,
        message_id=1,
        user_id=42,
        username="testuser",
        first_name="Test",
        text="hello",
        media_type=None,
        file_id=None,
        caption=None,
        date=now,
    )
    msgs = await test_db.get_recent_messages(100, limit=5)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_add_and_get_warnings(test_db):
    await test_db.add_warning(
        chat_id=100, user_id=42, reason="spam", warned_by=1
    )
    warns = await test_db.get_warnings(100, 42)
    assert len(warns) == 1
    assert warns[0]["reason"] == "spam"


@pytest.mark.asyncio
async def test_set_and_get_moderation(test_db):
    await test_db.set_moderation(100, is_enabled=True)
    mod = await test_db.get_moderation(100)
    assert mod is not None
    assert mod["is_enabled"] == 1


@pytest.mark.asyncio
async def test_moderation_warn_limit(test_db):
    await test_db.set_moderation(100, warn_limit=5)
    mod = await test_db.get_moderation(100)
    assert mod["warn_limit"] == 5


@pytest.mark.asyncio
async def test_save_reminder(test_db):
    dt = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
    await test_db.add_reminder(
        owner_id=42,
        chat_id=100,
        target_name="Bob",
        text="call Bob",
        remind_at=dt.isoformat(),
    )
    reminders = await test_db.get_pending_reminders(42)
    assert len(reminders) >= 1
    assert reminders[0]["reminder_text"] == "call Bob"


@pytest.mark.asyncio
async def test_save_note(test_db):
    await test_db.add_note(owner_id=42, text="buy milk")
    notes = await test_db.get_notes(42)
    assert len(notes) == 1
    assert notes[0]["text"] == "buy milk"


@pytest.mark.asyncio
async def test_delete_note(test_db):
    await test_db.add_note(owner_id=42, text="temp note")
    notes = await test_db.get_notes(42)
    note_id = notes[0]["id"]
    await test_db.delete_note(42, note_id)
    notes_after = await test_db.get_notes(42)
    assert len(notes_after) == 0
