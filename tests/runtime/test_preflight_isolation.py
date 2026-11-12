import os
import subprocess
import sys
from pathlib import Path

from markos.runtime.schemas import schema_documents


def test_generated_schemas_are_current() -> None:
    config = Path(__file__).resolve().parents[2] / "config"
    for name, body in schema_documents().items():
        assert (config / name).read_text() == body


def test_preflight_never_loads_legacy_config_or_connects(
    runtime_config: Path, runtime_env: dict[str, str], tmp_path: Path
) -> None:
    legacy = tmp_path / "forbidden.toml"
    legacy.write_text("not TOML and must not be read")
    env = (
        os.environ.copy()
        | runtime_env
        | {
            "MARKOS_CONFIG": str(legacy),
            "MARKOS_VAULT_PATH": str(tmp_path / "forbidden-old-root"),
            "MARKOS_MARKDOWN_ROOT": str(tmp_path / "forbidden-markdown_root"),
            "MARKOS_INDEX_PATH": str(tmp_path / "forbidden-index.duckdb"),
        }
    )
    script = r"""
import os, sys
from pathlib import Path
forbidden = tuple(os.environ[name] for name in
    ("MARKOS_CONFIG", "MARKOS_VAULT_PATH", "MARKOS_MARKDOWN_ROOT", "MARKOS_INDEX_PATH"))
def guard(event, args):
    if event == "socket.connect":
        raise AssertionError("preflight attempted network IO")
    if event in {"open", "os.listdir", "os.scandir"} and args:
        value = args[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            value = os.fsdecode(value)
            if any(value == p or value.startswith(p + os.sep) for p in forbidden):
                raise AssertionError("preflight accessed legacy data")
sys.addaudithook(guard)
from markos.runtime.bootstrap import validate_startup
assert "markos.settings" not in sys.modules
result = validate_startup(Path(sys.argv[1]))
assert "markos.settings" not in sys.modules
assert result.settings.scope.service_id == "foundation.publisher"
print("preflight-safe")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(runtime_config)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "preflight-safe"
    assert not (tmp_path / "forbidden-index.duckdb").exists()
