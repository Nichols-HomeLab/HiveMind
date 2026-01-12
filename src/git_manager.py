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
        self.config = config
        self.work_dir = Path(work_dir)
        self.repo_path = self.work_dir / "repo"
        self.current_commit = None
        
    def clone_or_pull(self) -> bool:
        """Clone repository if not exists, otherwise pull latest changes"""
        try:
            if not self.repo_path.exists():
                logger.info(f"Cloning repository {self.config.url}")
                self._clone()
            else:
                logger.info("Pulling latest changes")
                self._pull()
            
            new_commit = self._get_current_commit()
            if new_commit != self.current_commit:
                logger.info(f"Repository updated to commit {new_commit}")
                self.current_commit = new_commit
                return True
            return False
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed: {e}")
            return False
    
    def _clone(self):
        """Clone the repository"""
        cmd = ["git", "clone"]
        
        if self.config.branch:
            cmd.extend(["-b", self.config.branch])
        
        repo_url = self._get_authenticated_url()
        cmd.extend([repo_url, str(self.repo_path)])
        
        subprocess.run(cmd, check=True, capture_output=True)
    
    def _pull(self):
        """Pull latest changes"""
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{self.config.branch}"],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )
    
    def _get_current_commit(self) -> str:
        """Get current commit hash"""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()
    
    def _get_authenticated_url(self) -> str:
        """Build authenticated Git URL"""
        if self.config.username and self.config.password:
            url = self.config.url
            if url.startswith("https://"):
                url = url.replace("https://", f"https://{self.config.username}:{self.config.password}@")
            elif url.startswith("http://"):
                url = url.replace("http://", f"http://{self.config.username}:{self.config.password}@")
            return url
        return self.config.url
    
    def get_file_path(self, relative_path: str) -> Path:
        """Get absolute path to a file in the repository"""
        return self.repo_path / self.config.path / relative_path
