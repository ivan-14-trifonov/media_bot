"""
Installer module for Kit Runner.
Handles tool installation via winget, pip/pipx, and GitHub releases fallback.
"""

import os
import sys
import subprocess
import json
import re
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum


class InstallMethod(str, Enum):
    """Installation method used."""
    WINGET = "winget"
    PIP = "pip"
    PIPX = "pipx"
    GITHUB = "github"
    MANUAL = "manual"
    ALREADY_INSTALLED = "already_installed"


class InstallStatus(str, Enum):
    """Installation result status."""
    SUCCESS = "success"
    FAILED = "failed"
    ALREADY_INSTALLED = "already_installed"
    PARTIAL = "partial"


@dataclass
class InstallResult:
    """Result of tool installation attempt."""
    tool_name: str
    status: InstallStatus
    method: Optional[InstallMethod] = None
    version: Optional[str] = None
    path: Optional[str] = None
    message: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    manifest_confidence_updated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "tool_name": self.tool_name,
            "status": self.status.value,
            "method": self.method.value if self.method else None,
            "version": self.version,
            "path": self.path,
            "message": self.message,
            "errors": self.errors,
            "warnings": self.warnings,
            "manifest_confidence_updated": self.manifest_confidence_updated,
        }


@dataclass
class ToolInstallConfig:
    """Installation configuration for a tool."""
    tool_name: str
    winget_id: Optional[str] = None
    pip_package: Optional[str] = None
    github_repo: Optional[str] = None
    health_check_command: Optional[str] = None
    version_command: Optional[str] = None
    install_methods: List[str] = field(default_factory=list)

    @classmethod
    def from_manifest(cls, manifest: Dict[str, Any]) -> "ToolInstallConfig":
        """Create install config from tool manifest."""
        install_config = manifest.get("install", {})
        return cls(
            tool_name=manifest.get("tool", "unknown"),
            winget_id=install_config.get("winget_id"),
            pip_package=install_config.get("pip_package"),
            github_repo=install_config.get("github_repo"),
            health_check_command=install_config.get("health_check_command"),
            version_command=install_config.get("version_command"),
            install_methods=install_config.get("methods", ["winget", "pip", "github"]),
        )


class ToolInstaller:
    """
    Universal tool installer with multiple fallback methods.
    
    Priority:
    1. winget (Windows)
    2. pip/pipx (Python tools)
    3. GitHub releases
    4. Manual installation hints
    """

    # Known tool configurations
    KNOWN_TOOLS = {
        "ffmpeg": {
            "winget_id": "Gyan.FFmpeg",
            "winget_alternatives": ["yt-dlp.FFmpeg", "BtbN.FFmpeg.GPL"],
            "pip_package": None,
            "github_repo": "FFmpeg/FFmpeg",
            "health_check": "ffmpeg -version",
            "version_command": "ffmpeg -version",
        },
        "yt-dlp": {
            "winget_id": "yt-dlp.yt-dlp",
            "winget_alternatives": [],
            "pip_package": "yt-dlp",
            "github_repo": "yt-dlp/yt-dlp",
            "health_check": "yt-dlp --version",
            "version_command": "yt-dlp --version",
        },
        "whisper": {
            "winget_id": None,
            "winget_alternatives": [],
            "pip_package": "openai-whisper",
            "github_repo": "openai/whisper",
            "health_check": "whisper --version",
            "version_command": "whisper --version",
        },
        "deno": {
            "winget_id": "DenoLand.Deno",
            "winget_alternatives": [],
            "pip_package": None,
            "github_repo": "denoland/deno",
            "health_check": "deno --version",
            "version_command": "deno --version",
        },
    }

    def __init__(self, manifests_dir: Optional[Path] = None):
        self.manifests_dir = manifests_dir
        self._winget_available: Optional[bool] = None
        self._pip_available: Optional[bool] = None
        self._pipx_available: Optional[bool] = None

    def _check_winget(self) -> bool:
        """Check if winget is available."""
        if self._winget_available is not None:
            return self._winget_available
        
        try:
            result = subprocess.run(
                ["winget", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            self._winget_available = result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            self._winget_available = False
        
        return self._winget_available

    def _check_pip(self) -> bool:
        """Check if pip is available."""
        if self._pip_available is not None:
            return self._pip_available
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            self._pip_available = result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            self._pip_available = False
        
        return self._pip_available

    def _check_pipx(self) -> bool:
        """Check if pipx is available."""
        if self._pipx_available is not None:
            return self._pipx_available
        
        try:
            result = subprocess.run(
                ["pipx", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            self._pipx_available = result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            self._pipx_available = False
        
        return self._pipx_available

    def _run_command(
        self,
        cmd: List[str],
        timeout: int = 300,
        capture_output: bool = True
    ) -> Tuple[int, str, str]:
        """Run command and return (returncode, stdout, stderr)."""
        try:
            # Use utf-8 encoding for subprocess to handle international characters
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                env=env,
                encoding="utf-8",
                errors="replace"
            )
            return result.returncode, result.stdout or "", result.stderr or ""
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    def health_check(
        self,
        tool_name: str,
        manifest: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if tool is already installed and working.
        
        Returns:
            (is_installed, version_string)
        """
        # Get health check command from manifest or known tools
        health_cmd = None
        version_cmd = None
        
        if manifest:
            health_cmd = manifest.get("health_check", {}).get("command")
        
        if not health_cmd and tool_name in self.KNOWN_TOOLS:
            config = self.KNOWN_TOOLS[tool_name]
            health_cmd = config.get("health_check")
            version_cmd = config.get("version_command")
        
        if not health_cmd:
            # Try common defaults
            health_cmd = f"{tool_name} --version"
        
        # Try direct command first
        returncode, stdout, stderr = self._run_command(
            health_cmd.split(),
            timeout=10
        )
        
        if returncode == 0:
            # Extract version
            version = stdout.strip().split("\n")[0] if stdout else "unknown"
            if version_cmd and version == "unknown":
                _, v_stdout, _ = self._run_command(
                    version_cmd.split(),
                    timeout=10
                )
                if v_stdout:
                    version = v_stdout.strip().split("\n")[0]
            return True, version
        
        # Try python -m for Python tools
        if tool_name in ("yt-dlp", "whisper", "pipx"):
            module_name = tool_name.replace("-", "_")
            py_cmd = f"{sys.executable} -m {module_name} --version"
            returncode, stdout, stderr = self._run_command(
                py_cmd.split(),
                timeout=10
            )
            if returncode == 0:
                version = stdout.strip().split("\n")[0] if stdout else "unknown"
                return True, version
        
        return False, None

    def _search_winget(self, query: str) -> List[Dict[str, str]]:
        """Search winget for packages."""
        if not self._check_winget():
            return []
        
        returncode, stdout, _ = self._run_command(
            ["winget", "search", query, "--disable-interactivity"],
            timeout=60
        )
        
        if returncode != 0:
            return []
        
        packages = []
        lines = stdout.strip().split("\n")
        
        # Skip header lines
        for line in lines[4:]:  # Skip header and progress bars
            parts = line.split()
            if len(parts) >= 3:
                packages.append({
                    "name": parts[0],
                    "id": parts[1] if len(parts) > 1 else parts[0],
                    "version": parts[2] if len(parts) > 2 else "unknown"
                })
        
        return packages

    def _install_winget(
        self,
        package_id: str,
        silent: bool = True,
        accept_agreements: bool = True
    ) -> InstallResult:
        """Install package using winget."""
        if not self._check_winget():
            return InstallResult(
                tool_name=package_id,
                status=InstallStatus.FAILED,
                message="winget is not available on this system"
            )
        
        # Build command
        cmd = ["winget", "install", "--id", package_id, "--disable-interactivity"]
        
        if silent:
            cmd.append("--silent")
        
        if accept_agreements:
            cmd.append("--accept-package-agreements")
            cmd.append("--accept-source-agreements")
        
        # Check if already installed
        list_cmd = ["winget", "list", "--id", package_id, "--disable-interactivity"]
        returncode, stdout, _ = self._run_command(list_cmd, timeout=30)
        
        if returncode == 0 and package_id in stdout:
            # Already installed, get version
            version = self._extract_version_from_winget_list(stdout, package_id)
            return InstallResult(
                tool_name=package_id,
                status=InstallStatus.ALREADY_INSTALLED,
                method=InstallMethod.ALREADY_INSTALLED,
                version=version,
                message=f"Package {package_id} is already installed"
            )
        
        # Install
        returncode, stdout, stderr = self._run_command(cmd, timeout=600)
        
        if returncode == 0:
            # Get installed version
            version = self._get_installed_version_winget(package_id)
            return InstallResult(
                tool_name=package_id,
                status=InstallStatus.SUCCESS,
                method=InstallMethod.WINGET,
                version=version,
                message=f"Successfully installed {package_id} via winget"
            )
        else:
            return InstallResult(
                tool_name=package_id,
                status=InstallStatus.FAILED,
                method=InstallMethod.WINGET,
                message=f"winget installation failed: {stderr}",
                errors=[stderr]
            )

    def _extract_version_from_winget_list(
        self,
        output: str,
        package_id: str
    ) -> Optional[str]:
        """Extract version from winget list output."""
        for line in output.split("\n"):
            if package_id in line:
                parts = line.split()
                if len(parts) >= 3:
                    return parts[2]
        return None

    def _get_installed_version_winget(self, package_id: str) -> Optional[str]:
        """Get installed version using winget list."""
        returncode, stdout, _ = self._run_command(
            ["winget", "list", "--id", package_id, "--disable-interactivity"],
            timeout=30
        )
        if returncode == 0:
            return self._extract_version_from_winget_list(stdout, package_id)
        return None

    def _install_pip(
        self,
        package: str,
        use_pipx: bool = False
    ) -> InstallResult:
        """Install package using pip or pipx."""
        if use_pipx and self._check_pipx():
            cmd = ["pipx", "install", package]
            method = InstallMethod.PIPX
        elif self._check_pip():
            cmd = [sys.executable, "-m", "pip", "install", package]
            method = InstallMethod.PIP
        else:
            return InstallResult(
                tool_name=package,
                status=InstallStatus.FAILED,
                message="Neither pip nor pipx is available"
            )
        
        returncode, stdout, stderr = self._run_command(cmd, timeout=300)
        
        if returncode == 0:
            # Get version
            version = self._get_pip_version(package)
            return InstallResult(
                tool_name=package,
                status=InstallStatus.SUCCESS,
                method=method,
                version=version,
                message=f"Successfully installed {package} via {method.value}"
            )
        else:
            return InstallResult(
                tool_name=package,
                status=InstallStatus.FAILED,
                method=method,
                message=f"pip installation failed: {stderr}",
                errors=[stderr]
            )

    def _get_pip_version(self, package: str) -> Optional[str]:
        """Get installed version from pip."""
        returncode, stdout, _ = self._run_command(
            [sys.executable, "-m", "pip", "show", package],
            timeout=30
        )
        if returncode == 0:
            for line in stdout.split("\n"):
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
        return None

    def _install_github(
        self,
        repo: str,
        tool_name: str
    ) -> InstallResult:
        """
        Install from GitHub releases.
        This is a simplified implementation.
        """
        # For now, just provide guidance
        return InstallResult(
            tool_name=tool_name,
            status=InstallStatus.FAILED,
            method=InstallMethod.GITHUB,
            message=f"Manual installation required. Download from https://github.com/{repo}/releases",
            warnings=[
                f"Visit https://github.com/{repo}/releases",
                f"Download the latest release for your platform",
                f"Add to PATH or place in project directory"
            ]
        )

    def install(
        self,
        tool_name: str,
        manifest: Optional[Dict[str, Any]] = None,
        prefer_method: Optional[str] = None,
        silent: bool = True
    ) -> InstallResult:
        """
        Install a tool using available methods with fallbacks.
        
        Args:
            tool_name: Name of the tool to install
            manifest: Optional tool manifest for configuration
            prefer_method: Preferred installation method
            silent: Run installation silently
            
        Returns:
            InstallResult with installation status
        """
        # Check if already installed
        is_installed, version = self.health_check(tool_name, manifest)
        if is_installed:
            result = InstallResult(
                tool_name=tool_name,
                status=InstallStatus.ALREADY_INSTALLED,
                method=InstallMethod.ALREADY_INSTALLED,
                version=version,
                message=f"{tool_name} is already installed (version {version})"
            )
            self._update_manifest_confidence(manifest, result)
            return result
        
        # Get install config
        config = self._get_install_config(tool_name, manifest)
        
        # Try installation methods in priority order
        methods_to_try = config.install_methods or ["winget", "pip", "github"]
        
        if prefer_method:
            if prefer_method in methods_to_try:
                methods_to_try.remove(prefer_method)
            methods_to_try.insert(0, prefer_method)
        
        last_error = None
        
        for method in methods_to_try:
            result = None
            
            if method == "winget" and config.winget_id:
                result = self._install_winget(config.winget_id, silent=silent)
                
            elif method == "pip" and config.pip_package:
                result = self._install_pip(config.pip_package, use_pipx=False)
                
            elif method == "pipx" and config.pip_package:
                result = self._install_pip(config.pip_package, use_pipx=True)
                
            elif method == "github" and config.github_repo:
                result = self._install_github(config.github_repo, tool_name)
            
            if result and result.status in (
                InstallStatus.SUCCESS,
                InstallStatus.ALREADY_INSTALLED
            ):
                self._update_manifest_confidence(manifest, result)
                return result
            
            if result:
                last_error = result
        
        # All methods failed
        if last_error:
            last_error.status = InstallStatus.FAILED
            return last_error
        
        return InstallResult(
            tool_name=tool_name,
            status=InstallStatus.FAILED,
            message=f"No installation method available for {tool_name}",
            warnings=["Check manifest for installation configuration"]
        )

    def _get_install_config(
        self,
        tool_name: str,
        manifest: Optional[Dict[str, Any]]
    ) -> ToolInstallConfig:
        """Get installation configuration for a tool."""
        # Try manifest first
        if manifest and "install" in manifest:
            return ToolInstallConfig.from_manifest(manifest)
        
        # Fall back to known tools
        if tool_name in self.KNOWN_TOOLS:
            config = self.KNOWN_TOOLS[tool_name]
            return ToolInstallConfig(
                tool_name=tool_name,
                winget_id=config.get("winget_id"),
                pip_package=config.get("pip_package"),
                github_repo=config.get("github_repo"),
                health_check_command=config.get("health_check"),
                version_command=config.get("version_command"),
                install_methods=self._get_default_methods(
                    config.get("winget_id"),
                    config.get("pip_package")
                )
            )
        
        # Unknown tool - try generic approaches
        return ToolInstallConfig(
            tool_name=tool_name,
            install_methods=["winget", "pip", "github"]
        )

    def _get_default_methods(
        self,
        winget_id: Optional[str],
        pip_package: Optional[str]
    ) -> List[str]:
        """Get default installation methods based on available options."""
        methods = []
        
        if winget_id:
            methods.append("winget")
        if pip_package:
            methods.extend(["pip", "pipx"])
        methods.append("github")
        
        return methods

    def _update_manifest_confidence(
        self,
        manifest: Optional[Dict[str, Any]],
        result: InstallResult
    ) -> None:
        """Update manifest confidence based on installation result."""
        if not manifest:
            return
        
        if "confidence" not in manifest:
            manifest["confidence"] = {}
        
        if result.status == InstallStatus.SUCCESS:
            manifest["confidence"]["level"] = "high"
            manifest["confidence"]["verified"] = True
            manifest["confidence"]["installed_version"] = result.version
            manifest["confidence"]["install_method"] = result.method.value if result.method else None
            result.manifest_confidence_updated = True
        elif result.status == InstallStatus.FAILED:
            manifest["confidence"]["level"] = "low"
            manifest["confidence"]["install_failed"] = True
            result.manifest_confidence_updated = True

    def get_available_tools(self) -> List[Dict[str, Any]]:
        """Get list of tools with installation status."""
        tools = []
        
        for tool_name, config in self.KNOWN_TOOLS.items():
            is_installed, version = self.health_check(tool_name)
            tools.append({
                "name": tool_name,
                "installed": is_installed,
                "version": version,
                "winget_id": config.get("winget_id"),
                "pip_package": config.get("pip_package"),
            })
        
        return tools


def install_tool(
    tool_name: str,
    manifest: Optional[Dict[str, Any]] = None,
    prefer_method: Optional[str] = None
) -> InstallResult:
    """
    Convenience function to install a tool.
    
    Args:
        tool_name: Name of the tool to install
        manifest: Optional tool manifest
        prefer_method: Preferred installation method
        
    Returns:
        InstallResult with installation status
    """
    installer = ToolInstaller()
    return installer.install(tool_name, manifest, prefer_method)


def check_tool_installed(
    tool_name: str,
    manifest: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Check if a tool is installed.
    
    Args:
        tool_name: Name of the tool
        manifest: Optional tool manifest
        
    Returns:
        (is_installed, version)
    """
    installer = ToolInstaller()
    return installer.health_check(tool_name, manifest)
