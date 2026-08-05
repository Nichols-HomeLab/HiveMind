"""Tests for stack_manager module"""

import base64
import json
import pytest
import yaml
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
from src.stack_manager import (
    DeployResult,
    PersistedStackState,
    STACK_HASH_LABEL,
    STATE_LABEL,
    STATE_STACK_LABEL,
    SwarmStackManager,
    StackConfig,
)


@pytest.fixture
def stack_manager():
    """Create a test stack manager instance"""
    return SwarmStackManager()


@pytest.fixture
def stack_config():
    """Create a test stack configuration"""
    return StackConfig(
        name="test-stack",
        compose_file="docker-compose.yml",
        enabled=True,
        env_file=".env"
    )


def configure_compose_run(mock_run, compose_file):
    """Return realistic output for Compose rendering and successful Docker calls."""
    def run(command, *args, **kwargs):
        if command[:2] == ["docker", "compose"]:
            stdout = "" if command[-1] == "--quiet" else compose_file.read_text()
            return Mock(stdout=stdout, stderr="", returncode=0)
        if command[:3] == ["docker", "stack", "ls"]:
            return Mock(stdout="", stderr="", returncode=0)
        if command[:3] == ["docker", "config", "ls"]:
            return Mock(stdout="", stderr="", returncode=0)
        return Mock(stdout="Stack deployed", stderr="", returncode=0)

    mock_run.side_effect = run


def test_stack_config_creation():
    """Test StackConfig dataclass creation"""
    config = StackConfig(name="test", compose_file="compose.yml")
    assert config.name == "test"
    assert config.compose_file == "compose.yml"
    assert config.enabled is True
    assert config.env_file is None


def test_stack_manager_initialization(stack_manager):
    """Test SwarmStackManager initialization"""
    assert isinstance(stack_manager.deployed_stacks, dict)
    assert isinstance(stack_manager.deployed_service_images, dict)
    assert len(stack_manager.deployed_stacks) == 0


@patch('subprocess.run')
def test_list_stacks_success(mock_run, stack_manager):
    """Test listing deployed stacks"""
    mock_run.return_value = Mock(stdout="stack1\nstack2\nstack3\n", returncode=0)
    stacks = stack_manager.list_stacks()
    assert stacks == ["stack1", "stack2", "stack3"]


@patch('subprocess.run')
def test_list_stacks_empty(mock_run, stack_manager):
    """Test listing stacks when none are deployed"""
    mock_run.return_value = Mock(stdout="", returncode=0)
    stacks = stack_manager.list_stacks()
    assert stacks == []


@patch('subprocess.run')
def test_list_stacks_error(mock_run, stack_manager):
    """Test error handling when listing stacks"""
    mock_run.side_effect = Exception("Docker error")
    stacks = stack_manager.list_stacks()
    assert stacks == []


@patch('subprocess.run')
def test_remove_stack_success(mock_run, stack_manager):
    """Test successful stack removal"""
    stack_manager.deployed_stacks["test-stack"] = "hash123"
    mock_run.return_value = Mock(returncode=0)
    result = stack_manager.remove_stack("test-stack")
    assert result is True
    assert "test-stack" not in stack_manager.deployed_stacks


@patch('subprocess.run')
def test_remove_stack_error(mock_run, stack_manager):
    """Test error handling during stack removal"""
    mock_run.side_effect = Exception("Docker error")
    result = stack_manager.remove_stack("test-stack")
    assert result is False


def test_calculate_file_hash(stack_manager, tmp_path):
    """Test file hash calculation"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")
    hash_value = stack_manager._calculate_file_hash(test_file)
    assert isinstance(hash_value, str)
    assert len(hash_value) == 64


def test_calculate_stack_hash_without_env(stack_manager, tmp_path):
    """Test stack hash calculation without env file"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    hash_value = stack_manager._calculate_stack_hash([compose_file], None)
    assert isinstance(hash_value, str)
    assert len(hash_value) == 64


def test_calculate_stack_hash_with_env(stack_manager, tmp_path):
    """Test stack hash calculation with env file"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    env_file = tmp_path / ".env"
    env_file.write_text("VAR=value")
    hash_value = stack_manager._calculate_stack_hash([compose_file], env_file)
    assert isinstance(hash_value, str)
    assert len(hash_value) == 64


@patch('subprocess.run')
def test_calculate_stack_hash_uses_decrypted_sops_env(mock_run, stack_manager, tmp_path):
    """Test stack hash changes when decrypted SOPS env content changes"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    env_file = tmp_path / "stack.env.sops"
    env_file.write_text("encrypted payload")

    mock_run.side_effect = [
        Mock(stdout="SECRET=old\n", returncode=0),
        Mock(stdout="SECRET=new\n", returncode=0),
    ]

    old_hash = stack_manager._calculate_stack_hash([compose_file], env_file)
    new_hash = stack_manager._calculate_stack_hash([compose_file], env_file)

    assert old_hash != new_hash
    assert mock_run.call_args_list[0].args[0][:2] == ["sops", "--decrypt"]


def test_load_env_file(stack_manager, tmp_path):
    """Test loading environment variables from file"""
    env_file = tmp_path / ".env"
    env_file.write_text("VAR1=value1\nVAR2=value2\n# Comment\nVAR3=value3")
    env = stack_manager._load_env_file(env_file)
    assert "VAR1" in env
    assert env["VAR1"] == "value1"
    assert "VAR2" in env
    assert env["VAR2"] == "value2"
    assert "VAR3" in env
    assert env["VAR3"] == "value3"


def test_load_env_file_parses_docker_dotenv_quotes(stack_manager, tmp_path):
    """Test Docker-style env parsing strips wrapping quotes and handles escapes"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PLAIN=value\n"
        "SINGLE='quoted value'\n"
        'DOUBLE="quoted \\"value\\""\n'
        "EMPTY=\n"
    )

    env = stack_manager._load_env_file(env_file)

    assert env["PLAIN"] == "value"
    assert env["SINGLE"] == "quoted value"
    assert env["DOUBLE"] == 'quoted "value"'
    assert env["EMPTY"] == ""


@patch('subprocess.run')
def test_load_sops_env_file_decrypts_before_parsing(mock_run, stack_manager, tmp_path):
    """Test loading SOPS env files decrypts dotenv content before parsing"""
    env_file = tmp_path / "stack.env.sops"
    env_file.write_text("encrypted payload")
    mock_run.return_value = Mock(
        stdout="SECRET='decrypted value'\nTOKEN=abc123\n",
        returncode=0,
    )

    env = stack_manager._load_env_file(env_file)

    assert env["SECRET"] == "decrypted value"
    assert env["TOKEN"] == "abc123"
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == [
        "sops",
        "--decrypt",
        "--input-type",
        "dotenv",
        "--output-type",
        "dotenv",
        str(env_file),
    ]


@patch('subprocess.run')
def test_deploy_stack_success(mock_run, stack_manager, stack_config, tmp_path):
    """Test successful stack deployment"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "version: '3'\nservices:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:latest\n"
    )
    configure_compose_run(mock_run, compose_file)
    result = stack_manager.deploy_stack(stack_config, [compose_file])
    assert result.status == "new"
    assert result.image_changes == [
        "Created test-stack - bazarr image: lscr.io/linuxserver/bazarr:latest"
    ]
    assert stack_config.name in stack_manager.deployed_stacks
    assert stack_manager.deployed_service_images[stack_config.name] == {
        "bazarr": "lscr.io/linuxserver/bazarr:latest"
    }


@patch('subprocess.run')
def test_deploy_stack_up_to_date(mock_run, stack_manager, stack_config, tmp_path):
    """Test deploying stack that is already up to date"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "version: '3'\nservices:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:latest\n"
    )
    hash_value = stack_manager._calculate_stack_hash([compose_file], None)
    stack_manager.deployed_stacks[stack_config.name] = hash_value
    stack_manager.deployed_service_images[stack_config.name] = {
        "bazarr": "lscr.io/linuxserver/bazarr:latest"
    }
    stack_manager.deployed_service_hashes[stack_config.name] = {"bazarr": "service-hash"}
    result = stack_manager.deploy_stack(stack_config, [compose_file])
    assert result == DeployResult(status="unchanged")
    mock_run.assert_not_called()


@patch('subprocess.run')
def test_deploy_stack_with_env_file(mock_run, stack_manager, stack_config, tmp_path):
    """Test stack deployment with environment file"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'\nservices:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:latest\n")
    env_file = tmp_path / ".env"
    env_file.write_text("VAR=value")
    configure_compose_run(mock_run, compose_file)
    result = stack_manager.deploy_stack(stack_config, [compose_file], env_file)
    assert result.status == "new"


@patch('subprocess.run')
def test_deploy_stack_with_custom_command(mock_run, stack_manager, tmp_path):
    """Custom deploy commands run from the repository without a parallel stack deploy."""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  db:\n    image: mariadb:latest\n")
    stack_config = StackConfig(
        name="databases",
        compose_file="compose.yml",
        deploy_command=["scripts/deploy-stack.sh", "databases"],
    )
    mock_run.return_value = Mock(stdout="Database stack deployed safely", returncode=0)

    result = stack_manager.deploy_stack(
        stack_config,
        [compose_file],
        working_directory=tmp_path,
    )

    assert result.status == "new"
    custom_call = next(
        call
        for call in mock_run.call_args_list
        if call.args[0] == ["scripts/deploy-stack.sh", "databases"]
    )
    assert custom_call.kwargs["cwd"] == tmp_path
    assert not any(
        call.args[0][:3] == ["docker", "stack", "deploy"]
        for call in mock_run.call_args_list
    )


def test_custom_command_changes_stack_hash(stack_manager, tmp_path):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  db:\n    image: mariadb:latest\n")

    default_hash = stack_manager._calculate_stack_hash([compose_file], None)
    guarded_hash = stack_manager._calculate_stack_hash(
        [compose_file],
        None,
        ["scripts/deploy-stack.sh", "databases"],
    )

    assert guarded_hash != default_hash


@patch('subprocess.run')
def test_deploy_stack_with_sops_env_file(mock_run, stack_manager, stack_config, tmp_path):
    """Test stack deployment with SOPS encrypted environment file"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:latest\n")
    env_file = tmp_path / "stack.env.sops"
    env_file.write_text("encrypted payload")
    mock_run.side_effect = [
        Mock(stdout="VAR=decrypted\n", returncode=0),
        Mock(stdout="", returncode=0),
        Mock(stdout="VAR=decrypted\n", returncode=0),
        Mock(stdout="", returncode=0),
        Mock(stdout="services:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:latest\n", returncode=0),
        Mock(stdout="Stack deployed", returncode=0),
        Mock(stdout="", returncode=0),
        Mock(stdout="state-id\n", returncode=0),
    ]

    result = stack_manager.deploy_stack(stack_config, [compose_file], env_file)

    assert result.status == "new"
    deploy_call = next(
        call
        for call in mock_run.call_args_list
        if call.args[0][:3] == ["docker", "stack", "deploy"]
    )
    assert deploy_call.kwargs["env"]["VAR"] == "decrypted"
    assert deploy_call.args[0][:4] == ["docker", "stack", "deploy", "--compose-file"]


@patch('subprocess.run')
def test_deploy_stack_updated(mock_run, stack_manager, stack_config, tmp_path):
    """Test stack deployment when an existing stack has changed"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "version: '3.8'\nservices:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:1.0.0\n"
    )
    stack_manager.deployed_stacks[stack_config.name] = "oldhash"
    stack_manager.deployed_service_images[stack_config.name] = {
        "bazarr": "lscr.io/linuxserver/bazarr:0.9.0"
    }
    configure_compose_run(mock_run, compose_file)
    result = stack_manager.deploy_stack(stack_config, [compose_file])
    assert result.status == "updated"
    assert result.image_changes == [
        "Updated test-stack - bazarr image: lscr.io/linuxserver/bazarr:0.9.0 -> lscr.io/linuxserver/bazarr:1.0.0"
    ]


@patch('subprocess.run')
def test_deploy_stack_update_can_be_deferred(mock_run, stack_manager, stack_config, tmp_path):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  app:\n    image: example/app:2.0\n")
    stack_manager.deployed_stacks[stack_config.name] = "oldhash"
    stack_manager.deployed_service_images[stack_config.name] = {"app": "example/app:1.0"}
    stack_manager.deployed_service_hashes[stack_config.name] = {"app": "old-service-hash"}
    configure_compose_run(mock_run, compose_file)

    result = stack_manager.deploy_stack(
        stack_config,
        [compose_file],
        update_guard=lambda stack, service: (False, "waiting for midnight"),
    )

    assert result.status == "deferred"
    assert result.detail == "app: waiting for midnight"
    assert result.deferred_services == ["app"]
    assert stack_manager.deployed_stacks[stack_config.name] == "oldhash"
    assert not any(
        call.args[0][:3] == ["docker", "stack", "deploy"]
        for call in mock_run.call_args_list
    )


@patch('subprocess.run')
def test_deploy_stack_does_not_defer_initial_install(
    mock_run, stack_manager, stack_config, tmp_path
):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  app:\n    image: example/app:1.0\n")
    update_guard = Mock(return_value=(False, "active playback"))
    configure_compose_run(mock_run, compose_file)

    result = stack_manager.deploy_stack(
        stack_config,
        [compose_file],
        update_guard=update_guard,
    )

    assert result.status == "new"
    update_guard.assert_not_called()


@patch('subprocess.run')
def test_deploy_stack_error(mock_run, stack_manager, stack_config, tmp_path):
    """Test error handling during stack deployment"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'\nservices:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:latest\n")
    mock_run.side_effect = Exception("Docker error")
    result = stack_manager.deploy_stack(stack_config, [compose_file])
    assert result.status == "failed"


def test_extract_service_images_overrides_later_compose_files(stack_manager, tmp_path):
    """Test service images use the final value across compose files."""
    base_compose = tmp_path / "base.yml"
    base_compose.write_text(
        "services:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:latest\n"
    )
    override_compose = tmp_path / "override.yml"
    override_compose.write_text(
        "services:\n  bazarr:\n    image: lscr.io/linuxserver/bazarr:development\n  sonarr:\n    image: lscr.io/linuxserver/sonarr:latest\n"
    )

    service_images = stack_manager._extract_service_images([base_compose, override_compose])

    assert service_images == {
        "bazarr": "lscr.io/linuxserver/bazarr:development",
        "sonarr": "lscr.io/linuxserver/sonarr:latest",
    }


def test_normalize_compose_data_removes_swarm_unsupported_fields(stack_manager):
    data = {
        "name": "demo",
        "services": {
            "app": {
                "image": "example/app:latest",
                "group_add": ["44"],
                "depends_on": {"db": {"condition": "service_started"}},
                "deploy": {
                    "resources": {
                        "limits": {"cpus": 1.0, "memory": "1G"},
                        "reservations": {"cpus": 0.25, "memory": "256M"},
                    }
                },
                "ports": [{"target": "8080", "published": "80"}],
                "secrets": [{"source": "secret", "target": "/secret", "mode": "0440"}],
                "configs": [{"source": "config", "target": "/config", "mode": "292"}],
                "volumes": [
                    {
                        "type": "tmpfs",
                        "target": "/dev/shm",
                        "tmpfs": {"size": "1073741824"},
                    }
                ],
            }
        },
    }

    normalized = stack_manager._normalize_compose_data(data)

    assert "name" not in normalized
    service = normalized["services"]["app"]
    assert "group_add" not in service
    assert "depends_on" not in service
    assert service["deploy"]["resources"]["limits"]["cpus"] == "1.0"
    assert service["deploy"]["resources"]["reservations"]["cpus"] == "0.25"
    assert service["ports"] == [{"target": 8080, "published": 80}]
    assert service["secrets"][0]["mode"] == 0o440
    assert service["configs"][0]["mode"] == 292
    assert service["volumes"][0]["tmpfs"]["size"] == 1073741824


def test_stack_update_deploys_only_changed_service(stack_manager, stack_config, tmp_path):
    old_data = yaml.safe_load(
        """services:
  jellyfin:
    image: example/jellyfin:1
    environment: [MODE=stable]
  wiki:
    image: example/wiki:1
    environment: [ROUTE=old]
"""
    )
    new_data = yaml.safe_load(
        """services:
  jellyfin:
    image: example/jellyfin:1
    environment: [MODE=stable]
  wiki:
    image: example/wiki:1
    environment: [ROUTE=new]
"""
    )
    rendered = tmp_path / "rendered.yml"
    rendered.write_text(yaml.safe_dump(new_data))
    source = tmp_path / "source.yml"
    source.write_text(yaml.safe_dump(new_data))
    stack_manager.deployed_stacks[stack_config.name] = "old-stack-hash"
    stack_manager.deployed_service_hashes[stack_config.name] = (
        stack_manager._calculate_service_hashes(old_data)
    )
    stack_manager.deployed_service_images[stack_config.name] = {
        "jellyfin": "example/jellyfin:1",
        "wiki": "example/wiki:1",
    }
    deployed = {}

    def capture_deploy(stack_name, compose_path, env):
        deployed.update(yaml.safe_load(compose_path.read_text()))
        return Mock(stdout="", stderr="", returncode=0)

    guard = Mock(return_value=(True, "idle"))
    with patch.object(
        stack_manager, "_render_compose_file", return_value=rendered
    ), patch.object(
        stack_manager, "_deploy_compose", side_effect=capture_deploy
    ), patch.object(stack_manager, "_persist_stack_state", return_value=True):
        result = stack_manager.deploy_stack(
            stack_config,
            [source],
            update_guard=guard,
        )

    assert result.status == "updated"
    assert result.applied_services == ["wiki"]
    assert set(deployed["services"]) == {"wiki"}
    guard.assert_called_once_with("test-stack", "wiki")


def test_media_service_can_defer_while_unrelated_service_deploys(
    stack_manager, stack_config, tmp_path
):
    old_data = yaml.safe_load(
        """services:
  jellyfin:
    image: example/jellyfin:1
  wiki:
    image: example/wiki:1
"""
    )
    new_data = yaml.safe_load(
        """services:
  jellyfin:
    image: example/jellyfin:2
  wiki:
    image: example/wiki:2
"""
    )
    rendered = tmp_path / "rendered.yml"
    rendered.write_text(yaml.safe_dump(new_data))
    source = tmp_path / "source.yml"
    source.write_text(yaml.safe_dump(new_data))
    old_hashes = stack_manager._calculate_service_hashes(old_data)
    new_hashes = stack_manager._calculate_service_hashes(new_data)
    stack_manager.deployed_stacks[stack_config.name] = "old-stack-hash"
    stack_manager.deployed_service_hashes[stack_config.name] = old_hashes
    stack_manager.deployed_service_images[stack_config.name] = {
        "jellyfin": "example/jellyfin:1",
        "wiki": "example/wiki:1",
    }
    deployed = {}

    def guard(stack_name, service_name):
        return (False, "active stream") if service_name == "jellyfin" else (True, "idle")

    def capture_deploy(stack_name, compose_path, env):
        deployed.update(yaml.safe_load(compose_path.read_text()))
        return Mock(stdout="", stderr="", returncode=0)

    with patch.object(
        stack_manager, "_render_compose_file", return_value=rendered
    ), patch.object(
        stack_manager, "_deploy_compose", side_effect=capture_deploy
    ), patch.object(stack_manager, "_persist_stack_state", return_value=True) as persist:
        result = stack_manager.deploy_stack(
            stack_config,
            [source],
            update_guard=guard,
        )

    assert result.status == "deferred"
    assert result.applied_services == ["wiki"]
    assert result.deferred_services == ["jellyfin"]
    assert set(deployed["services"]) == {"wiki"}
    persisted_hashes = persist.call_args.args[3]
    assert persisted_hashes["wiki"] == new_hashes["wiki"]
    assert persisted_hashes["jellyfin"] == old_hashes["jellyfin"]


@patch("subprocess.run")
def test_discover_persisted_stack_state(mock_run, stack_manager):
    payload = base64.b64encode(
        json.dumps(
            {
                "version": 1,
                "service_images": {
                    "web": "example/web:1.0",
                    "worker": "example/worker:1.0",
                },
            }
        ).encode()
    ).decode()
    configs = [
        {
            "CreatedAt": "2026-07-14T19:00:00Z",
            "Spec": {"Labels": {STACK_HASH_LABEL: "hash123"}, "Data": payload},
        }
    ]
    mock_run.side_effect = [
        Mock(stdout="demo\n", returncode=0),
        Mock(stdout="demo_web\ndemo_worker\n", returncode=0),
        Mock(stdout="hivemind-state-demo\n", returncode=0),
        Mock(stdout=json.dumps(configs), returncode=0),
    ]

    state = stack_manager._discover_persisted_stack_state("demo")

    assert state.status == "tracked"
    assert state.stack_hash == "hash123"
    assert state.service_images == {
        "web": "example/web:1.0",
        "worker": "example/worker:1.0",
    }


@patch("subprocess.run")
def test_deploy_adopts_untracked_stack_without_redeploy(
    mock_run, stack_manager, stack_config, tmp_path
):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  app:\n    image: example/app:1.0\n")
    state = PersistedStackState(status="untracked", service_names=["test-stack_app"])

    with patch.object(
        stack_manager, "_discover_persisted_stack_state", return_value=state
    ), patch.object(
        stack_manager, "_render_compose_file", return_value=compose_file
    ), patch.object(stack_manager, "_persist_stack_state", return_value=True) as persist:
        result = stack_manager.deploy_stack(stack_config, [compose_file])

    assert result == DeployResult(
        status="unchanged", detail="backfilled per-service deployment state"
    )
    persist.assert_called_once()
    mock_run.assert_not_called()


def test_version_one_state_backfills_hashes_without_redeploy(
    stack_manager, stack_config, tmp_path
):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  app:\n    image: example/app:1.0\n")
    stack_hash = stack_manager._calculate_stack_hash([compose_file], None)
    state = PersistedStackState(
        status="tracked",
        stack_hash=stack_hash,
        service_images={"app": "example/app:1.0"},
        service_hashes={},
    )

    with patch.object(
        stack_manager, "_discover_persisted_stack_state", return_value=state
    ), patch.object(
        stack_manager, "_render_compose_file", return_value=compose_file
    ), patch.object(
        stack_manager, "_deploy_compose"
    ) as deploy, patch.object(
        stack_manager, "_persist_stack_state", return_value=True
    ) as persist:
        result = stack_manager.deploy_stack(stack_config, [compose_file])

    assert result == DeployResult(
        status="unchanged", detail="backfilled per-service deployment state"
    )
    deploy.assert_not_called()
    assert persist.call_args.args[3]["app"]


@patch("subprocess.run")
def test_deploy_fails_closed_when_state_discovery_fails(
    mock_run, stack_manager, stack_config, tmp_path
):
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services:\n  app:\n    image: example/app:1.0\n")
    state = PersistedStackState(status="error", detail="Docker API unavailable")

    with patch.object(stack_manager, "_discover_persisted_stack_state", return_value=state):
        result = stack_manager.deploy_stack(stack_config, [compose_file])

    assert result == DeployResult(status="failed", detail="Docker API unavailable")
    mock_run.assert_not_called()


@patch("subprocess.run")
def test_persist_stack_state_uses_swarm_config_without_service_update(mock_run, stack_manager):
    mock_run.return_value = Mock(stdout="", returncode=0)

    result = stack_manager._persist_stack_state(
        "demo",
        "hash123",
        {"web": "example/web:1.0"},
        {"web": "servicehash123"},
    )

    assert result is True
    commands = [call.args[0] for call in mock_run.call_args_list]
    assert commands[0][:3] == ["docker", "config", "ls"]
    assert commands[1][:3] == [
        "docker",
        "config",
        "create",
    ]
    assert f"{STATE_LABEL}=true" in commands[1]
    assert f"{STATE_STACK_LABEL}=demo" in commands[1]
    assert f"{STACK_HASH_LABEL}=hash123" in commands[1]
    assert all(command[:2] != ["docker", "service"] for command in commands)
