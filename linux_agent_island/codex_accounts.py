from __future__ import annotations

import base64
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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .core.models import AgentSession, CodexAccountStatus, CodexAccountSummary


logger = logging.getLogger(__name__)

_DEVICE_LOGIN_TIMEOUT_SECONDS = 900
_DEVICE_LOGIN_POLL_INTERVAL_SECONDS = 0.2


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


class CodexAccountService:
    def __init__(
        self,
        auth_path: Path,
        accounts_dir: Path,
        manifest_path: Path,
        launch_login: Callable[[str], subprocess.Popen[object]] | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        self.auth_path = auth_path
        self.accounts_dir = accounts_dir
        self.manifest_path = manifest_path
        self.launch_login = launch_login or self._launch_login_terminal
        self.now = now or (lambda: int(time.time()))
        self._lock = threading.Lock()
        self._login_in_progress = False

    def list_accounts(self) -> list[CodexAccountSummary]:
        with self._lock:
            stored_accounts = self._load_accounts_locked()
            return self._summaries_from_stored_locked(stored_accounts)

    def get_status(self, sessions: list[AgentSession] | None = None) -> CodexAccountStatus:
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
                    else ("External login" if self._auth_payload_has_credentials(auth_payload) else None)
                ),
                current_account_managed=active_account is not None,
                device_login_in_progress=self._login_in_progress,
                switch_affects_new_sessions_only=True,
                has_running_codex_sessions=has_running_codex_sessions,
                accounts=summaries,
            )

    def rename_account(self, account_id: str, label: str) -> None:
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("account label is required")
        with self._lock:
            stored_accounts = self._load_accounts_locked()
            account = self._require_account(stored_accounts, account_id)
            account.label = normalized_label
            account.updated_at = self.now()
            self._save_accounts_locked(stored_accounts)

    def delete_account(self, account_id: str) -> None:
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
        with self._lock:
            stored_accounts = self._load_accounts_locked()
            self._require_account(stored_accounts, account_id)
            for account in stored_accounts:
                account.is_default = account.account_id == account_id
                if account.is_default:
                    account.updated_at = self.now()
            self._save_accounts_locked(stored_accounts)

    def switch_account(self, account_id: str) -> CodexAccountStatus:
        with self._lock:
            stored_accounts = self._load_accounts_locked()
            account = self._require_account(stored_accounts, account_id)
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
        logger.info("switched Codex account account_id=%s", account_id)
        return self.get_status()

    def start_device_login(
        self,
        label: str | None = None,
        on_complete: Callable[[bool], None] | None = None,
    ) -> bool:
        backup_path: Path | None = None
        status_path = self._login_status_path()
        with self._lock:
            if self._login_in_progress:
                logger.info("Codex device login request ignored because a login is already in progress")
                return False
            self._login_in_progress = True
            backup_path = self._prepare_auth_for_new_login_locked()
        login_label = (label or "").strip()
        logger.info(
            "starting Codex device login label=%s auth_path=%s status_path=%s shell_command=%s",
            login_label or "<auto>",
            self.auth_path,
            status_path,
            self._login_shell_command(),
        )
        try:
            process = self.launch_login(
                self._login_shell_command(status_path),
            )
            logger.info(
                "Codex device login terminal launched pid=%s label=%s",
                getattr(process, "pid", None),
                login_label or "<auto>",
            )
        except Exception:
            with self._lock:
                self._restore_login_backup_locked(backup_path)
                self._login_in_progress = False
            logger.exception("failed to launch Codex device login terminal")
            raise

        watcher = threading.Thread(
            target=self._wait_for_login_completion,
            args=(process, login_label, backup_path, status_path, on_complete),
            daemon=True,
        )
        watcher.start()
        return True

    def _wait_for_login_completion(
        self,
        process: subprocess.Popen[object],
        label: str,
        backup_path: Path | None,
        status_path: Path,
        on_complete: Callable[[bool], None] | None,
    ) -> None:
        success = False
        try:
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
            if return_code == 0 and has_credentials:
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
                success = True
            else:
                logger.warning(
                    "Codex device login did not produce importable credentials pid=%s return_code=%s auth_path_exists=%s",
                    getattr(process, "pid", None),
                    return_code,
                    self.auth_path.exists(),
                )
        except Exception:
            logger.exception("Codex device login watcher failed")
        finally:
            with self._lock:
                if not success:
                    self._restore_login_backup_locked(backup_path)
                self._login_in_progress = False
            self._cleanup_login_status(status_path)
            logger.info("Codex device login watcher completed success=%s label=%s", success, label or "<auto>")
            if on_complete is not None:
                on_complete(success)

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
        resolved_label = label.strip() or self._default_account_label(auth_payload, stored_accounts)
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
        account_id: str,
    ) -> _StoredCodexAccount:
        for account in stored_accounts:
            if account.account_id == account_id:
                return account
        raise ValueError(f"unknown Codex account: {account_id}")

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

    def _read_auth_payload(self) -> dict[str, object] | None:
        if not self.auth_path.exists():
            return None
        try:
            payload = json.loads(self.auth_path.read_text(encoding="utf-8"))
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

    def _default_account_label(
        self,
        auth_payload: dict[str, object],
        stored_accounts: list[_StoredCodexAccount],
    ) -> str:
        email = self._email_from_auth_payload(auth_payload)
        if email:
            return email
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

    def _email_from_jwt(self, token: object) -> str | None:
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
        base_command = "codex login"
        if status_path is None:
            return base_command
        return (
            f"{base_command}; status=$?; "
            f"mkdir -p {shlex.quote(str(status_path.parent))}; "
            f"printf '%s\\n' \"$status\" > {shlex.quote(str(status_path))}"
        )

    def _launch_login_terminal(self, shell_command: str) -> subprocess.Popen[object]:
        command = self._terminal_launch_command(shell_command)
        logger.info("launching Codex login terminal command=%s", shlex.join(command))
        return subprocess.Popen(command)

    def _terminal_launch_command(self, shell_command: str) -> list[str]:
        hold_command = (
            f"{shell_command}; status=$?; "
            "printf '\\nPress Enter to close...'; "
            "read _; "
            "exit $status"
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
