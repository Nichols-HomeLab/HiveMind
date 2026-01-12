"""Tests for stack_manager module"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
from src.stack_manager import SwarmStackManager, StackConfig


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
    hash_value = stack_manager._calculate_stack_hash(compose_file, None)
    assert isinstance(hash_value, str)
    assert len(hash_value) == 64


def test_calculate_stack_hash_with_env(stack_manager, tmp_path):
    """Test stack hash calculation with env file"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    env_file = tmp_path / ".env"
    env_file.write_text("VAR=value")
    hash_value = stack_manager._calculate_stack_hash(compose_file, env_file)
    assert isinstance(hash_value, str)
    assert len(hash_value) == 64


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


@patch('subprocess.run')
def test_deploy_stack_success(mock_run, stack_manager, stack_config, tmp_path):
    """Test successful stack deployment"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    mock_run.return_value = Mock(stdout="Stack deployed", returncode=0)
    result = stack_manager.deploy_stack(stack_config, compose_file)
    assert result is True
    assert stack_config.name in stack_manager.deployed_stacks


@patch('subprocess.run')
def test_deploy_stack_up_to_date(mock_run, stack_manager, stack_config, tmp_path):
    """Test deploying stack that is already up to date"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    hash_value = stack_manager._calculate_stack_hash(compose_file, None)
    stack_manager.deployed_stacks[stack_config.name] = hash_value
    result = stack_manager.deploy_stack(stack_config, compose_file)
    assert result is True
    mock_run.assert_not_called()


@patch('subprocess.run')
def test_deploy_stack_with_env_file(mock_run, stack_manager, stack_config, tmp_path):
    """Test stack deployment with environment file"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    env_file = tmp_path / ".env"
    env_file.write_text("VAR=value")
    mock_run.return_value = Mock(stdout="Stack deployed", returncode=0)
    result = stack_manager.deploy_stack(stack_config, compose_file, env_file)
    assert result is True


@patch('subprocess.run')
def test_deploy_stack_error(mock_run, stack_manager, stack_config, tmp_path):
    """Test error handling during stack deployment"""
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    mock_run.side_effect = Exception("Docker error")
    result = stack_manager.deploy_stack(stack_config, compose_file)
    assert result is False
