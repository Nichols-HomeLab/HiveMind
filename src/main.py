#!/usr/bin/env python3
"""
HiveMind - GitOps for Docker Swarm
Main entry point
"""

import os
import sys
import logging

from .controller import HiveMind

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('hivemind')


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python -m src.main <config.yml>")
        sys.exit(1)
    
    config_path = sys.argv[1]
    
    if not os.path.exists(config_path):
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    
    hivemind = HiveMind(config_path)
    
    if len(sys.argv) > 2 and sys.argv[2] == "bootstrap":
        hivemind.bootstrap()
    else:
        hivemind.run()


if __name__ == "__main__":
    main()
