"""Git repository management"""

import subprocess
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger('hivemind.git')


@dataclass
class GitConfig:
    """Git repository configuration"""
    url: str
    branch: str = "main"
    path: str = "."
    username: Optional[str] = None
    password: Optional[str] = None
    ssh_key: Optional[str] = None
    poll_interval: int = 60


class GitRepository:
    """Handles Git repository operations"""
    
    def __init__(self, config: GitConfig, work_dir: str):
        logger.debug(f"Initializing GitRepository with URL: {config.url}")
        self.config = config
        self.work_dir = Path(work_dir)
        self.repo_path = self.work_dir / "repo"
        logger.debug(f"Repository path: {self.repo_path}")
        logger.debug(f"Branch: {config.branch}")
        logger.debug(f"Subpath: {config.path}")
        self.current_commit = None
        logger.info(f"GitRepository initialized for {config.url}")
        
    def clone_or_pull(self) -> bool:
        """Clone repository if not exists, otherwise pull latest changes"""
        logger.debug("Starting clone_or_pull operation")
        try:
            if not self.repo_path.exists():
                logger.info(f"Repository not found locally, cloning {self.config.url}")
                logger.debug(f"Target branch: {self.config.branch}")
                self._clone()
                logger.info("Repository cloned successfully")
            else:
                logger.info("Repository exists, pulling latest changes")
                logger.debug(f"Repository path: {self.repo_path}")
                self._pull()
                logger.info("Repository updated successfully")
            
            logger.debug("Checking current commit")
            new_commit = self._get_current_commit()
            logger.debug(f"Current commit: {new_commit}")
            logger.debug(f"Previous commit: {self.current_commit}")
            
            if new_commit != self.current_commit:
                logger.info(f"Repository updated to commit {new_commit[:8]}")
                if self.current_commit:
                    logger.info(f"Previous commit was {self.current_commit[:8]}")
                self.current_commit = new_commit
                return True
            
            logger.debug("No changes detected in repository")
            return False
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed with exit code {e.returncode}: {e}")
            if e.stderr:
                logger.error(f"Git stderr: {e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr}")
            if e.stdout:
                logger.debug(f"Git stdout: {e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during clone_or_pull: {e}", exc_info=True)
            return False
    
    def _clone(self):
        """Clone the repository"""
        logger.debug("Preparing git clone command")
        cmd = ["git", "clone"]
        
        if self.config.branch:
            logger.debug(f"Adding branch specification: {self.config.branch}")
            cmd.extend(["-b", self.config.branch])
        
        repo_url = self._get_authenticated_url()
        cmd.extend([repo_url, str(self.repo_path)])
        
        logger.debug(f"Executing git clone to {self.repo_path}")
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.debug("Git clone completed successfully")
            if result.stdout:
                logger.debug(f"Clone output: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git clone failed: {e}")
            if e.stderr:
                logger.error(f"Clone error output: {e.stderr}")
            raise
    
    def _pull(self):
        """Pull latest changes"""
        logger.debug("Fetching from origin")
        try:
            result = subprocess.run(
                ["git", "fetch", "origin"],
                cwd=self.repo_path,
                check=True,
                capture_output=True,
                text=True
            )
            logger.debug("Fetch completed successfully")
            if result.stderr:
                logger.debug(f"Fetch output: {result.stderr}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git fetch failed: {e}")
            if e.stderr:
                logger.error(f"Fetch error: {e.stderr}")
            raise
        
        logger.debug(f"Resetting to origin/{self.config.branch}")
        try:
            result = subprocess.run(
                ["git", "reset", "--hard", f"origin/{self.config.branch}"],
                cwd=self.repo_path,
                check=True,
                capture_output=True,
                text=True
            )
            logger.debug("Reset completed successfully")
            if result.stdout:
                logger.debug(f"Reset output: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git reset failed: {e}")
            if e.stderr:
                logger.error(f"Reset error: {e.stderr}")
            raise
    
    def _get_current_commit(self) -> str:
        """Get current commit hash"""
        logger.debug("Getting current commit hash")
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_path,
                check=True,
                capture_output=True,
                text=True
            )
            commit = result.stdout.strip()
            logger.debug(f"Current commit: {commit}")
            return commit
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get current commit: {e}")
            raise
    
    def _get_authenticated_url(self) -> str:
        """Build authenticated Git URL"""
        logger.debug("Building authenticated URL")
        if self.config.username and self.config.password:
            logger.debug(f"Using credentials for user: {self.config.username}")
            url = self.config.url
            if url.startswith("https://"):
                url = url.replace("https://", f"https://{self.config.username}:{self.config.password}@")
                logger.debug("Added credentials to HTTPS URL")
            elif url.startswith("http://"):
                url = url.replace("http://", f"http://{self.config.username}:{self.config.password}@")
                logger.debug("Added credentials to HTTP URL")
            else:
                logger.warning(f"URL scheme not recognized for authentication: {url}")
            return url
        logger.debug("No credentials configured, using URL as-is")
        return self.config.url
    
    def get_file_path(self, relative_path: str) -> Path:
        """Get absolute path to a file in the repository"""
        full_path = self.repo_path / self.config.path / relative_path
        logger.debug(f"Resolved file path: {relative_path} -> {full_path}")
        return full_path
