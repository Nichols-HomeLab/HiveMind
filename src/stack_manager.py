"""Docker Swarm stack management"""

import os
import subprocess
import hashlib
import logging
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("hivemind.stack")


@dataclass
class StackConfig:
    """Docker stack configuration"""
    name: str
    compose_file: Optional[str] = None
    compose_files: Optional[List[str]] = None
    enabled: bool = True
    env_file: Optional[str] = None
    replaces: Optional[List[str]] = None


@dataclass(frozen=True)
class DeployResult:
    """Outcome of a stack deployment attempt"""
    status: str
    detail: Optional[str] = None
    image_changes: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.status in {"new", "updated"}


class SwarmStackManager:
    """Manages Docker Swarm stacks"""
    
    def __init__(self):
        logger.debug("Initializing SwarmStackManager")
        self.deployed_stacks: Dict[str, str] = {}
        self.deployed_service_images: Dict[str, Dict[str, str]] = {}
        logger.info("SwarmStackManager initialized")
    
    def deploy_stack(
        self,
        stack: StackConfig,
        compose_paths: List[Path],
        env_file: Optional[Path] = None,
    ) -> DeployResult:
        """Deploy or update a Docker stack"""
        logger.info(f"Starting deployment for stack: {stack.name}")
        logger.debug(f"Compose files: {compose_paths}")
        logger.debug(f"Environment file: {env_file}")
        
        try:
            service_images = self._extract_service_images(compose_paths)
            logger.debug(f"Calculating hash for stack {stack.name}")
            compose_hash = self._calculate_stack_hash(compose_paths, env_file)
            logger.debug(f"Stack hash: {compose_hash[:16]}...")
            status = "new"
            image_changes: List[str] = []

            if stack.name in self.deployed_stacks:
                previous_hash = self.deployed_stacks[stack.name]
                previous_images = self.deployed_service_images.get(stack.name, {})
                logger.debug(f"Previous hash: {previous_hash[:16]}...")
                if previous_hash == compose_hash:
                    logger.info(f"Stack {stack.name} is up to date (hash match)")
                    return DeployResult(status="unchanged")
                logger.info(f"Stack {stack.name} has changes, updating")
                status = "updated"
                image_changes = self._describe_image_changes(stack.name, previous_images, service_images)
            else:
                logger.info(f"Stack {stack.name} is new, deploying")
                image_changes = self._describe_new_services(stack.name, service_images)

            cmd = ["docker", "stack", "deploy"]
            for compose_path in compose_paths:
                cmd.extend(["-c", str(compose_path)])
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
            self.deployed_service_images[stack.name] = service_images
            logger.debug(f"Updated deployed stacks cache for {stack.name}")
            return DeployResult(status=status, image_changes=image_changes)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to deploy stack {stack.name} (exit code {e.returncode}): {e}")
            if e.stderr:
                logger.error(f"Docker error output: {e.stderr}")
            if e.stdout:
                logger.debug(f"Docker stdout: {e.stdout}")
            return DeployResult(status="failed", detail=str(e))
        except Exception as e:
            logger.error(f"Unexpected error deploying stack {stack.name}: {e}", exc_info=True)
            return DeployResult(status="failed", detail=str(e))
    
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
            if stack_name in self.deployed_service_images:
                del self.deployed_service_images[stack_name]
                logger.debug(f"Removed {stack_name} service image cache")
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

    def _calculate_stack_hash(self, compose_paths: List[Path], env_file: Optional[Path]) -> str:
        """Calculate stack hash based on compose files and env file"""
        logger.debug("Calculating combined stack hash")
        try:
            sha256_hash = hashlib.sha256()
            for compose_path in compose_paths:
                compose_hash = self._calculate_file_hash(compose_path)
                sha256_hash.update(compose_hash.encode("utf-8"))
                logger.debug(f"Compose file hash added to stack hash: {compose_path}")
            
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

    def _extract_service_images(self, compose_paths: List[Path]) -> Dict[str, str]:
        """Extract final image values for services across compose files."""
        service_images: Dict[str, str] = {}

        for compose_path in compose_paths:
            logger.debug(f"Loading compose services from {compose_path}")
            with open(compose_path, "r") as compose_file:
                compose_data = yaml.safe_load(compose_file) or {}
            if not isinstance(compose_data, dict):
                continue

            services = compose_data.get("services") or {}
            if not isinstance(services, dict):
                continue
            for service_name, service_config in services.items():
                if not isinstance(service_config, dict):
                    continue
                image = service_config.get("image")
                if isinstance(image, str) and image:
                    service_images[service_name] = image

        logger.debug("Extracted %d service image(s)", len(service_images))
        return service_images

    def _describe_image_changes(
        self,
        stack_name: str,
        previous_images: Dict[str, str],
        current_images: Dict[str, str],
    ) -> List[str]:
        """Summarize image updates or new services within an existing stack."""
        changes: List[str] = []

        for service_name, current_image in sorted(current_images.items()):
            previous_image = previous_images.get(service_name)
            if previous_image is None:
                changes.append(f"Created {stack_name} - {service_name} image: {current_image}")
            elif previous_image != current_image:
                changes.append(
                    f"Updated {stack_name} - {service_name} image: {previous_image} -> {current_image}"
                )

        return changes

    def _describe_new_services(self, stack_name: str, service_images: Dict[str, str]) -> List[str]:
        """Summarize services created as part of a new stack."""
        return [
            f"Created {stack_name} - {service_name} image: {image}"
            for service_name, image in sorted(service_images.items())
        ]
