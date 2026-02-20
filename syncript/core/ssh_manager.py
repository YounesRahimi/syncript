"""
SSH connection manager with auto-reconnect and keep-alive
"""
from typing import Optional
import paramiko
from .. import config as _cfg
from ..utils.logging import log
from ..utils.retry import retried


class SSHManager:
    """
    Wraps paramiko SSHClient + SFTPClient.
    Automatically reconnects on channel errors.
    Sends SSH keep-alives to reduce mid-transfer drops.
    """

    def __init__(self):
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    # ── connection ─────────────────────────────────────────────────────────

    def connect(self):
        if self._ssh:
            try:
                self._ssh.get_transport().send_ignore()  # test if alive
                return
            except Exception:
                self._close_quietly()

        log(f"[SSH] connecting to {_cfg.SSH_USER}@{_cfg.SSH_HOST}:{_cfg.SSH_PORT} …")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = dict(hostname=_cfg.SSH_HOST, port=_cfg.SSH_PORT, username=_cfg.SSH_USER,
                        timeout=20, banner_timeout=30, auth_timeout=30)
        if _cfg.SSH_KEY_PATH:
            kw["key_filename"] = _cfg.SSH_KEY_PATH
        if _cfg.SSH_PASSWORD:
            kw["password"] = _cfg.SSH_PASSWORD

        client.connect(**kw)

        # Keep-alive: send a NOP every 30s
        transport = client.get_transport()
        transport.set_keepalive(30)

        self._ssh = client
        self._sftp = client.open_sftp()
        log("[SSH] connected ✓")

    def _close_quietly(self):
        try:
            if self._sftp:
                self._sftp.close()
        except Exception:
            pass
        try:
            if self._ssh:
                self._ssh.close()
        except Exception:
            pass
        self._ssh = None
        self._sftp = None

    def disconnect(self):
        self._close_quietly()
        log("[SSH] disconnected.")

    def ensure_connected(self):
        """Call before any remote operation."""
        try:
            if self._ssh and self._ssh.get_transport().is_active():
                return
        except Exception:
            pass
        self.connect()

    # ── raw exec ────────────────────────────────────────────────────────────

    @retried
    def exec(self, cmd: str, timeout: int = 30) -> tuple[str, str]:
        """Run a command; return (stdout, stderr). Raises on non-zero exit."""
        self.ensure_connected()
        _, stdout, stderr = self._ssh.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            raise RuntimeError(f"remote command exited {rc}: {cmd!r}\nstderr: {err.strip()}")
        return out, err

    def exec_nowait(self, cmd: str):
        """Fire-and-forget: don't wait for exit status (for tmux / bg jobs)."""
        self.ensure_connected()
        self._ssh.exec_command(cmd)

    # ── sftp ops ────────────────────────────────────────────────────────────

    @retried
    def sftp_put(self, local: str, remote: str):
        self.ensure_connected()
        self._sftp.put(local, remote)

    @retried
    def sftp_get(self, remote: str, local: str):
        self.ensure_connected()
        self._sftp.get(remote, local)

    @retried
    def sftp_stat(self, remote: str):
        self.ensure_connected()
        return self._sftp.stat(remote)

    @retried
    def sftp_remove(self, remote: str):
        self.ensure_connected()
        self._sftp.remove(remote)

    def sftp_exists(self, remote: str) -> bool:
        try:
            self.sftp_stat(remote)
            return True
        except (FileNotFoundError, IOError):
            return False

    @retried
    def sftp_read_text(self, remote: str) -> str:
        self.ensure_connected()
        with self._sftp.open(remote, "r") as f:
            return f.read().decode("utf-8", errors="replace")
