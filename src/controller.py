"""HiveMind main controller"""

import time
import yaml
import logging
import tempfile
from pathlib import Path
from typing import List

from .git_manager import GitRepository, GitConfig
from .stack_manager import SwarmStackManager, StackConfig

logger = logging.getLogger("hivemind.controller")


class HiveMind:
    """Main HiveMind controller"""
    
    def __init__(self, config_path: str):
        logger.debug(f"Initializing HiveMind with config: {config_path}")
        self.config_path = Path(config_path)
        logger.debug("Loading configuration file")
        self.config = self._load_config()
        logger.info("Configuration loaded successfully")
        
        self.work_dir = Path(tempfile.gettempdir()) / "hivemind"
        logger.debug(f"Work directory: {self.work_dir}")
        self.work_dir.mkdir(exist_ok=True)
        logger.debug("Work directory created/verified")
        
        logger.debug("Initializing Git repository manager")
        git_config = GitConfig(**self.config['git'])
        self.git_repo = GitRepository(git_config, str(self.work_dir))
        logger.info(f"Git repository manager initialized for {git_config.url}")
        
        logger.debug("Initializing Swarm stack manager")
        self.stack_manager = SwarmStackManager()
        logger.info("Swarm stack manager initialized")
        
        self.running = False
        logger.debug("HiveMind initialization complete")
    
    def _load_config(self) -> dict:
        """Load HiveMind configuration"""
        logger.debug(f"Loading configuration from {self.config_path}")
        try:
            with open(self.config_path, "r") as f:
                config = yaml.safe_load(f)
            logger.debug(f"Configuration keys: {list(config.keys())}")
            return config
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in configuration file: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}", exc_info=True)
            raise
    
    def _load_stacks_config(self) -> List[StackConfig]:
        """Load stacks configuration from repository or local config"""
        logger.debug("Loading stacks configuration")
        stacks_path = self.config.get("stacks_file", "stacks.yml")
        logger.debug(f"Stacks file path: {stacks_path}")
        stacks_file = self.git_repo.get_file_path(stacks_path)
        logger.debug(f"Full stacks file path: {stacks_file}")
        stacks_data = None

        if stacks_file.exists():
            logger.info(f"Loading stacks from repository file: {stacks_file}")
            try:
                with open(stacks_file, "r") as f:
                    stacks_data = yaml.safe_load(f)
                logger.debug(f"Loaded stacks data from file")
            except yaml.YAMLError as e:
                logger.error(f"Invalid YAML in stacks file: {e}", exc_info=True)
                return []
            except Exception as e:
                logger.error(f"Failed to read stacks file: {e}", exc_info=True)
                return []
        elif self.config.get("stacks"):
            stacks_data = {"stacks": self.config.get("stacks")}
            logger.info("Using stacks defined in HiveMind config")
        else:
            logger.warning(f"Stacks configuration not found at {stacks_file}")
            return []
        
        stacks = []
        for stack_data in stacks_data.get('stacks', []):
            try:
                stack = StackConfig(**stack_data)
                stacks.append(stack)
                logger.debug(f"Loaded stack configuration: {stack.name}")
            except Exception as e:
                logger.error(f"Failed to parse stack configuration: {e}", exc_info=True)
                continue
        
        logger.info(f"Loaded {len(stacks)} stack configuration(s)")
        return stacks
    
    def reconcile(self):
        """Reconcile desired state with actual state"""
        logger.info("=" * 60)
        logger.info("Starting reconciliation")
        logger.debug(f"Current commit: {self.git_repo.current_commit}")
        
        try:
            has_changes = self.git_repo.clone_or_pull()
            logger.debug(f"Repository changes detected: {has_changes}")
        except Exception as e:
            logger.error(f"Failed to sync repository: {e}", exc_info=True)
            return
        
        if not has_changes and self.git_repo.current_commit:
            logger.info("No changes detected, skipping reconciliation")
            return
        
        logger.debug("Loading stacks configuration")
        stacks = self._load_stacks_config()
        
        if not stacks:
            logger.warning("No stacks configured")
            return
        
        enabled_stacks = set()
        logger.info(f"Processing {len(stacks)} stack(s)")
        
        for stack in stacks:
            logger.debug(f"Processing stack: {stack.name} (enabled={stack.enabled})")
            if stack.enabled:
                enabled_stacks.add(stack.name)
                compose_path = self.git_repo.get_file_path(stack.compose_file)
                logger.debug(f"Compose file path for {stack.name}: {compose_path}")
                
                if not compose_path.exists():
                    logger.error(f"Compose file not found for stack {stack.name}: {compose_path}")
                    continue
                
                env_file = None
                if stack.env_file:
                    env_file = self.git_repo.get_file_path(stack.env_file)
                    logger.debug(f"Environment file for {stack.name}: {env_file}")
                    if not env_file.exists():
                        logger.warning(f"Environment file specified but not found: {env_file}")
                        env_file = None
                
                logger.info(f"Deploying stack: {stack.name}")
                try:
                    self.stack_manager.deploy_stack(stack, compose_path, env_file)
                except Exception as e:
                    logger.error(f"Failed to deploy stack {stack.name}: {e}", exc_info=True)
            else:
                logger.info(f"Stack {stack.name} is disabled, skipping deployment")
        
        logger.debug("Checking for stacks to remove")
        deployed_stacks = set(self.stack_manager.list_stacks())
        managed_stacks = set(s.name for s in stacks)
        logger.debug(f"Deployed stacks: {deployed_stacks}")
        logger.debug(f"Managed stacks: {managed_stacks}")
        logger.debug(f"Enabled stacks: {enabled_stacks}")
        
        for stack_name in deployed_stacks:
            if stack_name in managed_stacks and stack_name not in enabled_stacks:
                logger.info(f"Stack {stack_name} is disabled, removing")
                try:
                    self.stack_manager.remove_stack(stack_name)
                except Exception as e:
                    logger.error(f"Failed to remove stack {stack_name}: {e}", exc_info=True)
        
        logger.info("Reconciliation complete")
        logger.info("=" * 60)
    
    def run(self):
        """Run the main reconciliation loop"""
        logger.info("HiveMind starting...")
        self.running = True
        
        poll_interval = self.config['git'].get('poll_interval', 60)
        logger.info(f"Poll interval set to {poll_interval} seconds")
        
        try:
            logger.info("Entering main reconciliation loop")
            while self.running:
                try:
                    logger.debug("Starting reconciliation cycle")
                    self.reconcile()
                    logger.debug("Reconciliation cycle completed")
                except Exception as e:
                    logger.error(f"Reconciliation error: {e}", exc_info=True)
                    logger.warning("Continuing despite reconciliation error")
                
                logger.info(f"Sleeping for {poll_interval} seconds")
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
            logger.info("Shutting down HiveMind gracefully")
            self.running = False
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            self.running = False
            raise
        finally:
            logger.info("HiveMind stopped")
    
    def bootstrap(self):
        """Bootstrap HiveMind - initial setup"""
        logger.info("=" * 60)
        logger.info("Bootstrapping HiveMind")
        logger.debug("Bootstrap mode: performing initial setup")
        
        try:
            logger.info("Cloning/pulling repository")
            self.git_repo.clone_or_pull()
            logger.info("Repository synced successfully")
        except Exception as e:
            logger.error(f"Failed to sync repository during bootstrap: {e}", exc_info=True)
            raise
        
        try:
            logger.info("Running initial reconciliation")
            self.reconcile()
        except Exception as e:
            logger.error(f"Failed to reconcile during bootstrap: {e}", exc_info=True)
            raise
        
        logger.info("Bootstrap complete")
        logger.info("=" * 60)
