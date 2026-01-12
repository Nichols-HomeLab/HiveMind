"""Docker Swarm stack management"""

import os
import subprocess
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger('hivemind.stack')


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
        self.deployed_stacks: Dict[str, str] = {}
    
    def deploy_stack(self, stack: StackConfig, compose_path: Path, env_file: Optional[Path] = None) -> bool:
        """Deploy or update a Docker stack"""
        try:
            logger.info(f"Deploying stack: {stack.name}")
            
            compose_hash = self._calculate_stack_hash(compose_path, env_file)
            
            if stack.name in self.deployed_stacks:
                if self.deployed_stacks[stack.name] == compose_hash:
                    logger.info(f"Stack {stack.name} is up to date")
                    return True
            
            cmd = ["docker", "stack", "deploy", "-c", str(compose_path)]
            env = None
            
            if env_file and env_file.exists():
                logger.info(f"Loading environment from {env_file}")
                env = self._load_env_file(env_file)
            
            cmd.append(stack.name)
            
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                env=env
            )
            logger.info(f"Stack {stack.name} deployed successfully")
            logger.debug(result.stdout)
            
            self.deployed_stacks[stack.name] = compose_hash
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to deploy stack {stack.name}: {e}")
            logger.error(e.stderr)
            return False
    
    def remove_stack(self, stack_name: str) -> bool:
        """Remove a Docker stack"""
        try:
            logger.info(f"Removing stack: {stack_name}")
            subprocess.run(
                ["docker", "stack", "rm", stack_name],
                check=True,
                capture_output=True
            )
            if stack_name in self.deployed_stacks:
                del self.deployed_stacks[stack_name]
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to remove stack {stack_name}: {e}")
            return False
    
    def list_stacks(self) -> List[str]:
        """List currently deployed stacks"""
        try:
            result = subprocess.run(
                ["docker", "stack", "ls", "--format", "{{.Name}}"],
                check=True,
                capture_output=True,
                text=True
            )
            return [line.strip() for line in result.stdout.split('\n') if line.strip()]
        except subprocess.CalledProcessError:
            return []
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _calculate_stack_hash(self, compose_path: Path, env_file: Optional[Path]) -> str:
        """Calculate stack hash based on compose file and env file"""
        sha256_hash = hashlib.sha256()
        sha256_hash.update(self._calculate_file_hash(compose_path).encode("utf-8"))
        if env_file and env_file.exists():
            sha256_hash.update(self._calculate_file_hash(env_file).encode("utf-8"))
        return sha256_hash.hexdigest()

    def _load_env_file(self, env_file: Path) -> dict:
        """Load env vars from a file for docker stack deploy"""
        env = dict(**os.environ)
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key] = value
        return env
