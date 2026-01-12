#!/usr/bin/env python3
"""
HiveMind - GitOps for Docker Swarm
Main entry point
"""

import os
import sys
import logging
import tempfile
from pathlib import Path
import yaml

from .controller import HiveMind

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('hivemind')


def _build_config_from_env() -> dict:
    def strip_quotes(value):
        """Strip surrounding quotes from environment variable values"""
        if value and isinstance(value, str):
            return value.strip('"').strip("'")
        return value
    
    git_cfg = {
        "url": strip_quotes(os.environ.get("HIVEMIND_GIT_URL")),
        "branch": strip_quotes(os.environ.get("HIVEMIND_GIT_BRANCH", "main")),
        "path": strip_quotes(os.environ.get("HIVEMIND_GIT_PATH", ".")),
        "username": strip_quotes(os.environ.get("HIVEMIND_GIT_USERNAME")),
        "password": strip_quotes(os.environ.get("HIVEMIND_GIT_PASSWORD")),
        "poll_interval": int(os.environ.get("HIVEMIND_GIT_POLL_INTERVAL", "60")),
    }

    if not git_cfg["url"]:
        raise ValueError("Missing required env var HIVEMIND_GIT_URL")

    return {"git": git_cfg}


def _write_temp_config(config: dict) -> str:
    config_dir = Path(tempfile.gettempdir()) / "hivemind"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "hivemind-config.yml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False)
    return str(config_path)


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python -m src.main <config.yml>")
        sys.exit(1)
    
    config_path = sys.argv[1]

    if not os.path.exists(config_path):
        logger.warning(f"Configuration file not found: {config_path}. Falling back to environment variables.")
        try:
            config = _build_config_from_env()
            config_path = _write_temp_config(config)
            logger.info(f"Using generated configuration at {config_path}")
        except Exception as e:
            logger.error(f"Failed to build configuration from environment: {e}")
            sys.exit(1)
    
    hivemind = HiveMind(config_path)
    
    if len(sys.argv) > 2 and sys.argv[2] == "bootstrap":
        hivemind.bootstrap()
    else:
        hivemind.run()


if __name__ == "__main__":
    main()
