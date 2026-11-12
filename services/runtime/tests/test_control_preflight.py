import os
import subprocess
import sys
from pathlib import Path


def test_control_preflight_guarded_outside_checkout(tmp_path: Path) -> None:
    examples = Path(__file__).resolve().parents[3] / "config/runtime/control"
    config = tmp_path / "config.json"
    config.write_bytes((examples / "config.example.json").read_bytes())
    event = tmp_path / "event.json"
    event.write_bytes((examples / "chat-event.example.json").read_bytes())
    program = r"""
import importlib.abc, pathlib, socket, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked={'markos','duckdb','fastapi','jwt','jinja2','uvicorn','psycopg','aio_pika'}
        if fullname.split('.')[0] in blocked:
            raise AssertionError('adapter or optional dependency import attempted')
sys.meta_path.insert(0,Guard())
def deny(*args,**kwargs): raise AssertionError('network attempted')
socket.socket=deny
socket.create_connection=deny
socket.getaddrinfo=deny
original=pathlib.Path.open
def opened(path,*args,**kwargs):
    installed=path.is_relative_to(sys.prefix) or path.is_relative_to(sys.base_prefix)
    if str(path) not in {sys.argv[1],sys.argv[2]} and not installed:
        raise AssertionError('implicit file discovery attempted')
    return original(path,*args,**kwargs)
pathlib.Path.open=opened
from markos_runtime.cli import main
assert main(['preflight-control','--config',sys.argv[1],'--event',sys.argv[2]])==0
"""
    env = dict(os.environ)
    for name in (
        "MARKOS_CONFIG",
        "MARKOS_RUNTIME_CONFIG",
        "MARKOS_VAULT_PATH",
        "MARKOS_MARKDOWN_ROOT",
    ):
        env[name] = str(tmp_path / "forbidden")
    for name in ("MARKOS_RUNTIME_CONTROL_DATABASE_DSN", "MARKOS_RUNTIME_CONTROL_SESSION_KEY"):
        env[name] = "NEVER_RESOLVE_SECRET"
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(config), str(event)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "NEVER_RESOLVE" not in result.stdout + result.stderr
    assert "no authentication or connections" in result.stdout


def test_control_preflight_failure_is_redacted_and_has_no_implicit_config(tmp_path: Path) -> None:
    config = tmp_path / "invalid.json"
    config.write_text('{"secret":"NEVER_ECHO","gateway_enabled":true}')
    for args in (
        ["preflight-control"],
        ["preflight-control", "--config", str(config)],
        ["serve-control"],
    ):
        result = subprocess.run(
            [sys.executable, "-I", "-m", "markos_runtime", *args],
            cwd=tmp_path,
            env={**os.environ, "MARKOS_RUNTIME_CONFIG": str(config)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "NEVER_ECHO" not in result.stdout + result.stderr
