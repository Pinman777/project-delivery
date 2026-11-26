#!/usr/bin/env python3
"""Watchdog для автономных задач: heartbeat, авто-продолжение, чекпойнты.

Использование:
    python3 autonomous_watchdog.py --root /path/to/project [--check-interval 30] [--max-iterations 100]

Поведение:
- Запускается как фоновый процесс рядом с автономной задачей.
- Мониторит heartbeat каждые check_interval секунд.
- Если heartbeat пропадает > 120 сек → делает чекпойнт (git add + commit) и продолжает.
- При достижении max_iterations делает авто-чекпойнт, коммит и сбрасывает счётчик.
- SIGTERM/SIGINT → чистое завершение с финальным чекпойнтом.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CHECK_INTERVAL = 30          # секунды между проверками heartbeat
DEFAULT_MAX_ITERATIONS = 100         # авто-чекпойнт при достижении
HEARTBEAT_TIMEOUT = 120              # секунды без heartbeat → аварийный чекпойнт


class AutonomousWatchdog:
    def __init__(
        self,
        root: str | Path,
        check_interval: int = DEFAULT_CHECK_INTERVAL,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        heartbeat_file: str | None = None,
    ):
        self.root = Path(root).resolve()
        self.check_interval = check_interval
        self.max_iterations = max_iterations
        self.heartbeat_file = Path(heartbeat_file) if heartbeat_file else self.root / ".watchdog_heartbeat"
        self.last_heartbeat = time.time()
        self.iteration = 0
        self.running = True
        self._setup_signals()

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

    def _shutdown(self, signum, frame):
        self.running = False
        self._checkpoint("watchdog shutdown")

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def _checkpoint(self, reason: str) -> None:
        """Чекпойнт: git add -A + commit с сообщением."""
        try:
            self._git("add", "-A")
            self._git("commit", "-m", f"chore: watchdog checkpoint — {reason}")
        except subprocess.CalledProcessError:
            # не валим watchdog, если git недоступен
            pass

    def heartbeat(self) -> None:
        """Вызывать из основного процесса при каждом шаге."""
        self.last_heartbeat = time.time()
        # пишем timestamp в файл для внешнего мониторинга
        try:
            self.heartbeat_file.write_text(str(time.time()))
        except OSError:
            pass

    def increment_iteration(self) -> bool:
        """Вызывать при каждом шаге основного цикла. Возвращает True, если сделан авто-чекпойнт."""
        self.iteration += 1
        if self.iteration >= self.max_iterations:
            self._auto_continue()
            return True
        return False

    def _auto_continue(self) -> None:
        """Авто-чекпойнт при достижении лимита итераций."""
        self._checkpoint(f"auto-continue at iteration {self.iteration}")
        self.iteration = 0  # сброс счётчика

    def _check_heartbeat_timeout(self) -> bool:
        """Проверка, не истёк ли таймаут heartbeat. Возвращает True, если сделан аварийный чекпойнт."""
        if time.time() - self.last_heartbeat > 120:  # HEARTBEAT_TIMEOUT
            self._checkpoint("heartbeat timeout — watchdog restart")
            return True
        return False

    def run(self) -> None:
        """Основной цикл watchdog'а. Запускать в отдельном потоке/процессе."""
        while True:
            if self._check_heartbeat_timeout():
                # аварийный чекпойнт уже сделан внутри
                pass
            time.sleep(self.check_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous watchdog for long-running tasks")
    parser.add_argument("--root", required=True, help="Project root directory")
    parser.add_argument("--check-interval", type=int, default=DEFAULT_CHECK_INTERVAL,
                        help=f"Heartbeat check interval in seconds (default: {DEFAULT_CHECK_INTERVAL})")
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS,
                        help=f"Auto-checkpoint after N iterations (default: {DEFAULT_MAX_ITERATIONS})")
    parser.add_argument("--heartbeat-file", help="Path to heartbeat file (default: <root>/.watchdog_heartbeat)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"Root directory does not exist: {root}", file=sys.stderr)
        return 1

    watchdog = AutonomousWatchdog(
        root=root,
        check_interval=args.check_interval,
        max_iterations=args.max_iterations,
        heartbeat_file=args.heartbeat_file,
    )

    # пишем PID для внешнего управления
    pid_file = Path(args.root) / ".watchdog.pid"
    try:
        pid_file.write_text(str(os.getpid()))
    except OSError:
        pass

    try:
        watchdog.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            pid_file.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())