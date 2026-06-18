import logging
import subprocess
import sys
import time
from pathlib import Path

SENTINEL = Path("/opt/igor/restart_requested")
POLL_INTERVAL = 5
COOLDOWN = 300

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s igor-watchdog: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Started, monitoring %s", SENTINEL)
    last_restart: float = 0

    while True:
        if SENTINEL.exists():
            try:
                reason = SENTINEL.read_text(encoding="utf-8").strip()
                SENTINEL.unlink()
            except Exception as e:
                logger.error("Failed to read/delete sentinel: %s", e)
                time.sleep(POLL_INTERVAL)
                continue

            now = time.time()
            elapsed = now - last_restart
            if elapsed < COOLDOWN:
                remaining = int(COOLDOWN - elapsed)
                logger.warning("Restart requested (%s) - cooldown active, %ds remaining", reason, remaining)
            else:
                logger.info("Restarting igor: %s", reason)
                last_restart = now
                result = subprocess.run(
                    ["sudo", "systemctl", "restart", "igor"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.info("Igor restarted successfully")
                else:
                    logger.error("Restart failed (exit %d): %s", result.returncode, result.stderr.strip())

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
