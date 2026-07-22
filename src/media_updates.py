"""Playback-aware scheduling for Plex and Jellyfin stack updates."""

import json
import logging
import os
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("hivemind.media_updates")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_names(name: str, default: str) -> Set[str]:
    value = os.environ.get(name, default)
    return {item.strip().casefold() for item in value.split(",") if item.strip()}


@dataclass(frozen=True)
class GateDecision:
    """Whether an existing stack update may be deployed now."""

    allowed: bool
    detail: str


@dataclass
class DeferredUpdate:
    """Persistent retry state for one protected stack."""

    first_scheduled_at: datetime
    next_attempt_at: datetime
    failures: int = 0


class MediaServerClient:
    """Small HTTP client for media-server session APIs."""

    def __init__(self, url: str, token: str, timeout: float):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def plex_playing(self) -> bool:
        request = urllib.request.Request(
            f"{self.url}/status/sessions",
            headers={"Accept": "application/xml", "X-Plex-Token": self.token},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            root = ET.fromstring(response.read())

        for video in root.findall(".//Video"):
            if video.get("type", "").casefold() not in {"movie", "episode"}:
                continue
            player = video.find("Player")
            if player is None or player.get("state", "playing").casefold() == "playing":
                return True
        return False

    def jellyfin_playing(self) -> bool:
        request = urllib.request.Request(
            f"{self.url}/Sessions",
            headers={"Accept": "application/json", "X-Emby-Token": self.token},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            sessions = json.loads(response.read().decode("utf-8"))

        for session in sessions:
            item = session.get("NowPlayingItem") or {}
            play_state = session.get("PlayState") or {}
            if item.get("Type", "").casefold() in {"movie", "episode"} and not play_state.get(
                "IsPaused", False
            ):
                return True
        return False


class MediaUpdateGate:
    """Schedule protected stack updates and defer them around active playback."""

    def __init__(
        self,
        *,
        enabled: bool,
        plex_stacks: Set[str],
        jellyfin_stacks: Set[str],
        timezone: ZoneInfo,
        scheduled_hour: int,
        scheduled_minute: int,
        window_minutes: int,
        base_backoff_seconds: int,
        max_backoff_seconds: int,
        max_deferral_days: int,
        state_file: Path,
        plex_check: Optional[Callable[[], bool]] = None,
        jellyfin_check: Optional[Callable[[], bool]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self.enabled = enabled
        self.plex_stacks = plex_stacks
        self.jellyfin_stacks = jellyfin_stacks
        self.timezone = timezone
        self.scheduled_hour = scheduled_hour
        self.scheduled_minute = scheduled_minute
        self.window_minutes = window_minutes
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.max_deferral_days = max_deferral_days
        self.state_file = state_file
        self.plex_check = plex_check
        self.jellyfin_check = jellyfin_check
        self._now = now or (lambda: datetime.now(self.timezone))
        self._states: Dict[str, DeferredUpdate] = {}
        self._load_state()

    @classmethod
    def from_env(cls) -> "MediaUpdateGate":
        timezone_name = os.environ.get("HIVEMIND_MEDIA_UPDATE_TIMEZONE", "UTC")
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Invalid HIVEMIND_MEDIA_UPDATE_TIMEZONE: {timezone_name}"
            ) from exc

        hour = int(os.environ.get("HIVEMIND_MEDIA_UPDATE_HOUR", "0"))
        minute = int(os.environ.get("HIVEMIND_MEDIA_UPDATE_MINUTE", "0"))
        window = int(os.environ.get("HIVEMIND_MEDIA_UPDATE_WINDOW_MINUTES", "60"))
        base_backoff = int(os.environ.get("HIVEMIND_MEDIA_UPDATE_BACKOFF_SECONDS", "300"))
        max_backoff = int(os.environ.get("HIVEMIND_MEDIA_UPDATE_MAX_BACKOFF_SECONDS", "21600"))
        max_days = int(os.environ.get("HIVEMIND_MEDIA_UPDATE_MAX_DEFERRAL_DAYS", "3"))
        timeout = float(os.environ.get("HIVEMIND_MEDIA_API_TIMEOUT_SECONDS", "10"))

        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Media update hour/minute must describe a valid time")
        if min(window, base_backoff, max_backoff, max_days) <= 0 or timeout <= 0:
            raise ValueError("Media update window, backoffs, deferral, and timeout must be positive")
        if max_backoff < base_backoff:
            raise ValueError("Media update maximum backoff cannot be less than the base backoff")

        plex_url = os.environ.get("HIVEMIND_PLEX_URL", "").strip()
        plex_token = os.environ.get("HIVEMIND_PLEX_TOKEN", "").strip()
        jellyfin_url = os.environ.get("HIVEMIND_JELLYFIN_URL", "").strip()
        jellyfin_token = os.environ.get("HIVEMIND_JELLYFIN_API_KEY", "").strip()
        plex_client = (
            MediaServerClient(plex_url, plex_token, timeout)
            if plex_url and plex_token
            else None
        )
        jellyfin_client = (
            MediaServerClient(jellyfin_url, jellyfin_token, timeout)
            if jellyfin_url and jellyfin_token
            else None
        )

        if bool(plex_url) != bool(plex_token):
            logger.warning("Plex playback checks disabled: both URL and token are required")
        if bool(jellyfin_url) != bool(jellyfin_token):
            logger.warning("Jellyfin playback checks disabled: both URL and API key are required")

        return cls(
            enabled=_env_bool("HIVEMIND_MEDIA_UPDATE_ENABLED"),
            plex_stacks=_env_names("HIVEMIND_PLEX_STACKS", "plex"),
            jellyfin_stacks=_env_names("HIVEMIND_JELLYFIN_STACKS", "jellyfin"),
            timezone=timezone,
            scheduled_hour=hour,
            scheduled_minute=minute,
            window_minutes=window,
            base_backoff_seconds=base_backoff,
            max_backoff_seconds=max_backoff,
            max_deferral_days=max_days,
            state_file=Path(
                os.environ.get(
                    "HIVEMIND_MEDIA_UPDATE_STATE_FILE",
                    "/var/lib/hivemind/media-update-state.json",
                )
            ),
            plex_check=plex_client.plex_playing if plex_client else None,
            jellyfin_check=jellyfin_client.jellyfin_playing if jellyfin_client else None,
        )

    def protects(self, stack_name: str) -> bool:
        name = stack_name.casefold()
        return self.enabled and name in (self.plex_stacks | self.jellyfin_stacks)

    @property
    def pending_stacks(self) -> Set[str]:
        """Return persisted deferred stacks that should be retried after restart."""
        if not self.enabled:
            return set()
        return set(self._states)

    def evaluate(self, stack_name: str) -> GateDecision:
        """Return a deployment decision for a changed, already-deployed stack."""
        if not self.protects(stack_name):
            return GateDecision(True, "stack is not playback-protected")

        now = self._now().astimezone(self.timezone)
        state_key = stack_name.casefold()
        state = self._states.get(state_key)
        if state is None:
            first_schedule = self._next_schedule(now)
            state = DeferredUpdate(first_schedule, first_schedule)
            self._states[state_key] = state
            self._save_state()

        deadline = self._scheduled_on(
            state.first_scheduled_at.date() + timedelta(days=self.max_deferral_days)
        )
        if now >= deadline:
            return GateDecision(
                True,
                f"maximum {self.max_deferral_days}-day playback deferral reached",
            )

        if now < state.next_attempt_at:
            return GateDecision(False, f"next playback check at {state.next_attempt_at.isoformat()}")

        checks = self._checks_for(stack_name)
        if not checks:
            return GateDecision(True, "scheduled time reached; no playback API configured")

        try:
            playing = any(check() for check in checks)
        except Exception as exc:
            logger.warning("Playback check failed for %s: %s", stack_name, exc)
            return self._defer_after_attempt(
                state, now, deadline, "playback API unavailable"
            )

        if not playing:
            return GateDecision(True, "scheduled playback check reports idle")
        return self._defer_after_attempt(
            state, now, deadline, "movie or episode is playing"
        )

    def clear(self, stack_name: str) -> None:
        """Forget retry state after an update is applied or no longer needed."""
        if self._states.pop(stack_name.casefold(), None) is not None:
            self._save_state()

    def retain(self, stack_names: Set[str]) -> None:
        """Discard retry state for stacks removed from configuration."""
        configured = {name.casefold() for name in stack_names}
        stale = [name for name in self._states if name not in configured]
        if stale:
            for name in stale:
                del self._states[name]
            self._save_state()

    def _checks_for(self, stack_name: str) -> List[Callable[[], bool]]:
        name = stack_name.casefold()
        checks = []
        if name in self.plex_stacks and self.plex_check:
            checks.append(self.plex_check)
        if name in self.jellyfin_stacks and self.jellyfin_check:
            checks.append(self.jellyfin_check)
        return checks

    def _defer_after_attempt(
        self,
        state: DeferredUpdate,
        now: datetime,
        deadline: datetime,
        reason: str,
    ) -> GateDecision:
        delay = min(
            self.base_backoff_seconds * (2 ** min(state.failures, 30)),
            self.max_backoff_seconds,
        )
        state.failures += 1
        state.next_attempt_at = min(now + timedelta(seconds=delay), deadline)
        self._save_state()
        return GateDecision(
            False,
            f"{reason}; retry {state.failures} at {state.next_attempt_at.isoformat()}",
        )

    def _scheduled_on(self, value: date) -> datetime:
        return datetime.combine(
            value,
            time(self.scheduled_hour, self.scheduled_minute),
            tzinfo=self.timezone,
        )

    def _next_schedule(self, now: datetime) -> datetime:
        today = self._scheduled_on(now.date())
        if now < today:
            return today
        if now < today + timedelta(minutes=self.window_minutes):
            return today
        return self._scheduled_on(now.date() + timedelta(days=1))

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text())
            for name, state in payload.get("stacks", {}).items():
                self._states[name.casefold()] = DeferredUpdate(
                    first_scheduled_at=datetime.fromisoformat(state["first_scheduled_at"]),
                    next_attempt_at=datetime.fromisoformat(state["next_attempt_at"]),
                    failures=int(state.get("failures", 0)),
                )
        except Exception as exc:
            logger.warning("Ignoring invalid media update state %s: %s", self.state_file, exc)
            self._states = {}

    def _save_state(self) -> None:
        payload = {
            "version": 1,
            "stacks": {
                name: {
                    "first_scheduled_at": state.first_scheduled_at.isoformat(),
                    "next_attempt_at": state.next_attempt_at.isoformat(),
                    "failures": state.failures,
                }
                for name, state in self._states.items()
            },
        }
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=self.state_file.parent, delete=False
            ) as temporary:
                json.dump(payload, temporary)
                temporary.flush()
                temporary_path = Path(temporary.name)
            temporary_path.replace(self.state_file)
        except Exception as exc:
            logger.error("Could not persist media update state to %s: %s", self.state_file, exc)
