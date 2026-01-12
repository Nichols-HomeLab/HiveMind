"""Docker Swarm stack management"""

import os
import subprocess
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger("hivemind.stack")


@dataclass
class StackConfig:
    """Docker stack configuration"""
    name: str
    compose_file: str
    enabled: bool = True
    env_file: Optional[str] = None


class SwarmStackManager:
    """Manages Docker Swarm stacks"""
    
    def __init__(self):
        logger.debug("Initializing SwarmStackManager")
        self.deployed_stacks: Dict[str, str] = {}
        logger.info("SwarmStackManager initialized")
    
    def deploy_stack(self, stack: StackConfig, compose_path: Path, env_file: Optional[Path] = None) -> bool:
        """Deploy or update a Docker stack"""
        logger.info(f"Starting deployment for stack: {stack.name}")
        logger.debug(f"Compose file: {compose_path}")
        logger.debug(f"Environment file: {env_file}")
        
        try:
            logger.debug(f"Calculating hash for stack {stack.name}")
            compose_hash = self._calculate_stack_hash(compose_path, env_file)
            logger.debug(f"Stack hash: {compose_hash[:16]}...")

            if stack.name in self.deployed_stacks:
                previous_hash = self.deployed_stacks[stack.name]
                logger.debug(f"Previous hash: {previous_hash[:16]}...")
                if previous_hash == compose_hash:
                    logger.info(f"Stack {stack.name} is up to date (hash match)")
                    return True
                else:
                    logger.info(f"Stack {stack.name} has changes, updating")
            else:
                logger.info(f"Stack {stack.name} is new, deploying")

            cmd = ["docker", "stack", "deploy", "-c", str(compose_path)]
            env = None
            
            if env_file and env_file.exists():
                logger.info(f"Loading environment from {env_file}")
                try:
                    env = self._load_env_file(env_file)
                    logger.debug(f"Loaded {len(env)} environment variables")
                except Exception as e:
                    logger.error(f"Failed to load environment file: {e}", exc_info=True)
                    logger.warning("Continuing deployment without environment file")
            elif env_file:
                logger.warning(f"Environment file specified but not found: {env_file}")
            
            cmd.append(stack.name)
            logger.debug(f"Executing docker command: {' '.join(cmd[:4])} ... {stack.name}")
            
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
            logger.info(f"Stack {stack.name} deployed successfully")
            if result.stdout:
                logger.debug(f"Docker output: {result.stdout}")
            if result.stderr:
                logger.debug(f"Docker stderr: {result.stderr}")

            self.deployed_stacks[stack.name] = compose_hash
            logger.debug(f"Updated deployed stacks cache for {stack.name}")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to deploy stack {stack.name} (exit code {e.returncode}): {e}")
            if e.stderr:
                logger.error(f"Docker error output: {e.stderr}")
            if e.stdout:
                logger.debug(f"Docker stdout: {e.stdout}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deploying stack {stack.name}: {e}", exc_info=True)
            return False
    
    def remove_stack(self, stack_name: str) -> bool:
        """Remove a Docker stack"""
        logger.info(f"Removing stack: {stack_name}")
        logger.debug(f"Executing: docker stack rm {stack_name}")
        
        try:
            result = subprocess.run(
                ["docker", "stack", "rm", stack_name], check=True, capture_output=True, text=True
            )
            logger.info(f"Stack {stack_name} removed successfully")
            if result.stdout:
                logger.debug(f"Docker output: {result.stdout}")
            if result.stderr:
                logger.debug(f"Docker stderr: {result.stderr}")
            
            if stack_name in self.deployed_stacks:
                del self.deployed_stacks[stack_name]
                logger.debug(f"Removed {stack_name} from deployed stacks cache")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to remove stack {stack_name} (exit code {e.returncode}): {e}")
            if e.stderr:
                logger.error(f"Docker error: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error removing stack {stack_name}: {e}", exc_info=True)
            return False
    
    def list_stacks(self) -> List[str]:
        """List currently deployed stacks"""
        logger.debug("Listing deployed Docker stacks")
        try:
            result = subprocess.run(
                ["docker", "stack", "ls", "--format", "{{.Name}}"], check=True, capture_output=True, text=True
            )
            stacks = [line.strip() for line in result.stdout.split("\n") if line.strip()]
            logger.debug(f"Found {len(stacks)} deployed stack(s): {stacks}")
            return stacks
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to list stacks (exit code {e.returncode}): {e}")
            if e.stderr:
                logger.error(f"Docker error: {e.stderr}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error listing stacks: {e}", exc_info=True)
            return []
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file"""
        logger.debug(f"Calculating hash for file: {file_path}")
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            file_hash = sha256_hash.hexdigest()
            logger.debug(f"File hash: {file_hash[:16]}...")
            return file_hash
        except Exception as e:
            logger.error(f"Failed to calculate hash for {file_path}: {e}", exc_info=True)
            raise

    def _calculate_stack_hash(self, compose_path: Path, env_file: Optional[Path]) -> str:
        """Calculate stack hash based on compose file and env file"""
        logger.debug("Calculating combined stack hash")
        try:
            sha256_hash = hashlib.sha256()
            compose_hash = self._calculate_file_hash(compose_path)
            sha256_hash.update(compose_hash.encode("utf-8"))
            logger.debug("Compose file hash added to stack hash")
            
            if env_file and env_file.exists():
                env_hash = self._calculate_file_hash(env_file)
                sha256_hash.update(env_hash.encode("utf-8"))
                logger.debug("Environment file hash added to stack hash")
            elif env_file:
                logger.debug(f"Environment file not found: {env_file}")
            
            stack_hash = sha256_hash.hexdigest()
            logger.debug(f"Combined stack hash: {stack_hash[:16]}...")
            return stack_hash
        except Exception as e:
            logger.error(f"Failed to calculate stack hash: {e}", exc_info=True)
            raise

    def _load_env_file(self, env_file: Path) -> dict:
        """Load env vars from a file for docker stack deploy"""
        logger.debug(f"Loading environment variables from {env_file}")
        try:
            env = dict(**os.environ)
            logger.debug(f"Starting with {len(env)} system environment variables")
            
            loaded_vars = 0
            with open(env_file, "r") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        logger.debug(f"Line {line_num}: empty, skipping")
                        continue
                    if line.startswith("#"):
                        logger.debug(f"Line {line_num}: comment, skipping")
                        continue
                    if "=" not in line:
                        logger.warning(f"Line {line_num}: invalid format (no '='), skipping: {line}")
                        continue
                    
                    key, value = line.split("=", 1)
                    env[key] = value
                    loaded_vars += 1
                    logger.debug(f"Line {line_num}: loaded variable '{key}'")
            
            logger.info(f"Loaded {loaded_vars} environment variable(s) from {env_file}")
            return env
        except Exception as e:
            logger.error(f"Failed to load environment file {env_file}: {e}", exc_info=True)
            raise
