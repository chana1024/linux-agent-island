from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import logging
import os
import pwd
import secrets
import shlex
import shutil
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from .core.models import AgentSession, CodexAccountStatus, CodexAccountSummary, CodexUsageInfo


logger = logging.getLogger(__name__)

_DEVICE_LOGIN_TIMEOUT_SECONDS = 900
_DEVICE_LOGIN_POLL_INTERVAL_SECONDS = 0.2
_GUI_ENV_KEYS = (
    "DISPLAY",
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "XDG_CURRENT_DESKTOP",
)
_GUI_ENV_PROCESS_HINTS = ("gnome-shell", "chrome", "google-chrome", "Xwayland", "terminator")


@dataclass(slots=True)
class _StoredCodexAccount:
    account_id: str
    label: str
    created_at: int
    updated_at: int
    auth_fingerprint: str
    is_default: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "label": self.label,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "auth_fingerprint": self.auth_fingerprint,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "_StoredCodexAccount":
        return cls(
            account_id=str(payload.get("account_id", "")),
            label=str(payload.get("label", "")),
            created_at=int(payload.get("created_at", 0)),
            updated_at=int(payload.get("updated_at", 0)),
            auth_fingerprint=str(payload.get("auth_fingerprint", "")),
            is_default=bool(payload.get("is_default", False)),
        )


@dataclass(slots=True)
class _UsageTarget:
    auth_payload: dict[str, object]
    target_account: _StoredCodexAccount | None
    auth_fingerprint: str
    cache_key: str
    cached_usage: CodexUsageInfo | None


class CodexAccountService:
    def __init__(
        self,
        auth_path: Path,
        accounts_dir: Path,
        manifest_path: Path,
        configured_codex_bin: str = "",
        launch_login: Callable[[str], subprocess.Popen[object]] | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        self.auth_path = auth_path
        self.accounts_dir = accounts_dir
        self.manifest_path = manifest_path
        self.configured_codex_bin = configured_codex_bin.strip()
        self.launch_login = launch_login or self._launch_login_terminal
        self.now = now or (lambda: int(time.time()))
        self._lock = threading.Lock()
        self._login_in_progress = False

    def list_accounts(self) -> list[CodexAccountSummary]:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                return self._summaries_from_stored_locked(stored_accounts)

    def get_status(self, sessions: list[AgentSession] | None = None) -> CodexAccountStatus:
        device_login_in_progress = self._shared_login_in_progress()
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                auth_payload = self._read_auth_payload()
                current_fingerprint = self._fingerprint_payload(auth_payload) if auth_payload is not None else None
                active_account = self._find_account_by_fingerprint(stored_accounts, current_fingerprint)
                summaries = self._summaries_from_stored_locked(stored_accounts)
                has_running_codex_sessions = any(
                    session.provider == "codex" and session.is_visible_in_island
                    for session in (sessions or [])
                )
                return CodexAccountStatus(
                    logged_in=self._auth_payload_has_credentials(auth_payload),
                    auth_mode=self._auth_mode(auth_payload),
                    current_account_id=active_account.account_id if active_account is not None else None,
                    current_account_label=(
                        active_account.label
                        if active_account is not None
                        else ("External account" if self._auth_payload_has_credentials(auth_payload) else None)
                    ),
                    current_account_managed=active_account is not None,
                    device_login_in_progress=device_login_in_progress,
                    switch_affects_new_sessions_only=True,
                    has_running_codex_sessions=has_running_codex_sessions,
                    accounts=summaries,
                )

    def rename_account(self, account_id: str, label: str) -> None:
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("account label is required")
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                account = self._require_account(stored_accounts, account_id)
                account.label = normalized_label
                account.updated_at = self.now()
                self._save_accounts_locked(stored_accounts)

    def delete_account(self, account_id: str) -> None:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                auth_payload = self._read_auth_payload()
                current_fingerprint = self._fingerprint_payload(auth_payload) if auth_payload is not None else None
                active_account = self._find_account_by_fingerprint(stored_accounts, current_fingerprint)
                if active_account is not None and active_account.account_id == account_id:
                    raise ValueError("cannot delete the active Codex account")
                remaining = [account for account in stored_accounts if account.account_id != account_id]
                if len(remaining) == len(stored_accounts):
                    raise ValueError(f"unknown Codex account: {account_id}")
                if remaining and not any(account.is_default for account in remaining):
                    remaining[0].is_default = True
                snapshot_path = self._snapshot_path(account_id)
                if snapshot_path.exists():
                    snapshot_path.unlink()
                self._save_accounts_locked(remaining)

    def set_default_account(self, account_id: str) -> None:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                self._require_account(stored_accounts, account_id)
                for account in stored_accounts:
                    account.is_default = account.account_id == account_id
                    if account.is_default:
                        account.updated_at = self.now()
                self._save_accounts_locked(stored_accounts)

    def import_current_auth(self, label: str | None = None) -> CodexAccountSummary:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                imported = self._import_current_auth_locked(stored_accounts, (label or "").strip())
        return CodexAccountSummary(
            account_id=imported.account_id,
            label=imported.label,
            is_default=imported.is_default,
            is_active=True,
            has_credentials=self._snapshot_path(imported.account_id).exists(),
        )

    def switch_account(self, account_selector: str) -> CodexAccountStatus:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                account = self._require_account(stored_accounts, account_selector)
                snapshot_path = self._snapshot_path(account.account_id)
                if not snapshot_path.exists():
                    raise ValueError("stored credentials are missing")
                logger.info(
                    "switching Codex account account_id=%s label=%s snapshot_path=%s auth_path=%s",
                    account.account_id,
                    account.label,
                    snapshot_path,
                    self.auth_path,
                )
                self.auth_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(snapshot_path, self.auth_path)
                self.auth_path.chmod(0o600)
                account.updated_at = self.now()
                self._save_accounts_locked(stored_accounts)
        logger.info("switched Codex account account_selector=%s", account_selector)
        return self.get_status()

    def get_usage_info(self, account_selector: str | None = None) -> CodexUsageInfo:
        usage_target = self._resolve_usage_target(account_selector)
        usage_payload = self._fetch_backend_usage_payload(usage_target.auth_payload)
        usage = self._usage_info_from_payload(
            usage_target.auth_payload,
            usage_payload,
            usage_target.target_account,
            cached_usage=usage_target.cached_usage,
        )
        self._write_usage_cache(usage_target.cache_key, usage_target.auth_fingerprint, usage)
        return usage

    def _resolve_usage_target(self, account_selector: str | None) -> _UsageTarget:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                target_account: _StoredCodexAccount | None = None
                if account_selector:
                    target_account = self._require_account(stored_accounts, account_selector)
                    auth_payload = self._read_payload_from_path(self._snapshot_path(target_account.account_id))
                    auth_fingerprint = target_account.auth_fingerprint
                    cache_key = target_account.account_id
                else:
                    auth_payload = self._read_auth_payload()
                    auth_fingerprint = self._fingerprint_payload(auth_payload) if auth_payload is not None else ""
                    target_account = self._find_account_by_fingerprint(stored_accounts, auth_fingerprint or None)
                    cache_key = (
                        target_account.account_id
                        if target_account is not None
                        else self._usage_cache_key_for_payload(auth_payload, auth_fingerprint)
                    )
                if auth_payload is None or not self._auth_payload_has_credentials(auth_payload):
                    raise ValueError("Codex is not logged in")
                if not auth_fingerprint:
                    auth_fingerprint = self._fingerprint_payload(auth_payload)
                return _UsageTarget(
                    auth_payload=auth_payload,
                    target_account=target_account,
                    auth_fingerprint=auth_fingerprint,
                    cache_key=cache_key,
                    cached_usage=self._read_usage_cache(cache_key, auth_fingerprint),
                )

    def start_device_login(
        self,
        label: str | None = None,
        on_complete: Callable[[bool], None] | None = None,
    ) -> bool:
        login_label = (label or "").strip()
        with self._locked_accounts_io():
            with self._lock:
                if self._shared_login_in_progress():
                    logger.info("Codex device login request ignored because a login is already in progress")
                    return False
                self._login_in_progress = True
        watcher = threading.Thread(
            target=self._run_device_login_background,
            args=(login_label, on_complete),
            daemon=True,
        )
        watcher.start()
        return True

    def run_device_login(self, label: str | None = None) -> bool:
        login_label = (label or "").strip()
        with self._locked_accounts_io():
            with self._lock:
                if self._shared_login_in_progress():
                    raise ValueError("Codex login already in progress")
                self._login_in_progress = True
                self._write_shared_login_state_locked(os.getpid())
                backup_path = self._prepare_auth_for_new_login_locked()
                status_path = self._login_status_path()
        process: subprocess.Popen[object] | None = None
        success = False
        try:
            logger.info(
                "starting Codex device login label=%s auth_path=%s status_path=%s shell_command=%s",
                login_label or "<auto>",
                self.auth_path,
                status_path,
                self._login_shell_command(),
            )
            process = self.launch_login(self._login_shell_command(status_path))
            logger.info(
                "Codex device login terminal launched pid=%s label=%s",
                getattr(process, "pid", None),
                login_label or "<auto>",
            )
            success = self._finalize_login_process(process, login_label, backup_path, status_path)
            return success
        finally:
            with self._locked_accounts_io():
                with self._lock:
                    self._login_in_progress = False
                    if not success:
                        self._restore_login_backup_locked(backup_path)
                    self._cleanup_login_status(status_path)
                    self._clear_shared_login_state_locked()
            logger.info("Codex device login completed success=%s label=%s", success, login_label or "<auto>")

    def _run_device_login_background(
        self,
        label: str,
        on_complete: Callable[[bool], None] | None,
    ) -> None:
        success = False
        try:
            success = self.run_device_login(label)
        except Exception:
            logger.exception("Codex device login watcher failed")
        finally:
            if on_complete is not None:
                on_complete(success)

    def _finalize_login_process(
        self,
        process: subprocess.Popen[object],
        label: str,
        backup_path: Path | None,
        status_path: Path,
    ) -> bool:
        return_code = self._wait_for_login_result(process, status_path)
        auth_payload = self._read_auth_payload()
        has_credentials = self._auth_payload_has_credentials(auth_payload)
        logger.info(
            "Codex device login process finished pid=%s return_code=%s has_credentials=%s auth_summary=%s",
            getattr(process, "pid", None),
            return_code,
            has_credentials,
            self._auth_payload_debug_summary(auth_payload),
        )
        if return_code != 0 or not has_credentials:
            logger.warning(
                "Codex device login did not produce importable credentials pid=%s return_code=%s auth_path_exists=%s",
                getattr(process, "pid", None),
                return_code,
                self.auth_path.exists(),
            )
            return False
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                imported = self._import_current_auth_locked(
                    stored_accounts,
                    label or self._next_default_label(stored_accounts),
                )
                self._cleanup_login_backup_locked(backup_path)
        logger.info(
            "Codex device login imported account account_id=%s label=%s",
            imported.account_id,
            imported.label,
        )
        return True

    def _summaries_from_stored_locked(self, stored_accounts: list[_StoredCodexAccount]) -> list[CodexAccountSummary]:
        auth_payload = self._read_auth_payload()
        current_fingerprint = self._fingerprint_payload(auth_payload) if auth_payload is not None else None
        summaries: list[CodexAccountSummary] = []
        for account in stored_accounts:
            summaries.append(
                CodexAccountSummary(
                    account_id=account.account_id,
                    label=account.label,
                    is_default=account.is_default,
                    is_active=account.auth_fingerprint == current_fingerprint,
                    has_credentials=self._snapshot_path(account.account_id).exists(),
                )
            )
        return summaries

    def _load_accounts_locked(self) -> list[_StoredCodexAccount]:
        if not self.manifest_path.exists():
            stored_accounts: list[_StoredCodexAccount] = []
        else:
            try:
                payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            raw_accounts = payload.get("accounts", []) if isinstance(payload, dict) else []
            stored_accounts = [
                _StoredCodexAccount.from_dict(item)
                for item in raw_accounts
                if isinstance(item, dict)
            ]
        stored_accounts = [account for account in stored_accounts if account.account_id]
        stored_accounts.sort(key=lambda account: (not account.is_default, account.created_at, account.account_id))
        auth_payload = self._read_auth_payload()
        if auth_payload is not None and self._auth_payload_has_credentials(auth_payload) and not stored_accounts:
            self._import_current_auth_locked(stored_accounts, "")
        return stored_accounts

    def _save_accounts_locked(self, stored_accounts: list[_StoredCodexAccount]) -> None:
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        payload = {"accounts": [account.to_dict() for account in stored_accounts]}
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _import_current_auth_locked(self, stored_accounts: list[_StoredCodexAccount], label: str) -> _StoredCodexAccount:
        auth_payload = self._read_auth_payload()
        if auth_payload is None or not self._auth_payload_has_credentials(auth_payload):
            raise ValueError("Codex is not logged in")
        fingerprint = self._fingerprint_payload(auth_payload)
        resolved_label = self._preferred_account_label(auth_payload, stored_accounts, label)
        existing = self._find_account_by_fingerprint(stored_accounts, fingerprint)
        now_ts = self.now()
        if existing is not None:
            if label:
                existing.label = resolved_label
            existing.updated_at = now_ts
            snapshot_path = self._snapshot_path(existing.account_id)
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(json.dumps(auth_payload, indent=2, sort_keys=True), encoding="utf-8")
            snapshot_path.chmod(0o600)
            self._save_accounts_locked(stored_accounts)
            return existing
        account = _StoredCodexAccount(
            account_id=secrets.token_hex(8),
            label=resolved_label,
            created_at=now_ts,
            updated_at=now_ts,
            auth_fingerprint=fingerprint,
            is_default=not stored_accounts,
        )
        stored_accounts.append(account)
        snapshot_path = self._snapshot_path(account.account_id)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(auth_payload, indent=2, sort_keys=True), encoding="utf-8")
        snapshot_path.chmod(0o600)
        self._save_accounts_locked(stored_accounts)
        return account

    def _require_account(
        self,
        stored_accounts: list[_StoredCodexAccount],
        account_selector: str,
    ) -> _StoredCodexAccount:
        normalized_selector = account_selector.strip()
        if not normalized_selector:
            raise ValueError("Codex account selector is required")

        for account in stored_accounts:
            if account.account_id == normalized_selector:
                return account

        lowered_selector = normalized_selector.casefold()
        matches: list[_StoredCodexAccount] = []
        for account in stored_accounts:
            if account.label.casefold() == lowered_selector:
                matches.append(account)
                continue
            account_email = self._account_email_from_snapshot(account)
            if account_email is not None and account_email.casefold() == lowered_selector:
                matches.append(account)

        unique_matches: dict[str, _StoredCodexAccount] = {account.account_id: account for account in matches}
        if len(unique_matches) == 1:
            return next(iter(unique_matches.values()))
        if len(unique_matches) > 1:
            raise ValueError(f"multiple Codex accounts match: {account_selector}")
        raise ValueError(f"unknown Codex account: {account_selector}")

    def _find_account_by_fingerprint(
        self,
        stored_accounts: list[_StoredCodexAccount],
        fingerprint: str | None,
    ) -> _StoredCodexAccount | None:
        if fingerprint is None:
            return None
        for account in stored_accounts:
            if account.auth_fingerprint == fingerprint:
                return account
        return None

    def _snapshot_path(self, account_id: str) -> Path:
        return self.accounts_dir / f"{account_id}.json"

    def _usage_cache_dir(self) -> Path:
        return self.accounts_dir / "usage-cache"

    def _usage_cache_path(self, cache_key: str) -> Path:
        return self._usage_cache_dir() / f"{cache_key}.json"

    def _usage_cache_key_for_payload(self, payload: dict[str, object], auth_fingerprint: str) -> str:
        account_id = self._token_account_id(payload)
        if account_id:
            return f"current-{account_id}"
        return f"current-{auth_fingerprint}"

    def _read_usage_cache(self, cache_key: str, auth_fingerprint: str) -> CodexUsageInfo | None:
        cache_path = self._usage_cache_path(cache_key)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("auth_fingerprint") != auth_fingerprint:
            return None
        usage_payload = payload.get("usage")
        if not isinstance(usage_payload, dict):
            return None
        try:
            return CodexUsageInfo.from_dict(usage_payload)
        except (TypeError, ValueError):
            return None

    def _write_usage_cache(self, cache_key: str, auth_fingerprint: str, usage: CodexUsageInfo) -> None:
        with self._locked_accounts_io():
            with self._lock:
                cache_path = self._usage_cache_path(cache_key)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(
                        {
                            "auth_fingerprint": auth_fingerprint,
                            "saved_at": self.now(),
                            "usage": usage.to_dict(),
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                cache_path.chmod(0o600)

    def _account_email_from_snapshot(self, account: _StoredCodexAccount) -> str | None:
        payload = self._read_payload_from_path(self._snapshot_path(account.account_id))
        if payload is None:
            return None
        return self._email_from_auth_payload(payload)

    def _lock_path(self) -> Path:
        return self.accounts_dir / ".accounts.lock"

    @contextmanager
    def _locked_accounts_io(self) -> Iterator[None]:
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        lock_path.touch(exist_ok=True)
        with lock_path.open("r+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _shared_login_state_path(self) -> Path:
        return self.accounts_dir / ".login-active.json"

    def _login_backup_path(self) -> Path:
        return self.accounts_dir / f".login-backup-{secrets.token_hex(8)}.json"

    def _login_status_path(self) -> Path:
        return self.accounts_dir / f".login-status-{secrets.token_hex(8)}.txt"

    def _prepare_auth_for_new_login_locked(self) -> Path | None:
        auth_payload = self._read_auth_payload()
        if auth_payload is not None and self._auth_payload_has_credentials(auth_payload):
            stored_accounts = self._load_accounts_locked()
            imported = self._import_current_auth_locked(stored_accounts, "")
            backup_path = self._login_backup_path()
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.auth_path), str(backup_path))
            logger.info(
                "prepared existing Codex auth for new login account_id=%s label=%s backup_path=%s",
                imported.account_id,
                imported.label,
                backup_path,
            )
            return backup_path

        if self.auth_path.exists():
            backup_path = self._login_backup_path()
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.auth_path), str(backup_path))
            logger.info("moved existing non-credential Codex auth aside backup_path=%s", backup_path)
            return backup_path
        return None

    def _restore_login_backup_locked(self, backup_path: Path | None) -> None:
        if backup_path is None or not backup_path.exists():
            return
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(backup_path), str(self.auth_path))
        self.auth_path.chmod(0o600)
        logger.info("restored previous Codex auth after login failure backup_path=%s", backup_path)

    def _cleanup_login_backup_locked(self, backup_path: Path | None) -> None:
        if backup_path is None or not backup_path.exists():
            return
        backup_path.unlink()
        logger.info("removed temporary Codex auth backup backup_path=%s", backup_path)

    def _cleanup_login_status(self, status_path: Path) -> None:
        if not status_path.exists():
            return
        status_path.unlink()
        logger.info("removed temporary Codex login status file status_path=%s", status_path)

    def _write_shared_login_state_locked(self, pid: int) -> None:
        state_path = self._shared_login_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"pid": pid, "started_at": self.now()}), encoding="utf-8")

    def _clear_shared_login_state_locked(self) -> None:
        state_path = self._shared_login_state_path()
        if state_path.exists():
            state_path.unlink()

    def _shared_login_in_progress(self) -> bool:
        state_path = self._shared_login_state_path()
        if not state_path.exists():
            return False
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state_path.unlink(missing_ok=True)
            return False
        if not isinstance(payload, dict):
            state_path.unlink(missing_ok=True)
            return False
        pid = payload.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            state_path.unlink(missing_ok=True)
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            state_path.unlink(missing_ok=True)
            return False
        except PermissionError:
            return True
        return True

    def _read_auth_payload(self) -> dict[str, object] | None:
        return self._read_payload_from_path(self.auth_path)

    def _read_payload_from_path(self, path: Path) -> dict[str, object] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _fingerprint_payload(self, payload: dict[str, object]) -> str:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _auth_payload_has_credentials(self, payload: dict[str, object] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        api_key = payload.get("OPENAI_API_KEY")
        if isinstance(api_key, str) and api_key.strip():
            return True
        tokens = payload.get("tokens")
        if isinstance(tokens, dict):
            return any(
                isinstance(tokens.get(key), str) and str(tokens.get(key)).strip()
                for key in ("refresh_token", "access_token", "id_token")
            )
        return False

    def _auth_mode(self, payload: dict[str, object] | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        mode = payload.get("auth_mode")
        return str(mode) if mode is not None else None

    def _next_default_label(self, stored_accounts: list[_StoredCodexAccount]) -> str:
        return f"Codex account {len(stored_accounts) + 1}"

    def _preferred_account_label(
        self,
        auth_payload: dict[str, object],
        stored_accounts: list[_StoredCodexAccount],
        requested_label: str,
    ) -> str:
        email = self._email_from_auth_payload(auth_payload)
        if email:
            return email
        normalized_label = requested_label.strip()
        if normalized_label:
            return normalized_label
        if not stored_accounts:
            return "Current account"
        return self._next_default_label(stored_accounts)

    def _email_from_auth_payload(self, payload: dict[str, object]) -> str | None:
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return None
        for token_key in ("id_token", "access_token"):
            token_value = tokens.get(token_key)
            email = self._email_from_jwt(token_value)
            if email:
                return email
        return None

    def _usage_info_from_payload(
        self,
        payload: dict[str, object],
        usage_payload: dict[str, object],
        account: _StoredCodexAccount | None,
        *,
        cached_usage: CodexUsageInfo | None = None,
    ) -> CodexUsageInfo:
        auth_claims = self._openai_auth_claims(payload)
        until = self._string_claim(auth_claims, "chatgpt_subscription_active_until") or (
            None if cached_usage is None else cached_usage.subscription_active_until
        )
        remaining_days, remaining_hours = self._remaining_time(until)
        five_hour_window = self._rate_limit_window(usage_payload, "rate_limit", "primary_window")
        weekly_window = self._rate_limit_window(usage_payload, "rate_limit", "secondary_window")
        credits = usage_payload.get("credits") if isinstance(usage_payload.get("credits"), dict) else {}
        backend_plan_type = self._string_claim(usage_payload, "plan_type")
        has_credits = self._bool_value(credits, "has_credits")
        if has_credits is None and cached_usage is not None:
            has_credits = cached_usage.has_credits
        credits_unlimited = self._bool_value(credits, "unlimited")
        if credits_unlimited is None and cached_usage is not None:
            credits_unlimited = cached_usage.credits_unlimited
        credits_balance = self._string_claim(credits, "balance")
        if credits_balance is None and cached_usage is not None:
            credits_balance = cached_usage.credits_balance
        return CodexUsageInfo(
            account_id=account.account_id if account is not None else self._token_account_id(payload),
            label=account.label if account is not None else None,
            email=(
                self._string_claim(usage_payload, "email")
                or self._email_from_auth_payload(payload)
                or (None if cached_usage is None else cached_usage.email)
            ),
            auth_mode=self._auth_mode(payload),
            plan_type=backend_plan_type
            or self._string_claim(auth_claims, "chatgpt_plan_type")
            or (None if cached_usage is None else cached_usage.plan_type),
            subscription_active_start=self._string_claim(auth_claims, "chatgpt_subscription_active_start")
            or (None if cached_usage is None else cached_usage.subscription_active_start),
            subscription_active_until=until,
            subscription_last_checked=self._string_claim(auth_claims, "chatgpt_subscription_last_checked")
            or (None if cached_usage is None else cached_usage.subscription_last_checked),
            remaining_days=remaining_days,
            remaining_hours=remaining_hours,
            five_hour_used_percent=self._number_value(five_hour_window, "used_percent"),
            five_hour_window_minutes=self._window_minutes(five_hour_window),
            five_hour_resets_at=self._int_value(five_hour_window, "reset_at"),
            weekly_used_percent=self._number_value(weekly_window, "used_percent"),
            weekly_window_minutes=self._window_minutes(weekly_window),
            weekly_resets_at=self._int_value(weekly_window, "reset_at"),
            has_credits=has_credits,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
        )

    def _fetch_backend_usage_payload(self, payload: dict[str, object]) -> dict[str, object]:
        access_token = self._token_value(payload, "access_token")
        if access_token is None:
            raise ValueError("Codex access token is missing")
        request = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage")
        request.add_header("Authorization", f"Bearer {access_token}")
        request.add_header("User-Agent", "codex-cli")
        account_id = self._token_account_id(payload)
        if account_id is not None:
            request.add_header("ChatGPT-Account-Id", account_id)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"failed to fetch Codex rate limits: {exc}") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"failed to decode Codex rate limits: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ValueError("failed to fetch Codex rate limits: unexpected payload")
        return decoded

    def _openai_auth_claims(self, payload: dict[str, object]) -> dict[str, object]:
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return {}
        for token_key in ("id_token", "access_token"):
            claims = self._jwt_payload(tokens.get(token_key))
            if not claims:
                continue
            auth_claims = claims.get("https://api.openai.com/auth")
            if isinstance(auth_claims, dict):
                return auth_claims
        return {}

    def _token_account_id(self, payload: dict[str, object]) -> str | None:
        token_account_id = self._token_value(payload, "account_id")
        if token_account_id is None:
            return None
        return token_account_id

    def _token_value(self, payload: dict[str, object], key: str) -> str | None:
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return None
        value = tokens.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _string_claim(self, payload: dict[str, object], key: str) -> str | None:
        value = payload.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _rate_limit_window(
        self,
        payload: dict[str, object],
        container_key: str,
        window_key: str,
    ) -> dict[str, object]:
        container = payload.get(container_key)
        if not isinstance(container, dict):
            return {}
        window = container.get(window_key)
        return window if isinstance(window, dict) else {}

    def _number_value(self, payload: dict[str, object], key: str) -> float | None:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _int_value(self, payload: dict[str, object], key: str) -> int | None:
        value = payload.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    def _bool_value(self, payload: dict[str, object], key: str) -> bool | None:
        value = payload.get(key)
        return value if isinstance(value, bool) else None

    def _window_minutes(self, payload: dict[str, object]) -> int | None:
        seconds = self._int_value(payload, "limit_window_seconds")
        if seconds is None or seconds <= 0:
            return None
        return (seconds + 59) // 60

    def _remaining_time(self, until: str | None) -> tuple[float | None, float | None]:
        if until is None:
            return None, None
        try:
            expiry = datetime.fromisoformat(until)
        except ValueError:
            return None, None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        remaining_hours = round((expiry - datetime.now(timezone.utc)).total_seconds() / 3600, 2)
        remaining_days = round(remaining_hours / 24, 2)
        return remaining_days, remaining_hours

    def _jwt_payload(self, token: object) -> dict[str, object] | None:
        if not isinstance(token, str) or not token.strip():
            return None
        segments = token.split(".")
        if len(segments) < 2:
            return None
        encoded_payload = segments[1]
        padding = "=" * (-len(encoded_payload) % 4)
        try:
            decoded_payload = base64.urlsafe_b64decode(encoded_payload + padding)
            payload = json.loads(decoded_payload.decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _email_from_jwt(self, token: object) -> str | None:
        payload = self._jwt_payload(token)
        if not isinstance(payload, dict):
            return None

        email = payload.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()

        profile = payload.get("https://api.openai.com/profile")
        if isinstance(profile, dict):
            profile_email = profile.get("email")
            if isinstance(profile_email, str) and profile_email.strip():
                return profile_email.strip()
        return None

    def _wait_for_login_result(self, process: subprocess.Popen[object], status_path: Path) -> int | None:
        wait_method = getattr(process, "wait", None)
        poll_method = getattr(process, "poll", None)
        if not callable(poll_method) and callable(wait_method):
            return int(wait_method())

        deadline = time.monotonic() + _DEVICE_LOGIN_TIMEOUT_SECONDS
        launcher_exit_logged = False
        while time.monotonic() < deadline:
            status = self._read_login_status(status_path)
            if status is not None:
                return status

            if callable(poll_method):
                launcher_return_code = poll_method()
                if launcher_return_code is not None and not launcher_exit_logged:
                    logger.info(
                        "Codex login launcher exited before status file pid=%s return_code=%s status_path=%s",
                        getattr(process, "pid", None),
                        launcher_return_code,
                        status_path,
                    )
                    launcher_exit_logged = True
            time.sleep(_DEVICE_LOGIN_POLL_INTERVAL_SECONDS)

        logger.warning("Codex login status file timed out status_path=%s", status_path)
        return None

    def _read_login_status(self, status_path: Path) -> int | None:
        if not status_path.exists():
            return None
        try:
            raw = status_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            logger.warning("invalid Codex login status file contents status_path=%s raw=%s", status_path, raw)
            return None

    def _login_shell_command(self, status_path: Path | None = None) -> str:
        resolved_codex_executable = self._resolve_codex_executable()
        codex_executable = shlex.quote(resolved_codex_executable)
        codex_bin_dir = shlex.quote(str(Path(resolved_codex_executable).expanduser().parent))
        base_command = f"{codex_executable} login --device-auth"
        prefixed_command = f"export PATH={codex_bin_dir}:$PATH; {base_command}"
        if status_path is None:
            return prefixed_command
        return (
            f"{prefixed_command}; login_rc=$?; "
            f"mkdir -p {shlex.quote(str(status_path.parent))}; "
            f"printf '%s\\n' \"$login_rc\" > {shlex.quote(str(status_path))}"
        )

    def _resolve_codex_executable(self) -> str:
        if self.configured_codex_bin:
            candidate = Path(self.configured_codex_bin).expanduser()
            if self._is_executable_file(candidate):
                return str(candidate)
            raise RuntimeError(f"configured Codex executable is not executable: {candidate}")

        path_candidate = shutil.which("codex")
        if path_candidate:
            return path_candidate

        raise RuntimeError(
            "Codex CLI not found in system PATH. Configure codex_bin_path in island settings or make `codex` available in PATH."
        )

    def _find_codex_in_nvm_tree(self) -> Path | None:
        nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm")).expanduser()
        versions_dir = nvm_dir / "versions" / "node"
        if not versions_dir.exists():
            return None
        candidates = sorted(
            (path for path in versions_dir.glob("*/bin/codex") if self._is_executable_file(path)),
            key=self._nvm_codex_version_sort_key,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _nvm_codex_version_sort_key(self, path: Path) -> tuple[int, ...]:
        version_name = path.parent.parent.name
        if version_name.startswith("v"):
            version_name = version_name[1:]
        parts: list[int] = []
        for raw_part in version_name.split("."):
            try:
                parts.append(int(raw_part))
            except ValueError:
                parts.append(-1)
        return tuple(parts)

    def _resolve_codex_via_nvm_shell(self) -> str | None:
        nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm")).expanduser()
        nvm_script = nvm_dir / "nvm.sh"
        if not nvm_script.exists():
            return None
        result = subprocess.run(
            [
                "bash",
                "-lc",
                (
                    f"export NVM_DIR={shlex.quote(str(nvm_dir))}; "
                    f". {shlex.quote(str(nvm_script))}; "
                    "command -v codex"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        candidate = result.stdout.strip()
        if result.returncode == 0 and candidate:
            return candidate
        return None

    def _is_executable_file(self, path: Path) -> bool:
        return path.is_file() and os.access(path, os.X_OK)

    def _launch_login_terminal(self, shell_command: str) -> subprocess.Popen[object]:
        command = self._terminal_launch_command(shell_command)
        env = os.environ.copy()
        env.update(self._gui_environment())
        logger.info("launching Codex login terminal command=%s", shlex.join(command))
        return subprocess.Popen(command, env=env)

    def _terminal_launch_command(self, shell_command: str) -> list[str]:
        hold_command = (
            f"{shell_command}; login_rc=$?; "
            "printf '\\nPress Enter to close...'; "
            "read _; "
            "exit $login_rc"
        )
        shell_argv = self._terminal_shell_argv(hold_command)
        terminals: list[tuple[str, list[str]]] = [
            ("terminator", ["terminator", "--no-dbus", "-x", *shell_argv]),
            ("gnome-terminal", ["gnome-terminal", "--", *shell_argv]),
            ("kitty", ["kitty", *shell_argv]),
            ("wezterm", ["wezterm", "start", "--", *shell_argv]),
            ("alacritty", ["alacritty", "-e", *shell_argv]),
            ("konsole", ["konsole", "-e", *shell_argv]),
            ("xfce4-terminal", ["xfce4-terminal", "--command", shlex.join(shell_argv)]),
            ("x-terminal-emulator", ["x-terminal-emulator", "-e", *shell_argv]),
            ("xterm", ["xterm", "-e", *shell_argv]),
        ]
        for executable, command in terminals:
            if shutil.which(executable):
                logger.info(
                    "selected terminal emulator for Codex login executable=%s shell_argv=%s",
                    executable,
                    shlex.join(shell_argv),
                )
                return command
        raise RuntimeError("no supported terminal emulator was found for Codex login")

    def _terminal_shell_argv(self, command: str) -> list[str]:
        shell_path = self._preferred_login_shell()
        if shell_path is not None:
            return [shell_path, "-l", "-i", "-c", command]
        return ["sh", "-lc", command]

    def _preferred_login_shell(self) -> str | None:
        candidates = [os.environ.get("SHELL")]
        try:
            candidates.append(pwd.getpwuid(os.getuid()).pw_shell)
        except KeyError:
            pass

        for candidate in candidates:
            shell_path = self._resolve_shell_path(candidate)
            if shell_path is not None:
                return shell_path
        return None

    def _resolve_shell_path(self, candidate: str | None) -> str | None:
        if not candidate:
            return None

        shell_path: str | None
        candidate_path = Path(candidate)
        if candidate_path.is_absolute():
            shell_path = str(candidate_path) if candidate_path.exists() else None
        else:
            shell_path = shutil.which(candidate)
            if shell_path is None:
                shell_path = shutil.which(candidate_path.name)
        if shell_path is None:
            return None

        shell_name = Path(shell_path).name
        if shell_name in {"false", "nologin"}:
            return None
        return shell_path

    def _gui_environment(self) -> dict[str, str]:
        gui_env = {
            key: value
            for key, value in os.environ.items()
            if key in _GUI_ENV_KEYS and value
        }
        if gui_env.get("DISPLAY") and gui_env.get("XDG_RUNTIME_DIR"):
            return gui_env

        current_uid = os.getuid()
        for proc_entry in sorted(Path("/proc").iterdir(), key=lambda path: path.name):
            if not proc_entry.name.isdigit():
                continue
            try:
                if proc_entry.stat().st_uid != current_uid:
                    continue
                cmdline = (proc_entry / "cmdline").read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
            except OSError:
                continue
            if not any(hint in cmdline for hint in _GUI_ENV_PROCESS_HINTS):
                continue
            try:
                raw_entries = (proc_entry / "environ").read_bytes().split(b"\0")
            except OSError:
                continue
            candidate: dict[str, str] = {}
            for entry in raw_entries:
                if not entry or b"=" not in entry:
                    continue
                key_raw, value_raw = entry.split(b"=", 1)
                key = key_raw.decode(errors="ignore")
                if key in _GUI_ENV_KEYS and value_raw:
                    candidate[key] = value_raw.decode(errors="ignore")
            if candidate.get("DISPLAY"):
                gui_env.update(candidate)
                return gui_env
        return gui_env

    def _auth_payload_debug_summary(self, payload: dict[str, object] | None) -> str:
        if not isinstance(payload, dict):
            return "missing"
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        return (
            f"auth_mode={self._auth_mode(payload) or 'none'} "
            f"api_key={bool(str(payload.get('OPENAI_API_KEY', '')).strip())} "
            f"refresh_token={bool(str(tokens.get('refresh_token', '')).strip())} "
            f"access_token={bool(str(tokens.get('access_token', '')).strip())} "
            f"id_token={bool(str(tokens.get('id_token', '')).strip())}"
        )
