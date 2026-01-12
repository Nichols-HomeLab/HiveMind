"""Tests for controller module"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from src.controller import HiveMind


@pytest.fixture
def config_file(tmp_path):
    """Create a test configuration file"""
    config = {
        "git": {
            "url": "https://github.com/test/repo.git",
            "branch": "main",
            "path": ".",
            "poll_interval": 60
        }
    }
    config_path = tmp_path / "config.yml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)
    return str(config_path)


@pytest.fixture
def stacks_config():
    """Create test stacks configuration"""
    return {
        "stacks": [
            {
                "name": "test-stack-1",
                "compose_file": "stack1/compose.yml",
                "enabled": True
            },
            {
                "name": "test-stack-2",
                "compose_file": "stack2/compose.yml",
                "enabled": False
            }
        ]
    }


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_hivemind_initialization(mock_stack_manager, mock_git_repo, config_file):
    """Test HiveMind controller initialization"""
    hivemind = HiveMind(config_file)
    assert hivemind.config_path == Path(config_file)
    assert hivemind.config["git"]["url"] == "https://github.com/test/repo.git"
    assert hivemind.running is False
    mock_git_repo.assert_called_once()
    mock_stack_manager.assert_called_once()


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_load_config(mock_stack_manager, mock_git_repo, config_file):
    """Test configuration loading"""
    hivemind = HiveMind(config_file)
    config = hivemind._load_config()
    assert "git" in config
    assert config["git"]["url"] == "https://github.com/test/repo.git"


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_load_stacks_config_from_repo(mock_stack_manager, mock_git_repo, config_file, stacks_config, tmp_path):
    """Test loading stacks configuration from repository"""
    hivemind = HiveMind(config_file)
    
    stacks_file = tmp_path / "stacks.yml"
    with open(stacks_file, "w") as f:
        yaml.safe_dump(stacks_config, f)
    
    hivemind.git_repo.get_file_path = Mock(return_value=stacks_file)
    stacks = hivemind._load_stacks_config()
    
    assert len(stacks) == 2
    assert stacks[0].name == "test-stack-1"
    assert stacks[0].enabled is True
    assert stacks[1].name == "test-stack-2"
    assert stacks[1].enabled is False


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_load_stacks_config_from_hivemind_config(mock_stack_manager, mock_git_repo, tmp_path):
    """Test loading stacks from HiveMind config when repo file doesn't exist"""
    config = {
        "git": {
            "url": "https://github.com/test/repo.git",
            "branch": "main",
            "path": ".",
            "poll_interval": 60
        },
        "stacks": [
            {
                "name": "test-stack",
                "compose_file": "compose.yml",
                "enabled": True
            }
        ]
    }
    config_path = tmp_path / "config.yml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)
    
    hivemind = HiveMind(str(config_path))
    hivemind.git_repo.get_file_path = Mock(return_value=Path("/nonexistent/stacks.yml"))
    stacks = hivemind._load_stacks_config()
    
    assert len(stacks) == 1
    assert stacks[0].name == "test-stack"


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_load_stacks_config_not_found(mock_stack_manager, mock_git_repo, config_file):
    """Test handling when stacks configuration is not found"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.get_file_path = Mock(return_value=Path("/nonexistent/stacks.yml"))
    stacks = hivemind._load_stacks_config()
    assert len(stacks) == 0


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_reconcile_no_changes(mock_stack_manager, mock_git_repo, config_file):
    """Test reconciliation when no changes detected"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.clone_or_pull = Mock(return_value=False)
    hivemind.git_repo.current_commit = "abc123"
    hivemind.reconcile()
    hivemind.stack_manager.deploy_stack.assert_not_called()


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_reconcile_with_changes(mock_stack_manager, mock_git_repo, config_file, stacks_config, tmp_path):
    """Test reconciliation with changes"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.clone_or_pull = Mock(return_value=True)
    
    stacks_file = tmp_path / "stacks.yml"
    with open(stacks_file, "w") as f:
        yaml.safe_dump(stacks_config, f)
    
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    
    hivemind.git_repo.get_file_path = Mock(side_effect=[stacks_file, compose_file, compose_file])
    hivemind.stack_manager.list_stacks = Mock(return_value=[])
    hivemind.stack_manager.deploy_stack = Mock(return_value=True)
    
    hivemind.reconcile()
    
    assert hivemind.stack_manager.deploy_stack.call_count == 1


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_reconcile_removes_disabled_stacks(mock_stack_manager, mock_git_repo, config_file, stacks_config, tmp_path):
    """Test that reconciliation removes disabled stacks"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.clone_or_pull = Mock(return_value=True)
    
    stacks_file = tmp_path / "stacks.yml"
    with open(stacks_file, "w") as f:
        yaml.safe_dump(stacks_config, f)
    
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("version: '3'")
    
    hivemind.git_repo.get_file_path = Mock(side_effect=[stacks_file, compose_file, compose_file])
    hivemind.stack_manager.list_stacks = Mock(return_value=["test-stack-2"])
    hivemind.stack_manager.deploy_stack = Mock(return_value=True)
    hivemind.stack_manager.remove_stack = Mock(return_value=True)
    
    hivemind.reconcile()
    
    hivemind.stack_manager.remove_stack.assert_called_once_with("test-stack-2")


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_bootstrap(mock_stack_manager, mock_git_repo, config_file):
    """Test bootstrap functionality"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.current_commit = None
    hivemind.git_repo.clone_or_pull = Mock(return_value=True)
    hivemind.git_repo.get_file_path = Mock(return_value=Path("/nonexistent/stacks.yml"))
    hivemind.bootstrap()
    hivemind.git_repo.clone_or_pull.assert_called_once()


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
@patch('time.sleep')
def test_run_loop(mock_sleep, mock_stack_manager, mock_git_repo, config_file):
    """Test main run loop"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.current_commit = "abc123"
    hivemind.git_repo.clone_or_pull = Mock(return_value=False)
    
    mock_sleep.side_effect = [None, KeyboardInterrupt()]
    
    hivemind.run()
    
    assert hivemind.running is False
    assert mock_sleep.call_count == 1
