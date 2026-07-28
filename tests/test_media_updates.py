"""Tests for playback-aware media stack update scheduling."""

import json
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from src.media_updates import MediaServerClient, MediaUpdateGate


def make_gate(tmp_path, now, **overrides):
    values = {
        "enabled": True,
        "plex_services": {"plex"},
        "jellyfin_services": {"jellyfin"},
        "timezone": ZoneInfo("America/New_York"),
        "scheduled_hour": 0,
        "scheduled_minute": 0,
        "window_minutes": 60,
        "base_backoff_seconds": 300,
        "max_backoff_seconds": 21600,
        "max_deferral_days": 3,
        "state_file": tmp_path / "media-state.json",
        "now": lambda: now[0],
    }
    values.update(overrides)
    return MediaUpdateGate(**values)


def test_new_pending_update_waits_until_midnight(tmp_path):
    now = [datetime(2026, 7, 17, 15, 0, tzinfo=ZoneInfo("America/New_York"))]
    gate = make_gate(tmp_path, now)

    decision = gate.evaluate("external", "plex")

    assert decision.allowed is False
    assert "2026-07-18T00:00:00-04:00" in decision.detail


def test_idle_playback_deploys_during_midnight_window(tmp_path):
    now = [datetime(2026, 7, 18, 0, 5, tzinfo=ZoneInfo("America/New_York"))]
    playback = Mock(return_value=False)
    gate = make_gate(tmp_path, now, plex_check=playback)

    decision = gate.evaluate("external", "plex")

    assert decision.allowed is True
    playback.assert_called_once_with()


def test_active_playback_uses_exponential_backoff(tmp_path):
    timezone = ZoneInfo("America/New_York")
    now = [datetime(2026, 7, 18, 0, 0, tzinfo=timezone)]
    gate = make_gate(tmp_path, now, plex_check=Mock(return_value=True))

    first = gate.evaluate("external", "plex")
    now[0] = datetime(2026, 7, 18, 0, 5, tzinfo=timezone)
    second = gate.evaluate("external", "plex")

    assert first.allowed is False
    assert "00:05:00" in first.detail
    assert second.allowed is False
    assert "00:15:00" in second.detail


def test_update_is_forced_at_midnight_on_third_day(tmp_path):
    timezone = ZoneInfo("America/New_York")
    now = [datetime(2026, 7, 18, 0, 0, tzinfo=timezone)]
    playback = Mock(return_value=True)
    gate = make_gate(tmp_path, now, plex_check=playback)
    gate.evaluate("external", "plex")

    now[0] = datetime(2026, 7, 21, 0, 0, tzinfo=timezone)
    decision = gate.evaluate("external", "plex")

    assert decision.allowed is True
    assert "3-day" in decision.detail
    assert playback.call_count == 1


def test_retry_state_survives_restart(tmp_path):
    timezone = ZoneInfo("America/New_York")
    now = [datetime(2026, 7, 18, 0, 0, tzinfo=timezone)]
    state_file = tmp_path / "media-state.json"
    first_gate = make_gate(tmp_path, now, plex_check=Mock(return_value=True), state_file=state_file)
    first_gate.evaluate("external", "plex")

    restarted_gate = make_gate(tmp_path, now, state_file=state_file)

    assert restarted_gate._states["external/plex"].failures == 1
    assert restarted_gate.pending_stacks == {"external"}
    assert json.loads(state_file.read_text())["stacks"]["external/plex"]["failures"] == 1


def test_playback_api_failure_defers_update(tmp_path):
    now = [datetime(2026, 7, 18, 0, 0, tzinfo=ZoneInfo("America/New_York"))]
    playback = Mock(side_effect=OSError("connection refused"))
    gate = make_gate(tmp_path, now, plex_check=playback)

    decision = gate.evaluate("external", "plex")

    assert decision.allowed is False
    assert "playback API unavailable" in decision.detail
    assert gate.pending_stacks == {"external"}


def test_unrelated_service_in_media_stack_is_not_protected(tmp_path):
    now = [datetime(2026, 7, 18, 0, 0, tzinfo=ZoneInfo("America/New_York"))]
    playback = Mock(return_value=True)
    gate = make_gate(tmp_path, now, plex_check=playback)

    decision = gate.evaluate("external", "wiki")

    assert decision.allowed is True
    playback.assert_not_called()


def test_full_service_selector_matches_only_target_service(tmp_path):
    now = [datetime(2026, 7, 18, 0, 0, tzinfo=ZoneInfo("America/New_York"))]
    gate = make_gate(
        tmp_path,
        now,
        plex_services={"external_plex"},
        plex_check=Mock(return_value=True),
    )

    assert gate.protects("external", "plex") is True
    assert gate.protects("other", "plex") is False


def test_from_env_reads_media_credentials_from_secret_files(tmp_path):
    plex_secret = tmp_path / "plex-token"
    jellyfin_secret = tmp_path / "jellyfin-key"
    plex_secret.write_text("plex-secret\n")
    jellyfin_secret.write_text("jellyfin-secret\n")

    with patch.dict(
        "os.environ",
        {
            "HIVEMIND_MEDIA_UPDATE_ENABLED": "true",
            "HIVEMIND_PLEX_URL": "http://external_plex:32400",
            "HIVEMIND_PLEX_TOKEN_FILE": str(plex_secret),
            "HIVEMIND_JELLYFIN_URL": "http://external_jellyfin:8096",
            "HIVEMIND_JELLYFIN_API_KEY_FILE": str(jellyfin_secret),
            "HIVEMIND_MEDIA_UPDATE_STATE_FILE": str(tmp_path / "state.json"),
        },
        clear=True,
    ):
        gate = MediaUpdateGate.from_env()

    assert gate.enabled is True
    assert gate.plex_check.__self__.token == "plex-secret"
    assert gate.jellyfin_check.__self__.token == "jellyfin-secret"


def test_plex_api_detects_playing_episode():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = (
        b'<MediaContainer><Video type="episode"><Player state="playing" /></Video></MediaContainer>'
    )
    client = MediaServerClient("http://plex:32400", "secret", 10)

    with patch("urllib.request.urlopen", return_value=response):
        assert client.plex_playing() is True


def test_jellyfin_api_ignores_paused_movie():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(
        [{"NowPlayingItem": {"Type": "Movie"}, "PlayState": {"IsPaused": True}}]
    ).encode()
    client = MediaServerClient("http://jellyfin:8096", "secret", 10)

    with patch("urllib.request.urlopen", return_value=response):
        assert client.jellyfin_playing() is False
