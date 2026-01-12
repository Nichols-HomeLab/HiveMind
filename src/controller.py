"""HiveMind main controller"""

import time
import yaml
import logging
import tempfile
from pathlib import Path
from typing import List

from .git_manager import GitRepository, GitConfig
from .stack_manager import SwarmStackManager, StackConfig

logger = logging.getLogger('hivemind.controller')


class HiveMind:
    """Main HiveMind controller"""
    
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.work_dir = Path(tempfile.gettempdir()) / "hivemind"
        self.work_dir.mkdir(exist_ok=True)
        
        git_config = GitConfig(**self.config['git'])
        self.git_repo = GitRepository(git_config, str(self.work_dir))
        self.stack_manager = SwarmStackManager()
        self.running = False
    
    def _load_config(self) -> dict:
        """Load HiveMind configuration"""
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)
    
    def _load_stacks_config(self) -> List[StackConfig]:
        """Load stacks configuration from repository"""
        stacks_file = self.git_repo.get_file_path("stacks.yml")
        
        if not stacks_file.exists():
            logger.warning(f"stacks.yml not found at {stacks_file}")
            return []
        
        with open(stacks_file, 'r') as f:
            stacks_data = yaml.safe_load(f)
        
        stacks = []
        for stack_data in stacks_data.get('stacks', []):
            stacks.append(StackConfig(**stack_data))
        
        return stacks
    
    def reconcile(self):
        """Reconcile desired state with actual state"""
        logger.info("Starting reconciliation")
        
        has_changes = self.git_repo.clone_or_pull()
        
        if not has_changes and self.git_repo.current_commit:
            logger.info("No changes detected, skipping reconciliation")
            return
        
        stacks = self._load_stacks_config()
        
        if not stacks:
            logger.warning("No stacks configured")
            return
        
        enabled_stacks = set()
        for stack in stacks:
            if stack.enabled:
                enabled_stacks.add(stack.name)
                compose_path = self.git_repo.get_file_path(stack.compose_file)
                
                if not compose_path.exists():
                    logger.error(f"Compose file not found: {compose_path}")
                    continue
                
                env_file = None
                if stack.env_file:
                    env_file = self.git_repo.get_file_path(stack.env_file)
                
                self.stack_manager.deploy_stack(stack, compose_path, env_file)
        
        deployed_stacks = set(self.stack_manager.list_stacks())
        managed_stacks = set(s.name for s in stacks)
        
        for stack_name in deployed_stacks:
            if stack_name in managed_stacks and stack_name not in enabled_stacks:
                logger.info(f"Stack {stack_name} is disabled, removing")
                self.stack_manager.remove_stack(stack_name)
    
    def run(self):
        """Run the main reconciliation loop"""
        logger.info("HiveMind starting...")
        self.running = True
        
        poll_interval = self.config['git'].get('poll_interval', 60)
        
        try:
            while self.running:
                try:
                    self.reconcile()
                except Exception as e:
                    logger.error(f"Reconciliation error: {e}", exc_info=True)
                
                logger.info(f"Sleeping for {poll_interval} seconds")
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Shutting down HiveMind")
            self.running = False
    
    def bootstrap(self):
        """Bootstrap HiveMind - initial setup"""
        logger.info("Bootstrapping HiveMind")
        
        self.git_repo.clone_or_pull()
        self.reconcile()
        
        logger.info("Bootstrap complete")
