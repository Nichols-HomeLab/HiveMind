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

def strip_quotes(value):
    """Strip surrounding quotes from environment variable values"""
    if value and isinstance(value, str):
        return value.strip('"').strip("'")
    return value

def _build_config_from_env() -> dict:
    git_cfg = {
        "url": strip_quotes(os.environ.get("HIVEMIND_GIT_URL")),
        "branch": strip_quotes(os.environ.get("HIVEMIND_GIT_BRANCH", "main")),
        "path": strip_quotes(os.environ.get("HIVEMIND_GIT_PATH", ".")),
        "username": strip_quotes(os.environ.get("HIVEMIND_GIT_USERNAME")),
        "password": strip_quotes(os.environ.get("HIVEMIND_GIT_PASSWORD")),
        "poll_interval": int(os.environ.get("HIVEMIND_GIT_POLL_INTERVAL", "60")),
    }
    
    logger.debug(f"Git URL: {git_cfg['url']}")
    logger.debug(f"Git branch: {git_cfg['branch']}")
    logger.debug(f"Git path: {git_cfg['path']}")
    logger.debug(f"Poll interval: {git_cfg['poll_interval']}s")

    if not git_cfg["url"]:
        logger.error("Missing required environment variable: HIVEMIND_GIT_URL")
        raise ValueError("Missing required env var HIVEMIND_GIT_URL")
    
    logger.info("Successfully built configuration from environment variables")
    return {"git": git_cfg}


def _write_temp_config(config: dict) -> str:
    logger.debug("Writing temporary configuration file")
    try:
        config_dir = Path(tempfile.gettempdir()) / "hivemind"
        logger.debug(f"Creating config directory: {config_dir}")
        config_dir.mkdir(exist_ok=True)
        config_path = config_dir / "hivemind-config.yml"
        logger.debug(f"Writing config to: {config_path}")
        with open(config_path, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False)
        logger.info(f"Temporary configuration written to {config_path}")
        return str(config_path)
    except Exception as e:
        logger.error(f"Failed to write temporary config file: {e}", exc_info=True)
        raise


def main():
    """Main entry point"""
    logger.info("Starting HiveMind application")
    logger.debug(f"Command line arguments: {sys.argv}")
    
    if len(sys.argv) < 2:
        logger.error("No configuration file provided")
        print("Usage: python -m src.main <config.yml>")
        sys.exit(1)
    
    config_path = sys.argv[1]
    logger.debug(f"Configuration path: {config_path}")

    if not os.path.exists(config_path):
        logger.warning(f"Configuration file not found: {config_path}. Falling back to environment variables.")
        try:
            config = _build_config_from_env()
            config_path = _write_temp_config(config)
            logger.info(f"Using generated configuration at {config_path}")
        except Exception as e:
            logger.error(f"Failed to build configuration from environment: {e}", exc_info=True)
            sys.exit(1)
    else:
        logger.info(f"Using configuration file: {config_path}")
    
    try:
        logger.debug("Initializing HiveMind controller")
        hivemind = HiveMind(config_path)
        logger.info("HiveMind controller initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize HiveMind: {e}", exc_info=True)
        sys.exit(1)
    
    if len(sys.argv) > 2 and sys.argv[2] == "bootstrap":
        logger.info("Running in bootstrap mode")
        try:
            hivemind.bootstrap()
        except Exception as e:
            logger.error(f"Bootstrap failed: {e}", exc_info=True)
            sys.exit(1)
    else:
        logger.info("Running in continuous mode")
        try:
            hivemind.run()
        except Exception as e:
            logger.error(f"Runtime error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
