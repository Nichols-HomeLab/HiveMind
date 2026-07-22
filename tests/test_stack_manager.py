"""Tests for stack_manager module"""

import base64
import json
import pytest
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
    mock_run.return_value = Mock(stdout="Stack deployed", returncode=0)
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
    mock_run.return_value = Mock(stdout="Stack deployed", returncode=0)
    result = stack_manager.deploy_stack(stack_config, [compose_file], env_file)
    assert result.status == "new"


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
    mock_run.return_value = Mock(stdout="Stack updated", returncode=0)
    result = stack_manager.deploy_stack(stack_config, [compose_file])
    assert result.status == "updated"
    assert result.image_changes == [
        "Updated test-stack - bazarr image: lscr.io/linuxserver/bazarr:0.9.0 -> lscr.io/linuxserver/bazarr:1.0.0"
    ]


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
    assert service["ports"] == [{"target": 8080, "published": 80}]
    assert service["secrets"][0]["mode"] == 0o440
    assert service["configs"][0]["mode"] == 292
    assert service["volumes"][0]["tmpfs"]["size"] == 1073741824


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

    with patch.object(stack_manager, "_discover_persisted_stack_state", return_value=state), patch.object(
        stack_manager, "_persist_stack_state", return_value=True
    ) as persist:
        result = stack_manager.deploy_stack(stack_config, [compose_file])

    assert result == DeployResult(status="unchanged", detail="adopted existing stack")
    persist.assert_called_once()
    mock_run.assert_not_called()


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
