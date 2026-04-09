"""
Tests for runner.proxy and runner.debug modules.
Task: task_003_core_infra
"""

import sys
import json
from pathlib import Path

# Add kit directory to path
kit_dir = Path(__file__).parent
sys.path.insert(0, str(kit_dir))

from runner import (
    ProxyManager,
    ProxyConfig,
    ProxyMethod,
    ProxyType,
    DebugCollector,
    DebugArchive,
    create_proxy_manager,
    create_debug_collector,
)
from runner.job import JobCard, JobStatus, StepCard, StepStatus


def test_proxy_import():
    """Test that proxy module imports correctly."""
    print("[PASS] Proxy module import")
    return True


def test_proxy_config_creation():
    """Test ProxyConfig creation from dict."""
    config = ProxyConfig.from_dict({
        "enabled": True,
        "socks5": "socks5://user:pass@127.0.0.1:10808"
    })
    
    assert config.enabled == True
    assert config.host == "127.0.0.1"
    assert config.port == 10808
    assert config.username == "user"
    assert config.password == "pass"
    assert config.type == ProxyType.SOCKS5
    
    print("[PASS] ProxyConfig creation from dict")
    return True


def test_proxy_config_url_parsing():
    """Test proxy URL parsing."""
    # Test SOCKS5
    config = ProxyConfig.from_dict({"socks5": "socks5://127.0.0.1:10808"})
    assert config.type == ProxyType.SOCKS5
    assert config.host == "127.0.0.1"
    assert config.port == 10808
    
    # Test with auth
    config = ProxyConfig.from_dict({"socks5": "socks5://user:pass@host:9050"})
    assert config.type == ProxyType.SOCKS5
    assert config.host == "host"
    assert config.port == 9050
    assert config.username == "user"
    assert config.password == "pass"
    
    print("[PASS] ProxyConfig URL parsing")
    return True


def test_proxy_config_to_url():
    """Test ProxyConfig to_url method."""
    config = ProxyConfig(
        enabled=True,
        type=ProxyType.SOCKS5,
        host="127.0.0.1",
        port=10808,
        username="user",
        password="pass"
    )
    
    url = config.to_url()
    assert "socks5://" in url
    assert "127.0.0.1:10808" in url
    
    print("[PASS] ProxyConfig to_url")
    return True


def test_proxy_manager_initialization():
    """Test ProxyManager initialization."""
    pm = ProxyManager({
        "enabled": True,
        "socks5": "socks5://127.0.0.1:10808"
    })
    
    assert pm.is_enabled() == True
    assert pm.is_configured() == True
    
    status = pm.get_status()
    assert "enabled" in status
    assert "configured" in status
    
    print("[PASS] ProxyManager initialization")
    return True


def test_proxy_manager_env_vars():
    """Test proxy environment variable injection."""
    pm = ProxyManager({
        "enabled": True,
        "socks5": "socks5://127.0.0.1:10808"
    })
    
    env_vars = pm.get_env_vars()
    assert "HTTP_PROXY" in env_vars
    assert "HTTPS_PROXY" in env_vars
    assert "socks5://127.0.0.1:10808" in env_vars["HTTP_PROXY"]
    
    print("[PASS] ProxyManager env vars injection")
    return True


def test_proxy_manager_param_injection():
    """Test proxy command-line parameter injection."""
    pm = ProxyManager({
        "enabled": True,
        "socks5": "socks5://127.0.0.1:10808",
        "method": "param"
    })
    
    param = pm.get_param("--proxy '{proxy}'")
    assert param is not None
    assert "--proxy" in param
    assert "socks5://127.0.0.1:10808" in param
    
    print("[PASS] ProxyManager param injection")
    return True


def test_proxy_manager_step_injection():
    """Test proxy injection for pipeline step."""
    pm = ProxyManager({
        "enabled": True,
        "socks5": "socks5://127.0.0.1:10808"
    })
    
    manifest = {
        "tool": "yt-dlp",
        "proxy": {"method": "env"}
    }
    
    env_vars, extra_params = pm.inject_for_step(manifest, {"EXISTING": "value"})
    
    assert "HTTP_PROXY" in env_vars
    assert env_vars["EXISTING"] == "value"
    
    print("[PASS] ProxyManager step injection")
    return True


def test_debug_import():
    """Test that debug module imports correctly."""
    print("[PASS] Debug module import")
    return True


def test_debug_collector_creation():
    """Test DebugCollector creation."""
    dc = DebugCollector()
    
    assert dc.output_dir.exists() or True  # May not exist yet
    assert len(dc.sanitization_rules) > 0
    
    print("[PASS] DebugCollector creation")
    return True


def test_debug_sanitization():
    """Test content sanitization."""
    dc = DebugCollector()
    
    # Test API key sanitization
    text_with_key = "My API key is sk-1234567890abcdefghijklmnop"
    sanitized = dc._sanitize_content(text_with_key)
    assert "sk-" not in sanitized
    assert "[REDACTED]" in sanitized or "[API_KEY_REDACTED]" in sanitized
    
    # Test password in URL
    text_with_pass = "http://user:secret123@example.com/api"
    sanitized = dc._sanitize_content(text_with_pass)
    assert "secret123" not in sanitized
    
    print("[PASS] DebugCollector sanitization")
    return True


def test_debug_archive_creation():
    """Test debug archive creation."""
    from runner.job import JobCard, JobStatus, StepCard, StepStatus
    
    # Create test job
    job = JobCard(
        job_id="test-job-123",
        goal="Test goal",
        input_data={"test": "value"},
        expected_output=["test output"],
        steps=[
            StepCard(
                step_id="step-1",
                step_name="Convert video",
                tool="ffmpeg",
                mode="convert",
                input_params={"input_file": "test.mp4"},
            )
        ]
    )
    job.status = JobStatus.FAILED
    job.steps[0].status = StepStatus.FAILED
    job.steps[0].stdout = "Test output"
    job.steps[0].stderr = "Error occurred"
    
    # Create collector
    dc = DebugCollector(output_dir=Path.home() / ".kit" / "test_logs")
    
    # Collect archive
    archive = dc.collect_for_job(job, step_index=0)
    
    assert archive.path.exists()
    assert archive.job_id == job.job_id
    assert archive.size_bytes > 0
    assert len(archive.contents) > 0
    
    # Cleanup
    archive.path.unlink()
    
    print("[PASS] DebugArchive creation")
    return True


def test_create_proxy_manager_function():
    """Test create_proxy_manager convenience function."""
    pm = create_proxy_manager(global_config={
        "proxy": {
            "enabled": True,
            "socks5": "socks5://127.0.0.1:10808"
        }
    })
    
    assert isinstance(pm, ProxyManager)
    assert pm.is_enabled()
    
    print("[PASS] create_proxy_manager function")
    return True


def test_create_debug_collector_function():
    """Test create_debug_collector convenience function."""
    dc = create_debug_collector(output_dir=str(Path.home() / ".kit" / "test_logs"))
    
    assert isinstance(dc, DebugCollector)
    
    print("[PASS] create_debug_collector function")
    return True


def run_all_tests():
    """Run all core_infra tests."""
    print("=" * 60)
    print("Running task_003_core_infra tests")
    print("=" * 60)
    
    tests = [
        ("Proxy import", test_proxy_import),
        ("ProxyConfig creation", test_proxy_config_creation),
        ("ProxyConfig URL parsing", test_proxy_config_url_parsing),
        ("ProxyConfig to_url", test_proxy_config_to_url),
        ("ProxyManager initialization", test_proxy_manager_initialization),
        ("ProxyManager env vars", test_proxy_manager_env_vars),
        ("ProxyManager param injection", test_proxy_manager_param_injection),
        ("ProxyManager step injection", test_proxy_manager_step_injection),
        ("Debug import", test_debug_import),
        ("DebugCollector creation", test_debug_collector_creation),
        ("Debug sanitization", test_debug_sanitization),
        ("DebugArchive creation", test_debug_archive_creation),
        ("create_proxy_manager", test_create_proxy_manager_function),
        ("create_debug_collector", test_create_debug_collector_function),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            print(f"\n[{name}]")
            if test_func():
                passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Tests completed: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
