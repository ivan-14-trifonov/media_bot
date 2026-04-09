"""
Proxy Manager Module
Manages proxy configuration and injection for network tools.
Implements Principle 39: Automatic proxy handling.
"""

import os
import re
import socket
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum


class ProxyMethod(str, Enum):
    """Methods for proxy injection."""
    ENV = "env"       # Set environment variables (HTTP_PROXY, HTTPS_PROXY)
    PARAM = "param"   # Add command-line parameter (--proxy ...)
    SYSTEM = "system" # Use system proxy settings


class ProxyType(str, Enum):
    """Proxy protocol types."""
    SOCKS5 = "socks5"
    SOCKS4 = "socks4"
    HTTP = "http"
    HTTPS = "https"


@dataclass
class ProxyConfig:
    """Proxy configuration from config.yaml or manifest."""
    enabled: bool = True
    url: Optional[str] = None
    method: ProxyMethod = ProxyMethod.ENV
    type: ProxyType = ProxyType.SOCKS5
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    auto_detect: bool = True
    check_before_use: bool = True
    
    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "ProxyConfig":
        """Create ProxyConfig from dictionary."""
        url = config.get("socks5") or config.get("url") or config.get("http")
        
        # Parse URL if provided
        host, port, username, password = None, None, None, None
        proxy_type = ProxyType.SOCKS5
        
        if url:
            parsed = cls._parse_proxy_url(url)
            host = parsed.get("host")
            port = parsed.get("port")
            username = parsed.get("username")
            password = parsed.get("password")
            proxy_type = parsed.get("type", ProxyType.SOCKS5)
        
        # Override with explicit values
        host = config.get("host", host)
        port = config.get("port", port)
        username = config.get("username", username)
        password = config.get("password", password)
        
        method_str = config.get("method", "env").lower()
        method = ProxyMethod(method_str) if method_str in ProxyMethod.__members__ else ProxyMethod.ENV
        
        type_str = config.get("type", "socks5").lower()
        proxy_type = ProxyType(type_str) if type_str in ProxyType.__members__ else ProxyType.SOCKS5
        
        return cls(
            enabled=config.get("enabled", True),
            url=url,
            method=method,
            type=proxy_type,
            host=host,
            port=port,
            username=username,
            password=password,
            auto_detect=config.get("auto_detect", True),
            check_before_use=config.get("check_before_use", True),
        )
    
    @staticmethod
    def _parse_proxy_url(url: str) -> Dict[str, Any]:
        """Parse proxy URL into components."""
        # Pattern: [type://][user:pass@]host:port
        pattern = r'^(?:(?P<type>socks5|socks4|https?|http)://)?(?:(?P<user>[^:]+):(?P<pass>[^@]+)@)?(?P<host>[^:]+):?(?P<port>\d+)?$'
        match = re.match(pattern, url, re.IGNORECASE)
        
        if not match:
            return {}
        
        result = match.groupdict()
        
        proxy_type = None
        if result.get("type"):
            type_str = result["type"].lower()
            if type_str in ("socks5",):
                proxy_type = ProxyType.SOCKS5
            elif type_str in ("socks4",):
                proxy_type = ProxyType.SOCKS4
            elif type_str in ("https",):
                proxy_type = ProxyType.HTTPS
            elif type_str in ("http",):
                proxy_type = ProxyType.HTTP
        
        return {
            "type": proxy_type,
            "host": result.get("host"),
            "port": int(result["port"]) if result.get("port") else None,
            "username": result.get("user"),
            "password": result.get("pass"),
        }
    
    def to_url(self) -> Optional[str]:
        """Convert config back to proxy URL string."""
        if not self.host or not self.port:
            return None
        
        # Build URL
        type_prefix = f"{self.type.value}://" if self.type else ""
        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        elif self.username:
            auth = f"{self.username}@"
        
        return f"{type_prefix}{auth}{self.host}:{self.port}"


@dataclass
class ProxyCheckResult:
    """Result of proxy connectivity check."""
    is_reachable: bool
    response_time_ms: Optional[float] = None
    error: Optional[str] = None
    target: str = "1.1.1.1:443"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_reachable": self.is_reachable,
            "response_time_ms": self.response_time_ms,
            "error": self.error,
            "target": self.target,
        }


class ProxyManager:
    """
    Manages proxy configuration and injection for network tools.
    
    Features:
    - Reads proxy config from config.yaml or tool manifests
    - Checks proxy availability before use
    - Injects proxy via environment variables or command-line parameters
    - Supports SOCKS5, HTTP proxies
    - Auto-detection of system proxy settings
    """
    
    # Default test targets for connectivity check
    DEFAULT_TEST_TARGETS = [
        ("1.1.1.1", 443),  # Cloudflare DNS
        ("8.8.8.8", 53),   # Google DNS
        ("google.com", 80), # HTTP test
    ]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize ProxyManager.
        
        Args:
            config: Proxy configuration dictionary (from config.yaml proxy block)
        """
        self.config = ProxyConfig.from_dict(config) if config else ProxyConfig()
        self._is_available: Optional[bool] = None
        self._last_check: Optional[ProxyCheckResult] = None
    
    def is_enabled(self) -> bool:
        """Check if proxy is enabled in configuration."""
        return self.config.enabled
    
    def is_configured(self) -> bool:
        """Check if proxy has valid configuration."""
        return bool(self.config.host and self.config.port)
    
    def check_availability(self, force: bool = False) -> bool:
        """
        Check if proxy server is reachable.
        
        Args:
            force: Force re-check even if already checked
            
        Returns:
            True if proxy is reachable, False otherwise
        """
        if not self.config.enabled:
            return True  # Proxy disabled, consider available
        
        if not self.is_configured():
            return False
        
        if self._is_available is not None and not force:
            return self._is_available
        
        # Test connectivity
        result = self._check_connectivity()
        self._last_check = result
        self._is_available = result.is_reachable
        
        return self._is_available
    
    def _check_connectivity(self) -> ProxyCheckResult:
        """
        Test proxy connectivity using socket connection.
        
        Returns:
            ProxyCheckResult with connectivity status
        """
        if not self.config.host or not self.config.port:
            return ProxyCheckResult(
                is_reachable=False,
                error="Proxy host/port not configured"
            )
        
        # Try to connect through proxy to test targets
        for host, port in self.DEFAULT_TEST_TARGETS:
            try:
                result = self._test_proxy_connection(host, port)
                if result.is_reachable:
                    return result
            except Exception:
                continue
        
        # All targets failed
        return ProxyCheckResult(
            is_reachable=False,
            error=f"Failed to connect through proxy to any test target",
            target="multiple"
        )
    
    def _test_proxy_connection(self, target_host: str, target_port: int) -> ProxyCheckResult:
        """
        Test connection to target through proxy.
        
        For SOCKS5, we need to use a library or subprocess.
        For HTTP proxy, we can use raw socket.
        """
        import time
        
        start_time = time.time()
        
        try:
            if self.config.type in (ProxyType.SOCKS5, ProxyType.SOCKS4):
                # For SOCKS5, try using subprocess with curl or netcat
                result = self._test_socks5_connection(target_host, target_port)
            else:
                # HTTP proxy - use raw socket CONNECT
                result = self._test_http_proxy_connection(target_host, target_port)
            
            if result.is_reachable:
                result.response_time_ms = (time.time() - start_time) * 1000
            
            return result
            
        except Exception as e:
            return ProxyCheckResult(
                is_reachable=False,
                error=str(e),
                target=f"{target_host}:{target_port}"
            )
    
    def _test_socks5_connection(self, target_host: str, target_port: int) -> ProxyCheckResult:
        """Test SOCKS5 proxy connection using subprocess."""
        # Try using curl if available
        proxy_url = self.config.to_url()
        if not proxy_url:
            return ProxyCheckResult(is_reachable=False, error="No proxy URL")
        
        try:
            # Use curl to test connection
            cmd = [
                "curl",
                "--proxy", proxy_url,
                "--connect-timeout", "5",
                "--max-time", "10",
                "-s", "-o", "/dev/null",
                "-w", "%{http_code}",
                f"https://{target_host}/"
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            
            if result.returncode == 0 and result.stdout.isdigit():
                http_code = int(result.stdout)
                if http_code < 500:  # Any non-server-error response means proxy works
                    return ProxyCheckResult(
                        is_reachable=True,
                        target=f"{target_host}:{target_port}"
                    )
            
            return ProxyCheckResult(
                is_reachable=False,
                error=f"curl failed: {result.stderr}",
                target=f"{target_host}:{target_port}"
            )
            
        except FileNotFoundError:
            # curl not available, try raw socket (limited SOCKS5 support)
            return self._test_socks5_raw(target_host, target_port)
        except Exception as e:
            return ProxyCheckResult(
                is_reachable=False,
                error=str(e),
                target=f"{target_host}:{target_port}"
            )
    
    def _test_socks5_raw(self, target_host: str, target_port: int) -> ProxyCheckResult:
        """Test SOCKS5 connection using raw socket (basic implementation)."""
        try:
            import socket
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            # Connect to SOCKS5 server
            sock.connect((self.config.host, self.config.port))
            
            # SOCKS5 greeting (no auth)
            sock.sendall(b'\x05\x01\x00')
            response = sock.recv(2)
            
            if len(response) < 2 or response[0] != 0x05:
                return ProxyCheckResult(
                    is_reachable=False,
                    error="Invalid SOCKS5 response",
                    target=f"{target_host}:{target_port}"
                )
            
            # Connect to target
            connect_request = b'\x05\x01\x00\x03'  # SOCKS5, CONNECT, no auth, domain
            connect_request += len(target_host).to_bytes(1, 'big')
            connect_request += target_host.encode('ascii')
            connect_request += target_port.to_bytes(2, 'big')
            
            sock.sendall(connect_request)
            response = sock.recv(4)
            
            if len(response) < 4 or response[1] != 0x00:
                return ProxyCheckResult(
                    is_reachable=False,
                    error="SOCKS5 connection refused",
                    target=f"{target_host}:{target_port}"
                )
            
            sock.close()
            return ProxyCheckResult(
                is_reachable=True,
                target=f"{target_host}:{target_port}"
            )
            
        except Exception as e:
            return ProxyCheckResult(
                is_reachable=False,
                error=str(e),
                target=f"{target_host}:{target_port}"
            )
    
    def _test_http_proxy_connection(self, target_host: str, target_port: int) -> ProxyCheckResult:
        """Test HTTP proxy connection using raw socket."""
        try:
            import socket
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            # Connect to HTTP proxy
            sock.connect((self.config.host, self.config.port))
            
            # Send HTTP CONNECT request
            request = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
            request += f"Host: {target_host}:{target_port}\r\n"
            request += "Proxy-Connection: Keep-Alive\r\n"
            request += "\r\n"
            
            sock.sendall(request.encode('ascii'))
            response = sock.recv(1024).decode('ascii', errors='ignore')
            
            sock.close()
            
            # Check for 200 OK response
            if "200" in response.split("\r\n")[0]:
                return ProxyCheckResult(
                    is_reachable=True,
                    target=f"{target_host}:{target_port}"
                )
            
            return ProxyCheckResult(
                is_reachable=False,
                error=f"HTTP proxy error: {response.split(chr(13))[0]}",
                target=f"{target_host}:{target_port}"
            )
            
        except Exception as e:
            return ProxyCheckResult(
                is_reachable=False,
                error=str(e),
                target=f"{target_host}:{target_port}"
            )
    
    def get_env_vars(self) -> Dict[str, str]:
        """
        Get environment variables for proxy injection.
        
        Returns:
            Dictionary with HTTP_PROXY, HTTPS_PROXY, etc.
        """
        if not self.config.enabled or not self.is_configured():
            return {}
        
        proxy_url = self.config.to_url()
        if not proxy_url:
            return {}
        
        # Add auth if present
        if self.config.username and self.config.password:
            # URL already has auth from to_url()
            pass
        
        return {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "ALL_PROXY": proxy_url,
            # For tools that use lowercase
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "all_proxy": proxy_url,
        }
    
    def get_param(self, param_format: str = "--proxy '{proxy}'") -> Optional[str]:
        """
        Get command-line parameter for proxy injection.
        
        Args:
            param_format: Format string for proxy parameter
            
        Returns:
            Formatted parameter string or None
        """
        if not self.config.enabled or not self.is_configured():
            return None
        
        proxy_url = self.config.to_url()
        if not proxy_url:
            return None
        
        return param_format.format(proxy=proxy_url)
    
    def inject_for_step(
        self,
        step_manifest: Dict[str, Any],
        existing_env: Optional[Dict[str, str]] = None
    ) -> Tuple[Dict[str, str], List[str]]:
        """
        Inject proxy settings for a pipeline step.
        
        Args:
            step_manifest: Tool manifest with proxy configuration
            existing_env: Existing environment variables
            
        Returns:
            Tuple of (env_vars, extra_params)
        """
        env_vars = dict(existing_env) if existing_env else {}
        extra_params = []
        
        # Get proxy config from step manifest or use global
        step_proxy_config = step_manifest.get("proxy")
        
        if step_proxy_config:
            # Tool-specific proxy config
            if isinstance(step_proxy_config, dict):
                method = step_proxy_config.get("method", "env")
                param_format = step_proxy_config.get("param_format", "--proxy '{proxy}'")
            else:
                method = "env"
                param_format = "--proxy '{proxy}'"
        else:
            # Use global config
            method = self.config.method.value
            param_format = "--proxy '{proxy}'"
        
        # Inject based on method
        if method == "env":
            env_vars.update(self.get_env_vars())
        elif method == "param":
            param = self.get_param(param_format)
            if param:
                extra_params.append(param)
        elif method == "system":
            # Use system proxy settings (already in environment)
            pass
        
        return env_vars, extra_params
    
    def auto_detect_system_proxy(self) -> bool:
        """
        Auto-detect system proxy settings.
        
        Returns:
            True if system proxy detected and configured
        """
        if os.name != "nt":  # Only Windows for now
            return False
        
        try:
            # Read Windows registry for proxy settings
            import winreg
            
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            ) as key:
                try:
                    proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                    if proxy_enable:
                        proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                        
                        # Parse proxy server string
                        if ":" in proxy_server:
                            host, port = proxy_server.split(":", 1)
                            self.config.host = host
                            self.config.port = int(port)
                            self.config.enabled = True
                            return True
                except FileNotFoundError:
                    pass
            
            return False
            
        except Exception:
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get proxy manager status."""
        return {
            "enabled": self.config.enabled,
            "configured": self.is_configured(),
            "available": self._is_available if self._is_available is not None else "not checked",
            "method": self.config.method.value,
            "type": self.config.type.value,
            "host": self.config.host,
            "port": self.config.port,
            "last_check": self._last_check.to_dict() if self._last_check else None,
        }


def create_proxy_manager(
    config_path: Optional[Path] = None,
    global_config: Optional[Dict[str, Any]] = None
) -> ProxyManager:
    """
    Create ProxyManager from config file or dictionary.
    
    Args:
        config_path: Path to config.yaml
        global_config: Global configuration dictionary
        
    Returns:
        Configured ProxyManager instance
    """
    proxy_config = None
    
    if global_config and "proxy" in global_config:
        proxy_config = global_config["proxy"]
    elif config_path and config_path.exists():
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            if config and "proxy" in config:
                proxy_config = config["proxy"]
    
    return ProxyManager(proxy_config)
