"""Tests for controller module"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from src.controller import HiveMind
from src.stack_manager import DeployResult


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
    hivemind.stack_manager.deploy_stack = Mock(return_value=DeployResult(status="updated"))
    
    hivemind.reconcile()
    
    assert hivemind.stack_manager.deploy_stack.call_count == 1


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_reconcile_uses_default_sops_env_file(mock_stack_manager, mock_git_repo, config_file, tmp_path):
    """Test reconciliation uses env/<stack>.env.sops when stack env_file is omitted"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.clone_or_pull = Mock(return_value=True)

    stacks_file = tmp_path / "stacks.yml"
    stacks_file.write_text(
        yaml.safe_dump(
            {
                "stacks": [
                    {
                        "name": "arr",
                        "compose_file": "Compose-Files/Arr/sonarr.yml",
                        "enabled": True,
                    }
                ]
            }
        )
    )
    compose_file = tmp_path / "Compose-Files" / "Arr" / "sonarr.yml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services:\n  sonarr:\n    image: lscr.io/linuxserver/sonarr:latest\n")
    sops_env = tmp_path / "env" / "arr.env.sops"
    sops_env.parent.mkdir()
    sops_env.write_text("encrypted payload")

    def resolve(path):
        return tmp_path / path

    hivemind.git_repo.get_file_path = Mock(side_effect=resolve)
    hivemind.stack_manager.list_stacks = Mock(return_value=[])
    hivemind.stack_manager.deploy_stack = Mock(return_value=DeployResult(status="updated"))

    hivemind.reconcile()

    hivemind.stack_manager.deploy_stack.assert_called_once()
    assert hivemind.stack_manager.deploy_stack.call_args.args[2] == sops_env


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
    hivemind.stack_manager.deploy_stack = Mock(return_value=DeployResult(status="updated"))
    hivemind.stack_manager.remove_stack = Mock(return_value=True)
    
    hivemind.reconcile()
    
    hivemind.stack_manager.remove_stack.assert_called_once_with("test-stack-2")


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_reconcile_removes_replaced_and_retired_stacks(mock_stack_manager, mock_git_repo, config_file, tmp_path):
    """Test that reconciliation removes obsolete stacks after successful deployment"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.clone_or_pull = Mock(return_value=True)

    stacks_file = tmp_path / "stacks.yml"
    stacks_file.write_text(
        yaml.safe_dump(
            {
                "retired_stacks": ["old-retired"],
                "stacks": [
                    {
                        "name": "grouped-stack",
                        "compose_files": ["stack1.yml", "stack2.yml"],
                        "replaces": ["old-stack-1", "old-stack-2"],
                        "enabled": True,
                    }
                ],
            }
        )
    )

    compose_file_1 = tmp_path / "stack1.yml"
    compose_file_1.write_text("services:\n  one:\n    image: busybox\n")
    compose_file_2 = tmp_path / "stack2.yml"
    compose_file_2.write_text("services:\n  two:\n    image: busybox\n")

    def resolve(path):
        return {
            "stacks.yml": stacks_file,
            "stack1.yml": compose_file_1,
            "stack2.yml": compose_file_2,
        }.get(path, tmp_path / path)

    hivemind.git_repo.get_file_path = Mock(side_effect=resolve)
    hivemind.stack_manager.list_stacks = Mock(
        return_value=["grouped-stack", "old-stack-1", "old-stack-2", "old-retired"]
    )
    hivemind.stack_manager.deploy_stack = Mock(return_value=DeployResult(status="updated"))
    hivemind.stack_manager.remove_stack = Mock(return_value=True)

    hivemind.reconcile()

    assert hivemind.stack_manager.remove_stack.call_count == 3
    hivemind.stack_manager.remove_stack.assert_any_call("old-stack-1")
    hivemind.stack_manager.remove_stack.assert_any_call("old-stack-2")
    hivemind.stack_manager.remove_stack.assert_any_call("old-retired")


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_reconcile_keeps_replaced_stack_when_deploy_fails(mock_stack_manager, mock_git_repo, config_file, tmp_path):
    """Test that replaced stacks are not removed if the replacement fails to deploy"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.clone_or_pull = Mock(return_value=True)

    stacks_file = tmp_path / "stacks.yml"
    stacks_file.write_text(
        yaml.safe_dump(
            {
                "stacks": [
                    {
                        "name": "grouped-stack",
                        "compose_file": "stack.yml",
                        "replaces": ["old-stack"],
                        "enabled": True,
                    }
                ],
            }
        )
    )

    compose_file = tmp_path / "stack.yml"
    compose_file.write_text("services:\n  one:\n    image: busybox\n")

    def resolve(path):
        return {
            "stacks.yml": stacks_file,
            "stack.yml": compose_file,
        }.get(path, tmp_path / path)

    hivemind.git_repo.get_file_path = Mock(side_effect=resolve)
    hivemind.stack_manager.list_stacks = Mock(return_value=["grouped-stack", "old-stack"])
    hivemind.stack_manager.deploy_stack = Mock(return_value=DeployResult(status="failed"))
    hivemind.stack_manager.remove_stack = Mock(return_value=True)

    hivemind.reconcile()

    hivemind.stack_manager.remove_stack.assert_not_called()


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_bootstrap(mock_stack_manager, mock_git_repo, config_file):
    """Test bootstrap functionality"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.current_commit = None
    hivemind.git_repo.clone_or_pull = Mock(return_value=True)
    hivemind.git_repo.get_file_path = Mock(return_value=Path("/nonexistent/stacks.yml"))
    hivemind.bootstrap()
    assert hivemind.git_repo.clone_or_pull.call_count == 2


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_run_loop(mock_stack_manager, mock_git_repo, config_file):
    """Test main run loop"""
    hivemind = HiveMind(config_file)
    hivemind.git_repo.current_commit = "abc123"
    hivemind.git_repo.clone_or_pull = Mock(return_value=False)
    hivemind._reconcile_event = Mock()
    hivemind._reconcile_event.wait.side_effect = [False, KeyboardInterrupt()]
    
    hivemind.run()
    
    assert hivemind.running is False
    assert hivemind._reconcile_event.wait.call_count == 2
    hivemind._reconcile_event.wait.assert_called_with(timeout=60)


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_trigger_reconcile_wakes_run_loop(mock_stack_manager, mock_git_repo, config_file):
    hivemind = HiveMind(config_file)
    hivemind._reconcile_event = Mock()

    hivemind.trigger_reconcile()

    hivemind._reconcile_event.set.assert_called_once_with()


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_notify_update_reports_stack_outcomes(mock_stack_manager, mock_git_repo, tmp_path):
    """Test notification body distinguishes updated stacks from unchanged ones"""
    config = {
        "git": {
            "url": "https://github.com/test/repo.git",
            "branch": "main",
            "path": ".",
            "poll_interval": 60
        },
        "notifications": {
            "smtp": {
                "host": "smtp.example.com",
                "port": 587,
                "username": "user",
                "password": "pass",
                "from": "from@example.com",
                "to": ["to@example.com"]
            }
        }
    }
    config_path = tmp_path / "config.yml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)

    hivemind = HiveMind(str(config_path))
    hivemind.notifier = Mock()

    hivemind._notify_update(
        "aaaaaaaa",
        "bbbbbbbb",
        {
            "new": ["new-stack"],
            "updated": ["updated-stack"],
            "unchanged": ["same-stack"],
            "failed": ["bad-stack"],
            "skipped": ["disabled-stack"],
            "detail_lines": [
                "Created new-stack - bazarr image: lscr.io/linuxserver/bazarr:latest",
                "Updated updated-stack - bazarr image: lscr.io/linuxserver/bazarr:1.0.0 -> lscr.io/linuxserver/bazarr:1.1.0",
            ],
        },
    )

    hivemind.notifier.send.assert_called_once()
    subject, body = hivemind.notifier.send.call_args.args
    assert subject == "HiveMind upgrade applied"
    assert "New stacks: new-stack" in body
    assert "Updated stacks: updated-stack" in body
    assert "Unchanged stacks: same-stack" in body
    assert "Failed stacks: bad-stack" in body
    assert "Skipped disabled stacks: disabled-stack" in body
    assert "Created new-stack - bazarr image: lscr.io/linuxserver/bazarr:latest" in body
    assert (
        "Updated updated-stack - bazarr image: lscr.io/linuxserver/bazarr:1.0.0 -> lscr.io/linuxserver/bazarr:1.1.0"
        in body
    )


@patch('src.controller.GitRepository')
@patch('src.controller.SwarmStackManager')
def test_notify_update_without_stack_changes(mock_stack_manager, mock_git_repo, tmp_path):
    """Test notification body for repo-only changes"""
    config = {
        "git": {
            "url": "https://github.com/test/repo.git",
            "branch": "main",
            "path": ".",
            "poll_interval": 60
        },
        "notifications": {
            "smtp": {
                "host": "smtp.example.com",
                "port": 587,
                "username": "user",
                "password": "pass",
                "from": "from@example.com",
                "to": ["to@example.com"]
            }
        }
    }
    config_path = tmp_path / "config.yml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)

    hivemind = HiveMind(str(config_path))
    hivemind.notifier = Mock()

    hivemind._notify_update(
        "aaaaaaaa",
        "bbbbbbbb",
        {"new": [], "updated": [], "unchanged": ["same-stack"], "failed": [], "skipped": []},
    )

    hivemind.notifier.send.assert_called_once()
    subject, body = hivemind.notifier.send.call_args.args
    assert subject == "HiveMind repository update"
    assert "No stack upgrades were needed." in body
