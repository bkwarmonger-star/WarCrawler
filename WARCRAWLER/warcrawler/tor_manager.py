"""Manage a bundled Tor process and rotate circuits.

Launches the Tor binary shipped on the USB stick (or found on PATH), waits for
bootstrap, and rotates circuits via the control port (NEWNYM) when a target
starts blocking. Circuit *isolation* per worker is achieved separately by
using distinct SOCKS username/password pairs (Tor's IsolateSOCKSAuth).

This module is only imported/used when Tor transport is enabled, so the base
build has no hard dependency on Tor or stem.
"""
from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .config import TorConfig
from .logutil import get_logger

log = get_logger()


def _default_binary(base_dir: Path) -> Optional[str]:
    system = platform.system().lower()
    exe = "tor.exe" if system.startswith("win") else "tor"
    # Look for a per-OS bundled Tor under <base>/tor/<os>/
    candidates = [
        base_dir / "tor" / system / exe,
        base_dir / "tor" / exe,
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which("tor")
    return found


class TorManager:
    def __init__(self, cfg: TorConfig, base_dir: Path, data_dir: Path):
        self.cfg = cfg
        self.base_dir = Path(base_dir)
        self.data_dir = Path(data_dir)
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._sync_proc: Optional[subprocess.Popen] = None
        self._last_newnym = 0.0
        self._binary = cfg.tor_binary or _default_binary(self.base_dir)

    def _torrc(self) -> Path:
        tor_data = Path(self.cfg.data_dir) if self.cfg.data_dir else (self.data_dir / "tor")
        tor_data.mkdir(parents=True, exist_ok=True)
        # Forward slashes + quotes so paths with spaces (common on Windows,
        # e.g. C:\Users\John Doe\...) parse correctly in torrc.
        data_path = str(tor_data.resolve()).replace("\\", "/")
        lines = [
            "SocksPort {}".format(self.cfg.socks_port),
            "ControlPort {}".format(self.cfg.control_port),
            'DataDirectory "{}"'.format(data_path),
            "CookieAuthentication 1",
            "IsolateSOCKSAuth 1",
            "AvoidDiskWrites 1",
        ]
        path = tor_data / "torrc"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    async def start(self) -> None:
        if not self.cfg.auto_start:
            log.info("tor auto_start disabled; assuming Tor already on :%d",
                     self.cfg.socks_port)
            return
        if not self._binary:
            raise RuntimeError(
                "Tor binary not found. Drop the Tor Expert Bundle into "
                "<usb>/tor/<os>/ or set tor.tor_binary, or install tor on PATH.")
        torrc = self._torrc()
        log.info("starting Tor: %s", self._binary)
        self.proc = await asyncio.create_subprocess_exec(
            self._binary, "-f", str(torrc),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        await self._await_bootstrap()

    async def _await_bootstrap(self) -> None:
        assert self.proc and self.proc.stdout
        deadline = time.time() + self.cfg.bootstrap_timeout
        tail = []  # keep recent Tor output to surface the real error on failure
        while time.time() < deadline:
            try:
                line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                if self.proc.returncode is not None:
                    raise RuntimeError(self._exit_msg(tail))
                continue
            if not line:
                if self.proc.returncode is not None:
                    raise RuntimeError(self._exit_msg(tail))
                continue
            text = line.decode("utf-8", "replace").strip()
            log.debug("tor: %s", text)
            if text:
                tail.append(text)
                del tail[:-15]
            if "Bootstrapped 100%" in text:
                log.info("Tor bootstrapped, SOCKS on :%d", self.cfg.socks_port)
                return
        raise RuntimeError("Tor bootstrap timed out after {}s. Recent output:\n{}".format(
            self.cfg.bootstrap_timeout, "\n".join(tail[-8:])))

    @staticmethod
    def _exit_msg(tail) -> str:
        hint = "\n".join(tail[-8:]) or "(no output)"
        return "Tor exited during startup. Recent output:\n" + hint

    # ---- synchronous variant (for the multi-process parent) --------------
    def start_sync(self) -> None:
        """Blocking start; used by the parent to run ONE Tor shared by all
        child processes. The OS process outlives the caller's event loops."""
        if not self.cfg.auto_start:
            log.info("tor auto_start disabled; assuming Tor on :%d", self.cfg.socks_port)
            return
        if not self._binary:
            raise RuntimeError(
                "Tor binary not found. Drop the Tor Expert Bundle into "
                "<usb>/tor/<os>/ or set tor.tor_binary, or install tor on PATH.")
        torrc = self._torrc()
        log.info("starting shared Tor: %s", self._binary)
        self._sync_proc = subprocess.Popen(
            [self._binary, "-f", str(torrc)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + self.cfg.bootstrap_timeout
        tail = []
        while time.time() < deadline:
            line = self._sync_proc.stdout.readline()
            if not line:
                if self._sync_proc.poll() is not None:
                    raise RuntimeError(self._exit_msg(tail))
                continue
            text = line.strip()
            log.debug("tor: %s", text)
            if text:
                tail.append(text)
                del tail[:-15]
            if "Bootstrapped 100%" in line:
                log.info("Tor bootstrapped, SOCKS on :%d", self.cfg.socks_port)
                return
        raise RuntimeError("Tor bootstrap timed out after {}s. Recent output:\n{}".format(
            self.cfg.bootstrap_timeout, "\n".join(tail[-8:])))

    def stop_sync(self) -> None:
        if self._sync_proc and self._sync_proc.poll() is None:
            try:
                self._sync_proc.terminate()
                self._sync_proc.wait(timeout=10)
            except Exception:
                try:
                    self._sync_proc.kill()
                except Exception:
                    pass

    async def new_circuit(self) -> None:
        """Signal NEWNYM to get fresh circuits (rate-limited to once / 10s)."""
        now = time.time()
        if now - self._last_newnym < 10.0:
            return
        self._last_newnym = now
        await asyncio.get_event_loop().run_in_executor(None, self._signal_newnym)

    def _signal_newnym(self) -> None:
        try:
            from stem import Signal
            from stem.control import Controller
            with Controller.from_port(port=self.cfg.control_port) as controller:
                if self.cfg.control_password:
                    controller.authenticate(password=self.cfg.control_password)
                else:
                    controller.authenticate()  # cookie auth
                controller.signal(Signal.NEWNYM)
                log.info("Tor: requested new circuits (NEWNYM)")
        except Exception as exc:  # pragma: no cover
            log.warning("NEWNYM failed (%s); continuing on current circuits", exc)

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
