"""Tests for main module"""

import pytest
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from src.main import _build_config_from_env, _write_temp_config, main


def test_build_config_from_env_success():
    """Test building configuration from environment variables"""
    env_vars = {
        "HIVEMIND_GIT_URL": "https://github.com/test/repo.git",
        "HIVEMIND_GIT_BRANCH": "dev",
        "HIVEMIND_GIT_PATH": "stacks",
        "HIVEMIND_GIT_USERNAME": "user",
        "HIVEMIND_GIT_PASSWORD": "pass",
        "HIVEMIND_GIT_POLL_INTERVAL": "120"
    }
    
    with patch.dict(os.environ, env_vars):
        config = _build_config_from_env()
        assert config["git"]["url"] == "https://github.com/test/repo.git"
        assert config["git"]["branch"] == "dev"
        assert config["git"]["path"] == "stacks"
        assert config["git"]["username"] == "user"
        assert config["git"]["password"] == "pass"
        assert config["git"]["poll_interval"] == 120


def test_build_config_from_env_defaults():
    """Test default values when optional env vars are not set"""
    env_vars = {
        "HIVEMIND_GIT_URL": "https://github.com/test/repo.git"
    }
    
    with patch.dict(os.environ, env_vars, clear=True):
        config = _build_config_from_env()
        assert config["git"]["branch"] == "main"
        assert config["git"]["path"] == "."
        assert config["git"]["poll_interval"] == 60


def test_build_config_from_env_missing_url():
    """Test error when required HIVEMIND_GIT_URL is missing"""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="Missing required env var HIVEMIND_GIT_URL"):
            _build_config_from_env()


def test_write_temp_config(tmp_path):
    """Test writing temporary configuration file"""
    config = {
        "git": {
            "url": "https://github.com/test/repo.git",
            "branch": "main"
        }
    }
    
    with patch('tempfile.gettempdir', return_value=str(tmp_path)):
        config_path = _write_temp_config(config)
        assert Path(config_path).exists()
        assert "hivemind-config.yml" in config_path


@patch('src.main.HiveMind')
def test_main_with_config_file(mock_hivemind, tmp_path):
    """Test main function with existing config file"""
    config_file = tmp_path / "config.yml"
    config_file.write_text("git:\n  url: test")
    
    with patch.object(sys, 'argv', ['main.py', str(config_file)]):
        main()
        mock_hivemind.assert_called_once_with(str(config_file))
        mock_hivemind.return_value.run.assert_called_once()


@patch('src.main.HiveMind')
def test_main_with_bootstrap(mock_hivemind, tmp_path):
    """Test main function with bootstrap command"""
    config_file = tmp_path / "config.yml"
    config_file.write_text("git:\n  url: test")
    
    with patch.object(sys, 'argv', ['main.py', str(config_file), 'bootstrap']):
        main()
        mock_hivemind.assert_called_once_with(str(config_file))
        mock_hivemind.return_value.bootstrap.assert_called_once()


@patch('src.main.HiveMind')
@patch('src.main._build_config_from_env')
@patch('src.main._write_temp_config')
def test_main_fallback_to_env(mock_write_config, mock_build_config, mock_hivemind, tmp_path):
    """Test main function falling back to environment variables"""
    mock_build_config.return_value = {"git": {"url": "test"}}
    mock_write_config.return_value = str(tmp_path / "config.yml")
    
    with patch.object(sys, 'argv', ['main.py', '/nonexistent/config.yml']):
        main()
        mock_build_config.assert_called_once()
        mock_write_config.assert_called_once()
        mock_hivemind.return_value.run.assert_called_once()


@patch('src.main._build_config_from_env')
def test_main_env_fallback_error(mock_build_config):
    """Test main function error handling when env fallback fails"""
    mock_build_config.side_effect = ValueError("Missing env var")
    
    with patch.object(sys, 'argv', ['main.py', '/nonexistent/config.yml']):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_main_no_arguments():
    """Test main function with no arguments"""
    with patch.object(sys, 'argv', ['main.py']):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
