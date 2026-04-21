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
_OPENCLAW_PROFILE_ID = "openai-codex:default"
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
    identity_key: str = ""
    is_default: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "label": self.label,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "auth_fingerprint": self.auth_fingerprint,
            "identity_key": self.identity_key,
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
            identity_key=str(payload.get("identity_key", "")),
            is_default=bool(payload.get("is_default", False)),
        )


@dataclass(slots=True)
class _UsageTarget:
    auth_payload: dict[str, object]
    target_account: _StoredCodexAccount | None
    auth_fingerprint: str
    cache_key: str
    cached_usage: CodexUsageInfo | None


@dataclass(slots=True)
class CodexCredentialSyncResult:
    account_label: str | None
    account_email: str | None
    openclaw_paths: tuple[Path, ...]
    hermes_auth_path: Path


class CodexAccountService:
    def __init__(
        self,
        auth_path: Path,
        accounts_dir: Path,
        manifest_path: Path,
        configured_codex_bin: str = "",
        launch_login: Callable[[str], subprocess.Popen[object]] | None = None,
        now: Callable[[], int] | None = None,
        openclaw_auth_profile_paths: tuple[Path, ...] | None = None,
        hermes_auth_path: Path | None = None,
    ) -> None:
        self.auth_path = auth_path
        self.accounts_dir = accounts_dir
        self.manifest_path = manifest_path
        self.configured_codex_bin = configured_codex_bin.strip()
        self.launch_login = launch_login or self._launch_login_terminal
        self.now = now or (lambda: int(time.time()))
        self.openclaw_auth_profile_paths = openclaw_auth_profile_paths or (
            Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json",
            Path.home() / ".openclaw" / "agents" / "codex" / "agent" / "auth-profiles.json",
        )
        self.hermes_auth_path = hermes_auth_path or (Path.home() / ".hermes" / "auth.json")
        self._lock = threading.Lock()
        self._login_in_progress = False

    def list_accounts(self) -> list[CodexAccountSummary]:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                return self._summaries_from_stored_locked(stored_accounts)

    def get_status(self, sessions: list[AgentSession] | None = None) -> CodexAccountStatus:
        device_login_in_progress = self._login_in_progress or self._shared_login_in_progress()
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                auth_payload = self._read_auth_payload()
                active_account = self._find_account_by_payload(stored_accounts, auth_payload)
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
                active_account = self._find_account_by_payload(stored_accounts, auth_payload)
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

    def sync_credentials(self, account_selector: str | None = None) -> CodexCredentialSyncResult:
        auth_payload, account = self._resolve_sync_payload(account_selector)
        tokens = self._sync_tokens_from_payload(auth_payload)
        account_email = self._email_from_auth_payload(auth_payload)
        account_label = account.label if account is not None else account_email
        self._sync_openclaw_auth(tokens)
        self._sync_hermes_auth(tokens, self._string_claim(auth_payload, "last_refresh"), self._auth_mode(auth_payload))
        logger.info(
            "synced Codex credentials account_label=%s account_email=%s openclaw_targets=%s hermes_auth_path=%s",
            account_label or "<none>",
            account_email or "<none>",
            [str(path) for path in self.openclaw_auth_profile_paths],
            self.hermes_auth_path,
        )
        return CodexCredentialSyncResult(
            account_label=account_label,
            account_email=account_email,
            openclaw_paths=self.openclaw_auth_profile_paths,
            hermes_auth_path=self.hermes_auth_path,
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
                    target_account = self._find_account_by_payload(stored_accounts, auth_payload)
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

    def _resolve_sync_payload(
        self,
        account_selector: str | None,
    ) -> tuple[dict[str, object], _StoredCodexAccount | None]:
        with self._locked_accounts_io():
            with self._lock:
                stored_accounts = self._load_accounts_locked()
                current_auth_payload = self._read_auth_payload()
                if account_selector:
                    normalized_selector = account_selector.strip()
                    if not normalized_selector:
                        raise ValueError("Codex account selector is required")
                    if self._auth_payload_has_credentials(current_auth_payload):
                        current_email = self._email_from_auth_payload(current_auth_payload)
                        if current_email is not None and current_email.casefold() == normalized_selector.casefold():
                            return current_auth_payload, self._find_account_by_payload(stored_accounts, current_auth_payload)
                    account = self._require_account(stored_accounts, normalized_selector)
                    auth_payload = self._read_payload_from_path(self._snapshot_path(account.account_id))
                    if auth_payload is None or not self._auth_payload_has_credentials(auth_payload):
                        raise ValueError("stored credentials are missing")
                    return auth_payload, account

                if current_auth_payload is None or not self._auth_payload_has_credentials(current_auth_payload):
                    raise ValueError("Codex is not logged in")
                return current_auth_payload, self._find_account_by_payload(stored_accounts, current_auth_payload)

    def start_device_login(
        self,
        label: str | None = None,
        on_complete: Callable[[bool], None] | None = None,
    ) -> bool:
        login_label = (label or "").strip()
        with self._locked_accounts_io():
            with self._lock:
                if self._login_in_progress or self._shared_login_in_progress():
                    logger.info("Codex login request ignored because a login is already in progress")
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
        return self._run_device_login(label)

    def _run_device_login(self, label: str | None = None, *, local_slot_reserved: bool = False) -> bool:
        login_label = (label or "").strip()
        with self._locked_accounts_io():
            with self._lock:
                if (self._login_in_progress and not local_slot_reserved) or self._shared_login_in_progress():
                    raise ValueError("Codex login already in progress")
                self._login_in_progress = True
                self._write_shared_login_state_locked(os.getpid())
                backup_path = self._prepare_auth_for_new_login_locked()
                status_path = self._login_status_path()
        process: subprocess.Popen[object] | None = None
        success = False
        try:
            logger.info(
                "starting Codex login label=%s auth_path=%s status_path=%s shell_command=%s",
                login_label or "<auto>",
                self.auth_path,
                status_path,
                self._login_shell_command(),
            )
            process = self.launch_login(self._login_shell_command(status_path))
            logger.info(
                "Codex login terminal launched pid=%s label=%s",
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
            logger.info("Codex login completed success=%s label=%s", success, login_label or "<auto>")

    def _run_device_login_background(
        self,
        label: str,
        on_complete: Callable[[bool], None] | None,
    ) -> None:
        success = False
        try:
            success = self._run_device_login(label, local_slot_reserved=True)
        except Exception:
            logger.exception("Codex login watcher failed")
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
        previous_auth_payload = self._read_payload_from_path(backup_path) if backup_path is not None else None
        return_code, auth_payload = self._wait_for_login_credentials(
            process,
            status_path,
            previous_auth_payload,
        )
        has_credentials = auth_payload is not None and self._auth_payload_has_credentials(auth_payload)
        logger.info(
            "Codex login process finished pid=%s return_code=%s has_credentials=%s auth_summary=%s",
            getattr(process, "pid", None),
            return_code,
            has_credentials,
            self._auth_payload_debug_summary(auth_payload),
        )
        if not has_credentials:
            logger.warning(
                "Codex login did not produce importable credentials pid=%s return_code=%s auth_path_exists=%s",
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
            "Codex login imported account account_id=%s label=%s",
            imported.account_id,
            imported.label,
        )
        return True

    def _summaries_from_stored_locked(self, stored_accounts: list[_StoredCodexAccount]) -> list[CodexAccountSummary]:
        auth_payload = self._read_auth_payload()
        active_account = self._find_account_by_payload(stored_accounts, auth_payload)
        summaries: list[CodexAccountSummary] = []
        for account in stored_accounts:
            summaries.append(
                CodexAccountSummary(
                    account_id=account.account_id,
                    label=account.label,
                    is_default=account.is_default,
                    is_active=active_account is not None and account.account_id == active_account.account_id,
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
        auth_payload = self._read_auth_payload()
        if auth_payload is not None and self._auth_payload_has_credentials(auth_payload) and not stored_accounts:
            self._import_current_auth_locked(stored_accounts, "")
        stored_accounts = self._deduplicate_accounts_locked(stored_accounts)
        stored_accounts.sort(key=lambda account: (not account.is_default, account.created_at, account.account_id))
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
        identity_key = self._identity_key_from_auth_payload(auth_payload) or ""
        resolved_label = self._preferred_account_label(auth_payload, stored_accounts, label)
        existing = self._find_account_by_fingerprint(stored_accounts, fingerprint)
        if existing is None:
            existing = self._find_account_by_identity(stored_accounts, identity_key)
        now_ts = self.now()
        if existing is not None:
            if label:
                existing.label = resolved_label
            existing.updated_at = now_ts
            existing.auth_fingerprint = fingerprint
            if identity_key:
                existing.identity_key = identity_key
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
            identity_key=identity_key,
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

        ordinal_account = self._account_by_ordinal(stored_accounts, normalized_selector)
        if ordinal_account is not None:
            return ordinal_account

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

    def _account_by_ordinal(
        self,
        stored_accounts: list[_StoredCodexAccount],
        account_selector: str,
    ) -> _StoredCodexAccount | None:
        if not account_selector.isdecimal():
            return None
        ordinal = int(account_selector)
        if 1 <= ordinal <= len(stored_accounts):
            return stored_accounts[ordinal - 1]
        return None

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

    def _find_account_by_payload(
        self,
        stored_accounts: list[_StoredCodexAccount],
        payload: dict[str, object] | None,
    ) -> _StoredCodexAccount | None:
        if payload is None or not self._auth_payload_has_credentials(payload):
            return None
        fingerprint = self._fingerprint_payload(payload)
        by_fingerprint = self._find_account_by_fingerprint(stored_accounts, fingerprint)
        if by_fingerprint is not None:
            return by_fingerprint
        return self._find_account_by_identity(stored_accounts, self._identity_key_from_auth_payload(payload))

    def _find_account_by_identity(
        self,
        stored_accounts: list[_StoredCodexAccount],
        identity_key: str | None,
    ) -> _StoredCodexAccount | None:
        normalized_identity = identity_key.strip() if isinstance(identity_key, str) else ""
        if not normalized_identity:
            return None
        for account in stored_accounts:
            account_identity = self._identity_key_for_account(account)
            if account_identity == normalized_identity:
                return account
        return None

    def _identity_key_for_account(self, account: _StoredCodexAccount) -> str:
        if account.identity_key.strip():
            return account.identity_key.strip()
        payload = self._read_payload_from_path(self._snapshot_path(account.account_id))
        return self._identity_key_from_auth_payload(payload) or ""

    def _identity_key_from_auth_payload(self, payload: dict[str, object] | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        account_id = self._token_account_id(payload)
        if account_id:
            return f"account_id:{account_id}"
        for token_key in ("id_token", "access_token"):
            claims = self._jwt_payload(self._token_value(payload, token_key))
            if not claims:
                continue
            subject = claims.get("sub")
            if isinstance(subject, str) and subject.strip():
                return f"sub:{subject.strip()}"
        email = self._email_from_auth_payload(payload)
        if email:
            return f"email:{email.casefold()}"
        return None

    def _deduplicate_accounts_locked(self, stored_accounts: list[_StoredCodexAccount]) -> list[_StoredCodexAccount]:
        deduplicated: list[_StoredCodexAccount] = []
        changed = False
        for account in stored_accounts:
            identity_key = self._identity_key_for_account(account)
            if identity_key and account.identity_key != identity_key:
                account.identity_key = identity_key
                changed = True
            duplicate = None
            for existing in deduplicated:
                if identity_key and self._identity_key_for_account(existing) == identity_key:
                    duplicate = existing
                    break
                if not identity_key and account.auth_fingerprint and existing.auth_fingerprint == account.auth_fingerprint:
                    duplicate = existing
                    break
            if duplicate is None:
                deduplicated.append(account)
                continue
            changed = True
            self._merge_duplicate_account_locked(duplicate, account)

        if deduplicated and not any(account.is_default for account in deduplicated):
            deduplicated[0].is_default = True
            changed = True
        if changed:
            self._save_accounts_locked(deduplicated)
        return deduplicated

    def _merge_duplicate_account_locked(
        self,
        target: _StoredCodexAccount,
        duplicate: _StoredCodexAccount,
    ) -> None:
        target_snapshot = self._snapshot_path(target.account_id)
        duplicate_snapshot = self._snapshot_path(duplicate.account_id)
        replace_target_snapshot = duplicate_snapshot.exists() and (
            not target_snapshot.exists() or duplicate.updated_at >= target.updated_at
        )
        target.created_at = min(target.created_at, duplicate.created_at)
        if duplicate.updated_at >= target.updated_at:
            target.updated_at = duplicate.updated_at
            if duplicate.auth_fingerprint:
                target.auth_fingerprint = duplicate.auth_fingerprint
        else:
            target.updated_at = max(target.updated_at, duplicate.updated_at)
        if duplicate.is_default:
            target.is_default = True
        if not target.label.strip() and duplicate.label.strip():
            target.label = duplicate.label
        if not target.identity_key.strip():
            target.identity_key = self._identity_key_for_account(duplicate)
        if replace_target_snapshot:
            target_snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(duplicate_snapshot, target_snapshot)
            target_snapshot.chmod(0o600)
        if duplicate_snapshot.exists():
            duplicate_snapshot.unlink()

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

    def _sync_tokens_from_payload(self, payload: dict[str, object]) -> dict[str, str]:
        raw_tokens = payload.get("tokens")
        if not isinstance(raw_tokens, dict):
            raise ValueError("Codex auth is missing tokens")
        tokens = {
            key: value.strip()
            for key, value in raw_tokens.items()
            if isinstance(key, str) and isinstance(value, str) and value.strip()
        }
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token:
            raise ValueError("Codex auth is missing access token")
        if not refresh_token:
            raise ValueError("Codex auth is missing refresh token")
        account_id = tokens.get("account_id") or self._token_account_id(payload) or self._token_subject(payload, "access_token")
        if not account_id:
            raise ValueError("Codex auth is missing account ID")
        tokens["account_id"] = account_id
        return tokens

    def _sync_openclaw_auth(self, tokens: dict[str, str]) -> None:
        profile_payload = self._openclaw_profile_payload(tokens)
        for path in self.openclaw_auth_profile_paths:
            target = self._read_json_object(path)
            profiles = target.get("profiles")
            if not isinstance(profiles, dict):
                profiles = {}
                target["profiles"] = profiles
            existing = profiles.get(_OPENCLAW_PROFILE_ID)
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(profile_payload)
            profiles[_OPENCLAW_PROFILE_ID] = merged
            defaults = target.get("defaults")
            if not isinstance(defaults, dict):
                defaults = {}
                target["defaults"] = defaults
            defaults["openai-codex"] = _OPENCLAW_PROFILE_ID
            target.setdefault("version", 1)
            self._atomic_write_json(path, target)
            self._sync_openclaw_runtime_state(path)

    def _openclaw_profile_payload(self, tokens: dict[str, str]) -> dict[str, object]:
        access_token = tokens["access_token"]
        return {
            "type": "oauth",
            "provider": "openai-codex",
            "access": access_token,
            "refresh": tokens["refresh_token"],
            "expires": self._jwt_exp_ms(access_token),
            "accountId": tokens["account_id"],
            "managedBy": "codex-cli",
        }

    def _sync_openclaw_runtime_state(self, profile_path: Path) -> None:
        state_path = profile_path.with_name("auth-state.json")
        state = self._read_json_object(state_path)
        last_good = state.get("lastGood")
        if not isinstance(last_good, dict):
            last_good = {}
        last_good["openai-codex"] = _OPENCLAW_PROFILE_ID
        state["lastGood"] = last_good

        usage_stats = state.get("usageStats")
        if isinstance(usage_stats, dict) and _OPENCLAW_PROFILE_ID in usage_stats:
            del usage_stats[_OPENCLAW_PROFILE_ID]
            if usage_stats:
                state["usageStats"] = usage_stats
            else:
                state.pop("usageStats", None)

        state.setdefault("version", 1)
        self._atomic_write_json(state_path, state)

    def _sync_hermes_auth(self, tokens: dict[str, str], last_refresh: str | None, auth_mode: str | None) -> None:
        auth_store = self._read_json_object(self.hermes_auth_path)
        providers = auth_store.get("providers")
        if not isinstance(providers, dict):
            providers = {}
            auth_store["providers"] = providers
        state = providers.get("openai-codex")
        if not isinstance(state, dict):
            state = {}
        state["tokens"] = dict(tokens)
        state["last_refresh"] = last_refresh or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state["auth_mode"] = auth_mode or "chatgpt"
        providers["openai-codex"] = state
        auth_store["active_provider"] = "openai-codex"
        auth_store.setdefault("version", 1)
        self._atomic_write_json(self.hermes_auth_path, auth_store)

    def _read_json_object(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _atomic_write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

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

    def _token_subject(self, payload: dict[str, object], token_key: str) -> str | None:
        claims = self._jwt_payload(self._token_value(payload, token_key))
        if not claims:
            return None
        subject = claims.get("sub")
        return subject.strip() if isinstance(subject, str) and subject.strip() else None

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

    def _jwt_exp_ms(self, token: str) -> int:
        payload = self._jwt_payload(token)
        if not payload:
            raise ValueError("Codex access token is not a valid JWT")
        exp = payload.get("exp")
        if not isinstance(exp, int):
            raise ValueError("Codex access token is missing expiration")
        return exp * 1000

    def _wait_for_login_credentials(
        self,
        process: subprocess.Popen[object],
        status_path: Path,
        previous_auth_payload: dict[str, object] | None,
    ) -> tuple[int | None, dict[str, object] | None]:
        previous_fingerprint = (
            self._fingerprint_payload(previous_auth_payload)
            if self._auth_payload_has_credentials(previous_auth_payload)
            else None
        )
        wait_method = getattr(process, "wait", None)
        poll_method = getattr(process, "poll", None)
        deadline = time.monotonic() + _DEVICE_LOGIN_TIMEOUT_SECONDS
        launcher_return_code: int | None = None
        launcher_exit_logged = False

        if not callable(poll_method) and callable(wait_method):
            launcher_return_code = int(wait_method())
            logger.info(
                "Codex login launcher exited pid=%s return_code=%s status_path=%s",
                getattr(process, "pid", None),
                launcher_return_code,
                status_path,
            )
            launcher_exit_logged = True
            auth_payload = self._read_auth_payload()
            if self._is_new_login_auth_payload(auth_payload, previous_fingerprint):
                return launcher_return_code, auth_payload
            if launcher_return_code != 0 and not status_path.exists():
                return launcher_return_code, None
            deadline = min(deadline, time.monotonic() + max(0.1, _DEVICE_LOGIN_POLL_INTERVAL_SECONDS * 10))

        while time.monotonic() < deadline:
            auth_payload = self._read_auth_payload()
            if self._is_new_login_auth_payload(auth_payload, previous_fingerprint):
                return launcher_return_code, auth_payload

            status = self._read_login_status(status_path)
            if status is not None and launcher_return_code is None:
                launcher_return_code = status
                logger.info(
                    "Codex login launcher status observed pid=%s return_code=%s status_path=%s",
                    getattr(process, "pid", None),
                    launcher_return_code,
                    status_path,
                )
            if callable(poll_method):
                polled_return_code = poll_method()
                if polled_return_code is not None and not launcher_exit_logged:
                    launcher_return_code = polled_return_code
                    logger.info(
                        "Codex login launcher exited pid=%s return_code=%s status_path=%s",
                        getattr(process, "pid", None),
                        launcher_return_code,
                        status_path,
                    )
                    launcher_exit_logged = True
            time.sleep(_DEVICE_LOGIN_POLL_INTERVAL_SECONDS)

        auth_payload = self._read_auth_payload()
        if self._is_new_login_auth_payload(auth_payload, previous_fingerprint):
            return launcher_return_code, auth_payload
        logger.warning(
            "Codex login timed out waiting for updated credentials status_path=%s return_code=%s",
            status_path,
            launcher_return_code,
        )
        return launcher_return_code, None

    def _is_new_login_auth_payload(
        self,
        auth_payload: dict[str, object] | None,
        previous_fingerprint: str | None,
    ) -> bool:
        if not self._auth_payload_has_credentials(auth_payload):
            return False
        if previous_fingerprint is None:
            return True
        if auth_payload is None:
            return False
        return self._fingerprint_payload(auth_payload) != previous_fingerprint

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
        base_command = f"{codex_executable} login"
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
