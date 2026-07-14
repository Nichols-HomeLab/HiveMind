"""Docker Swarm stack management"""

import os
import subprocess
import hashlib
import base64
import json
import logging
import time
import yaml
import codecs
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("hivemind.stack")

STACK_HASH_LABEL = "io.nicholstech.hivemind.stack-hash"
STATE_LABEL = "io.nicholstech.hivemind.state"
STATE_STACK_LABEL = "io.nicholstech.hivemind.stack"
STATE_CONFIG_PREFIX = "hivemind-state"


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


@dataclass(frozen=True)
class PersistedStackState:
    """Deployment state reconstructed from a Docker Swarm config."""

    status: str
    service_names: List[str] = field(default_factory=list)
    stack_hash: Optional[str] = None
    service_images: Dict[str, str] = field(default_factory=dict)
    detail: Optional[str] = None


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

            if stack.name not in self.deployed_stacks:
                persisted = self._discover_persisted_stack_state(stack.name)
                if persisted.status == "tracked":
                    self.deployed_stacks[stack.name] = persisted.stack_hash or ""
                    self.deployed_service_images[stack.name] = persisted.service_images
                    logger.info(
                        "Restored deployment state for stack %s from a Swarm config",
                        stack.name,
                    )
                elif persisted.status == "untracked":
                    logger.warning(
                        "Adopting existing unlabeled stack %s without redeploying it",
                        stack.name,
                    )
                    if not self._persist_stack_state(stack.name, compose_hash, service_images):
                        return DeployResult(
                            status="failed",
                            detail=f"Failed to persist deployment state for existing stack {stack.name}",
                        )
                    self.deployed_stacks[stack.name] = compose_hash
                    self.deployed_service_images[stack.name] = service_images
                    return DeployResult(status="unchanged", detail="adopted existing stack")
                elif persisted.status in {"error", "inconsistent"}:
                    logger.error(
                        "Refusing to deploy stack %s because persisted state is %s: %s",
                        stack.name,
                        persisted.status,
                        persisted.detail,
                    )
                    return DeployResult(status="failed", detail=persisted.detail)

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

            env = None
            rendered_compose: Optional[Path] = None
            
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

            try:
                rendered_compose = self._render_compose_file(stack.name, compose_paths, env)
                cmd = ["docker", "stack", "deploy", "--compose-file", str(rendered_compose), stack.name]
                logger.debug(f"Executing docker command: docker stack deploy ... {stack.name}")

                result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
                logger.info(f"Stack {stack.name} deployed successfully")
                if result.stdout:
                    logger.debug(f"Docker output: {result.stdout}")
                if result.stderr:
                    logger.debug(f"Docker stderr: {result.stderr}")
            finally:
                if rendered_compose and rendered_compose.exists():
                    rendered_compose.unlink()

            if not self._persist_stack_state(stack.name, compose_hash, service_images):
                return DeployResult(
                    status="failed",
                    detail=f"Stack {stack.name} deployed but its reconciliation state was not persisted",
                )

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
            self._remove_persisted_stack_state(stack_name)
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
                env_content = self._read_env_content(env_file)
                env_hash = hashlib.sha256(env_content.encode("utf-8")).hexdigest()
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
            env_content = self._read_env_content(env_file)
            
            loaded_vars = 0
            for line_num, line in enumerate(env_content.splitlines(), 1):
                parsed = self._parse_env_line(line, line_num)
                if parsed is None:
                    continue

                key, value = parsed
                env[key] = value
                loaded_vars += 1
                logger.debug(f"Line {line_num}: loaded variable '{key}'")
            
            logger.info(f"Loaded {loaded_vars} environment variable(s) from {env_file}")
            return env
        except Exception as e:
            logger.error(f"Failed to load environment file {env_file}: {e}", exc_info=True)
            raise

    def _read_env_content(self, env_file: Path) -> str:
        """Read plaintext or SOPS-encrypted dotenv content."""
        if self._is_sops_env_file(env_file):
            logger.info("Decrypting SOPS environment file: %s", env_file)
            result = subprocess.run(
                [
                    "sops",
                    "--decrypt",
                    "--input-type",
                    "dotenv",
                    "--output-type",
                    "dotenv",
                    str(env_file),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout

        with open(env_file, "r") as f:
            return f.read()

    def _is_sops_env_file(self, env_file: Path) -> bool:
        """Return true when an env file should be decrypted with SOPS."""
        return env_file.name.endswith(".sops") or ".sops." in env_file.name

    def _parse_env_line(self, line: str, line_num: int) -> Optional[tuple[str, str]]:
        """Parse one Docker-compatible dotenv line."""
        line = line.strip()
        if not line:
            logger.debug(f"Line {line_num}: empty, skipping")
            return None
        if line.startswith("#"):
            logger.debug(f"Line {line_num}: comment, skipping")
            return None
        if "=" not in line:
            logger.warning(f"Line {line_num}: invalid format (no '='), skipping: {line}")
            return None

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            logger.warning(f"Line {line_num}: invalid format (empty key), skipping")
            return None

        return key, self._parse_env_value(value.strip())

    def _parse_env_value(self, value: str) -> str:
        """Parse dotenv value quoting emitted by SOPS and accepted by Docker Compose."""
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                return codecs.decode(value, "unicode_escape")
            return value
        return value

    def _render_compose_file(
        self,
        stack_name: str,
        compose_paths: List[Path],
        env: Optional[dict],
    ) -> Path:
        """Render Compose interpolation and normalize output for Swarm."""
        cmd = ["docker", "compose"]
        for compose_path in compose_paths:
            cmd.extend(["--file", str(compose_path)])

        subprocess.run(
            [*cmd, "config", "--quiet"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        result = subprocess.run(
            [*cmd, "config"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

        rendered = self._normalize_compose_data(yaml.safe_load(result.stdout) or {})

        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=f".{stack_name}.compose.yml",
            prefix="hivemind.",
            delete=False,
        )
        with handle:
            yaml.safe_dump(rendered, handle, sort_keys=False)
        return Path(handle.name)

    def _discover_persisted_stack_state(self, stack_name: str) -> PersistedStackState:
        """Read a stack's reconciliation state from a Swarm config object."""
        try:
            stacks_result = subprocess.run(
                ["docker", "stack", "ls", "--format", "{{.Name}}"],
                check=True,
                capture_output=True,
                text=True,
            )
            stacks = {line.strip() for line in stacks_result.stdout.splitlines() if line.strip()}
            if stack_name not in stacks:
                return PersistedStackState(status="absent")

            services_result = subprocess.run(
                ["docker", "stack", "services", stack_name, "--format", "{{.Name}}"],
                check=True,
                capture_output=True,
                text=True,
            )
            service_names = [
                line.strip() for line in services_result.stdout.splitlines() if line.strip()
            ]
            if not service_names:
                return PersistedStackState(
                    status="error",
                    detail=f"Existing stack {stack_name} has no discoverable services",
                )

            configs_result = subprocess.run(
                [
                    "docker",
                    "config",
                    "ls",
                    "--filter",
                    f"label={STATE_LABEL}=true",
                    "--filter",
                    f"label={STATE_STACK_LABEL}={stack_name}",
                    "--format",
                    "{{.Name}}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            config_names = [
                line.strip() for line in configs_result.stdout.splitlines() if line.strip()
            ]

            if not config_names:
                inspect_result = subprocess.run(
                    [
                        "docker",
                        "service",
                        "inspect",
                        "--format",
                        "{{json .Spec}}",
                        *service_names,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                specs = [json.loads(line) for line in inspect_result.stdout.splitlines() if line]
                if len(specs) != len(service_names):
                    return PersistedStackState(
                        status="error",
                        service_names=service_names,
                        detail=(
                            f"Expected {len(service_names)} service specs for {stack_name}, "
                            f"received {len(specs)}"
                        ),
                    )

                images: Dict[str, str] = {}
                prefix = f"{stack_name}_"
                for service_name, spec in zip(service_names, specs):
                    short_name = (
                        service_name[len(prefix):]
                        if service_name.startswith(prefix)
                        else service_name
                    )
                    image = (
                        spec.get("TaskTemplate", {})
                        .get("ContainerSpec", {})
                        .get("Image")
                    )
                    if image:
                        images[short_name] = image
                return PersistedStackState(
                    status="untracked",
                    service_names=service_names,
                    service_images=images,
                )

            inspect_result = subprocess.run(
                ["docker", "config", "inspect", *config_names],
                check=True,
                capture_output=True,
                text=True,
            )
            configs = json.loads(inspect_result.stdout)
            if not configs:
                return PersistedStackState(
                    status="error",
                    service_names=service_names,
                    detail=f"State configs for {stack_name} disappeared during inspection",
                )

            # Immutable configs make state updates atomic. If cleanup of an old
            # config failed, the newest successfully-created config wins.
            configs.sort(key=lambda item: item.get("CreatedAt", ""), reverse=True)
            spec = configs[0].get("Spec") or {}
            labels = spec.get("Labels") or {}
            stack_hash = labels.get(STACK_HASH_LABEL)
            if not stack_hash:
                return PersistedStackState(
                    status="inconsistent",
                    service_names=service_names,
                    detail=f"Newest state config for {stack_name} has no stack hash",
                )
            raw_data = base64.b64decode(spec.get("Data") or "").decode("utf-8")
            payload = json.loads(raw_data)
            images = payload.get("service_images") or {}
            if not isinstance(images, dict):
                raise ValueError(f"Invalid service_images in state config for {stack_name}")
            return PersistedStackState(
                status="tracked",
                service_names=service_names,
                stack_hash=stack_hash,
                service_images=images,
            )
        except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to discover persisted state for stack %s: %s", stack_name, exc)
            return PersistedStackState(status="error", detail=str(exc))
        except Exception as exc:
            logger.error(
                "Unexpected error discovering persisted state for stack %s: %s",
                stack_name,
                exc,
                exc_info=True,
            )
            return PersistedStackState(status="error", detail=str(exc))

    def _persist_stack_state(
        self,
        stack_name: str,
        stack_hash: str,
        service_images: Dict[str, str],
    ) -> bool:
        """Persist state in Swarm Raft without updating any service specs."""
        try:
            existing_result = subprocess.run(
                [
                    "docker",
                    "config",
                    "ls",
                    "--filter",
                    f"label={STATE_LABEL}=true",
                    "--filter",
                    f"label={STATE_STACK_LABEL}={stack_name}",
                    "--format",
                    "{{.Name}}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            existing = [
                line.strip() for line in existing_result.stdout.splitlines() if line.strip()
            ]
            config_name = (
                f"{STATE_CONFIG_PREFIX}-{stack_name}-{stack_hash[:12]}-{time.time_ns()}"
            )
            payload = json.dumps(
                {"version": 1, "service_images": service_images},
                sort_keys=True,
                separators=(",", ":"),
            )
            subprocess.run(
                [
                    "docker",
                    "config",
                    "create",
                    "--label",
                    f"{STATE_LABEL}=true",
                    "--label",
                    f"{STATE_STACK_LABEL}={stack_name}",
                    "--label",
                    f"{STACK_HASH_LABEL}={stack_hash}",
                    config_name,
                    "-",
                ],
                input=payload,
                check=True,
                capture_output=True,
                text=True,
            )
            for old_config in existing:
                cleanup = subprocess.run(
                    ["docker", "config", "rm", old_config],
                    capture_output=True,
                    text=True,
                )
                if cleanup.returncode != 0:
                    logger.warning(
                        "Created new state for %s but could not remove stale config %s: %s",
                        stack_name,
                        old_config,
                        cleanup.stderr.strip(),
                    )
            return True
        except subprocess.CalledProcessError as exc:
            logger.error(
                "Failed to persist deployment state for stack %s: %s",
                stack_name,
                exc.stderr or exc,
            )
            return False

    def _remove_persisted_stack_state(self, stack_name: str) -> None:
        """Remove all persisted state configs for a deleted stack."""
        try:
            result = subprocess.run(
                [
                    "docker",
                    "config",
                    "ls",
                    "--filter",
                    f"label={STATE_LABEL}=true",
                    "--filter",
                    f"label={STATE_STACK_LABEL}={stack_name}",
                    "--format",
                    "{{.Name}}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            configs = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if configs:
                subprocess.run(
                    ["docker", "config", "rm", *configs],
                    check=True,
                    capture_output=True,
                    text=True,
                )
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Stack %s was removed but its reconciliation state could not be cleaned up: %s",
                stack_name,
                exc.stderr or exc,
            )
        except Exception as exc:
            logger.warning(
                "Stack %s was removed but its reconciliation state cleanup failed: %s",
                stack_name,
                exc,
            )

    def _normalize_compose_data(self, data: dict) -> dict:
        """Remove or coerce fields emitted by Compose that Swarm rejects."""
        if not isinstance(data, dict):
            return {}

        data.pop("name", None)

        services = data.get("services") or {}
        if isinstance(services, dict):
            for service in services.values():
                if not isinstance(service, dict):
                    continue
                service.pop("group_add", None)
                service.pop("depends_on", None)
                for port in service.get("ports", []) or []:
                    if not isinstance(port, dict):
                        continue
                    for field in ("target", "published"):
                        value = port.get(field)
                        if isinstance(value, str) and value.isdigit():
                            port[field] = int(value)
                for mount in (service.get("secrets", []) or []) + (service.get("configs", []) or []):
                    if not isinstance(mount, dict):
                        continue
                    mode = mount.get("mode")
                    if isinstance(mode, str) and mode.isdigit():
                        mount["mode"] = int(mode, 8 if mode.startswith("0") else 10)

        return data

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
