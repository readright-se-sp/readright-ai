import re

import pytest
from packaging_helpers import ROOT, read_text


def script(name: str) -> str:
    return read_text(ROOT / "scripts" / name)


def code_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


@pytest.mark.parametrize("name", ["start-stack.sh", "idle-check.sh"])
def test_scripts_are_bash_and_fail_fast(name):
    """Criteria 16, 17."""
    text = script(name)
    assert text.startswith("#!") and "bash" in text.splitlines()[0]
    assert "set -euo pipefail" in code_lines(text)


def test_start_stack_reads_the_key_from_ssm_with_decryption():
    """Criterion 16."""
    text = script("start-stack.sh")
    assert "aws ssm get-parameter" in text
    assert "--with-decryption" in text
    assert re.search(r"--query\s+['\"]?Parameter\.Value['\"]?", text)
    assert re.search(r"--output\s+text", text)
    assert "${MAI_API_KEY_PARAMETER:-/readright/mai/api-key}" in text


def test_start_stack_exports_the_key_and_starts_production_compose_from_the_repo_root():
    """Criterion 16."""
    text = script("start-stack.sh")
    assert re.search(r"export\s+MAI_API_KEY\b", text)
    assert "docker compose -f compose.yaml up -d --build" in text
    assert re.search(r"^\s*cd\s+", text, re.MULTILINE)


def test_start_stack_never_prints_the_key():
    """Criterion 16."""
    lines = code_lines(script("start-stack.sh"))
    for line in lines:
        if re.match(r"(echo|printf|cat)\b", line):
            assert "MAI_API_KEY" not in line.replace("MAI_API_KEY_PARAMETER", "")
        assert not re.match(r"set\s+-\w*x", line)


def test_idle_check_reads_health_live_with_overridable_url():
    """Criterion 17."""
    text = script("idle-check.sh")
    assert re.search(r"\$\{\w+:-[^}]*/health/live\}", text)
    assert "last_request_at" in text
    assert "started_at" in text


def test_idle_check_shuts_down_after_idle_minutes_defaulting_to_thirty():
    """Criterion 17."""
    text = script("idle-check.sh")
    assert "${IDLE_MINUTES:-30}" in text
    assert "shutdown -h now" in text


def test_idle_check_does_nothing_when_the_server_is_unreachable():
    """Criterion 17."""
    text = script("idle-check.sh")
    # An unreachable /health/live must be handled explicitly (set -e would otherwise abort
    # with an error instead of logging and exiting quietly).
    assert re.search(r"\|\||\bif\b[^\n]*(curl|wget|python)|\bexit 0\b", text)


def test_gitattributes_forces_lf_for_shell_docker_and_caddy_files():
    """Criterion 17."""
    lines = [ln.split() for ln in read_text(ROOT / ".gitattributes").splitlines()]
    for pattern in ("*.sh", "Caddyfile", "Dockerfile"):
        rules = [ln for ln in lines if ln and ln[0] == pattern]
        assert rules, f"no rule for {pattern}"
        assert any("eol=lf" in attr for attr in rules[0][1:]), pattern
