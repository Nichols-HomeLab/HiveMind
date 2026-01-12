"""Tests for git_manager module"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from src.git_manager import GitRepository, GitConfig


@pytest.fixture
def git_config():
    """Create a test git configuration"""
    return GitConfig(
        url="https://github.com/test/repo.git",
        branch="main",
        path=".",
        username="testuser",
        password="testpass",
        poll_interval=60
    )


@pytest.fixture
def git_repo(git_config, tmp_path):
    """Create a test git repository instance"""
    return GitRepository(git_config, str(tmp_path))


def test_git_config_creation():
    """Test GitConfig dataclass creation"""
    config = GitConfig(url="https://github.com/test/repo.git")
    assert config.url == "https://github.com/test/repo.git"
    assert config.branch == "main"
    assert config.path == "."
    assert config.poll_interval == 60


def test_git_repository_initialization(git_repo, tmp_path):
    """Test GitRepository initialization"""
    assert git_repo.config.url == "https://github.com/test/repo.git"
    assert git_repo.work_dir == tmp_path
    assert git_repo.repo_path == tmp_path / "repo"
    assert git_repo.current_commit is None


def test_get_authenticated_url_with_credentials(git_repo):
    """Test URL authentication with username and password"""
    url = git_repo._get_authenticated_url()
    assert "testuser:testpass" in url
    assert url.startswith("https://")


def test_get_authenticated_url_without_credentials():
    """Test URL without authentication"""
    config = GitConfig(url="https://github.com/test/repo.git")
    repo = GitRepository(config, "/tmp/test")
    url = repo._get_authenticated_url()
    assert url == "https://github.com/test/repo.git"


def test_get_file_path(git_repo):
    """Test getting file path in repository"""
    file_path = git_repo.get_file_path("stacks.yml")
    assert file_path == git_repo.repo_path / "." / "stacks.yml"


@patch('subprocess.run')
def test_clone_success(mock_run, git_repo):
    """Test successful repository clone"""
    mock_run.return_value = Mock(returncode=0)
    git_repo._clone()
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "git" in args
    assert "clone" in args


@patch('subprocess.run')
def test_pull_success(mock_run, git_repo, tmp_path):
    """Test successful repository pull"""
    git_repo.repo_path.mkdir(parents=True, exist_ok=True)
    mock_run.return_value = Mock(returncode=0)
    git_repo._pull()
    assert mock_run.call_count == 2


@patch('subprocess.run')
def test_get_current_commit(mock_run, git_repo, tmp_path):
    """Test getting current commit hash"""
    git_repo.repo_path.mkdir(parents=True, exist_ok=True)
    mock_run.return_value = Mock(stdout="abc123def456\n", returncode=0)
    commit = git_repo._get_current_commit()
    assert commit == "abc123def456"


@patch('src.git_manager.GitRepository._get_current_commit')
@patch('src.git_manager.GitRepository._clone')
def test_clone_or_pull_new_repo(mock_clone, mock_get_commit, git_repo):
    """Test clone_or_pull with new repository"""
    mock_get_commit.return_value = "abc123"
    result = git_repo.clone_or_pull()
    mock_clone.assert_called_once()
    assert result is True
    assert git_repo.current_commit == "abc123"


@patch('src.git_manager.GitRepository._get_current_commit')
@patch('src.git_manager.GitRepository._pull')
def test_clone_or_pull_existing_repo(mock_pull, mock_get_commit, git_repo):
    """Test clone_or_pull with existing repository"""
    git_repo.repo_path.mkdir(parents=True, exist_ok=True)
    git_repo.current_commit = "abc123"
    mock_get_commit.return_value = "abc123"
    result = git_repo.clone_or_pull()
    mock_pull.assert_called_once()
    assert result is False


@patch('subprocess.run')
def test_clone_or_pull_error_handling(mock_run, git_repo):
    """Test error handling in clone_or_pull"""
    mock_run.side_effect = Exception("Git error")
    result = git_repo.clone_or_pull()
    assert result is False
