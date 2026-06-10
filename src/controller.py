"""HiveMind main controller"""

import time
import yaml
import logging
import tempfile
from pathlib import Path
from typing import Dict, List

from .git_manager import GitRepository, GitConfig
from .stack_manager import SwarmStackManager, StackConfig
from .notifier import SMTPNotifier

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

        self.notifier = None
        smtp_cfg = (self.config.get("notifications") or {}).get("smtp")
        if smtp_cfg:
            logger.info("SMTP notifications enabled")
            self.notifier = SMTPNotifier(smtp_cfg)

        self.retired_stacks: List[str] = []
        
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

        self.retired_stacks = list(stacks_data.get("retired_stacks", []))
        if self.retired_stacks:
            logger.info(f"Loaded {len(self.retired_stacks)} retired stack name(s)")
        
        stacks = []
        for stack_data in stacks_data.get('stacks', []):
            try:
                if not stack_data.get("compose_file") and not stack_data.get("compose_files"):
                    raise ValueError("stack requires compose_file or compose_files")
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
        previous_commit = self.git_repo.current_commit
        
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
        obsolete_stacks = set()
        deployment_results: Dict[str, List[str]] = {
            "new": [],
            "updated": [],
            "unchanged": [],
            "failed": [],
            "skipped": [],
            "detail_lines": [],
        }
        logger.info(f"Processing {len(stacks)} stack(s)")
        
        for stack in stacks:
            logger.debug(f"Processing stack: {stack.name} (enabled={stack.enabled})")
            if stack.enabled:
                enabled_stacks.add(stack.name)
                compose_files = stack.compose_files or ([stack.compose_file] if stack.compose_file else [])
                compose_paths = [self.git_repo.get_file_path(compose_file) for compose_file in compose_files]
                logger.debug(f"Compose file paths for {stack.name}: {compose_paths}")

                missing_paths = [path for path in compose_paths if not path.exists()]
                if missing_paths:
                    logger.error(f"Compose file(s) not found for stack {stack.name}: {missing_paths}")
                    deployment_results["failed"].append(stack.name)
                    continue
                
                env_file = None
                if stack.env_file:
                    env_file = self.git_repo.get_file_path(stack.env_file)
                    logger.debug(f"Environment file for {stack.name}: {env_file}")
                    if not env_file.exists():
                        logger.warning(f"Environment file specified but not found: {env_file}")
                        env_file = None
                else:
                    default_env_file = self.git_repo.get_file_path(f"env/{stack.name}.env.sops")
                    if default_env_file.exists():
                        env_file = default_env_file
                        logger.debug(f"Using default SOPS environment file for {stack.name}: {env_file}")
                
                logger.info(f"Deploying stack: {stack.name}")
                try:
                    result = self.stack_manager.deploy_stack(stack, compose_paths, env_file)
                    deployment_results.setdefault(result.status, []).append(stack.name)
                    if result.image_changes:
                        deployment_results["detail_lines"].extend(result.image_changes)
                    if result.status != "failed":
                        obsolete_stacks.update(stack.replaces or [])
                except Exception as e:
                    logger.error(f"Failed to deploy stack {stack.name}: {e}", exc_info=True)
                    deployment_results["failed"].append(stack.name)
            else:
                logger.info(f"Stack {stack.name} is disabled, skipping deployment")
                deployment_results["skipped"].append(stack.name)
        
        logger.debug("Checking for stacks to remove")
        deployed_stacks = set(self.stack_manager.list_stacks())
        managed_stacks = set(s.name for s in stacks)
        obsolete_stacks.update(self.retired_stacks)
        logger.debug(f"Deployed stacks: {deployed_stacks}")
        logger.debug(f"Managed stacks: {managed_stacks}")
        logger.debug(f"Enabled stacks: {enabled_stacks}")
        logger.debug(f"Obsolete stacks: {obsolete_stacks}")
        
        for stack_name in deployed_stacks:
            should_remove_disabled = stack_name in managed_stacks and stack_name not in enabled_stacks
            should_remove_obsolete = stack_name in obsolete_stacks
            if should_remove_disabled or should_remove_obsolete:
                if should_remove_obsolete:
                    logger.info(f"Stack {stack_name} is obsolete, removing")
                else:
                    logger.info(f"Stack {stack_name} is disabled, removing")
                try:
                    self.stack_manager.remove_stack(stack_name)
                except Exception as e:
                    logger.error(f"Failed to remove stack {stack_name}: {e}", exc_info=True)
        
        logger.info("Reconciliation complete")
        logger.info("=" * 60)

        if has_changes:
            self._notify_update(previous_commit, self.git_repo.current_commit, deployment_results)

    def _notify_update(self, previous_commit, current_commit, deployment_results: Dict[str, List[str]]):
        if not self.notifier:
            return
        updated_count = len(deployment_results.get("updated", []))
        new_count = len(deployment_results.get("new", []))
        if new_count or updated_count:
            headline = "HiveMind applied stack upgrades."
            subject = "HiveMind upgrade applied"
        else:
            headline = "HiveMind synced repository changes. No stack upgrades were needed."
            subject = "HiveMind repository update"

        summary_lines = [headline, f"Current commit: {current_commit[:8] if current_commit else 'unknown'}"]
        if previous_commit:
            summary_lines.append(f"Previous commit: {previous_commit[:8]}")
        for label, key in (
            ("New stacks", "new"),
            ("Updated stacks", "updated"),
            ("Unchanged stacks", "unchanged"),
            ("Failed stacks", "failed"),
            ("Skipped disabled stacks", "skipped"),
        ):
            stack_names = deployment_results.get(key, [])
            if stack_names:
                summary_lines.append(f"{label}: {', '.join(stack_names)}")
        detail_lines = deployment_results.get("detail_lines", [])
        if detail_lines:
            summary_lines.append("")
            summary_lines.extend(detail_lines)
        body = "\n".join(summary_lines)
        self.notifier.send(subject, body)
    
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
