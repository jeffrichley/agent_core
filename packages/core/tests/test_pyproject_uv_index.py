"""Tests for pyproject.toml's [tool.uv] configuration.

Phase 2.6: assert the cu130 PyTorch index is configured at the workspace
level so both `uv export` (in release.yml) and `uv pip install` (in
release.py) pick it up automatically. The bug Phase 3.5's test instance
surfaced: install couldn't find torch==2.12.0+cu130 because the cu130
index wasn't reachable from any config layer uv consults.
"""

from pathlib import Path

import tomllib


PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def test_pyproject_declares_pytorch_cu130_index():
    """[[tool.uv.index]] section must declare the pytorch-cu130 index by name."""
    data = tomllib.loads(PYPROJECT.read_text())
    indices = data.get("tool", {}).get("uv", {}).get("index", [])
    assert isinstance(indices, list), "expected [[tool.uv.index]] as an array of tables"
    by_name = {idx.get("name"): idx for idx in indices}
    assert "pytorch-cu130" in by_name, (
        f"expected an index named 'pytorch-cu130'; found {sorted(by_name)}"
    )
    cu130 = by_name["pytorch-cu130"]
    assert cu130.get("url") == "https://download.pytorch.org/whl/cu130"
    # `explicit = true` means uv won't search this index by default for
    # arbitrary packages — it'll only consult it when a source binding
    # explicitly references it. Prevents accidentally pulling other
    # torch variants from the cu130 index.
    assert cu130.get("explicit") is True, (
        "pytorch-cu130 index must be marked explicit=true so uv only consults it "
        "for packages with an explicit source binding"
    )


def test_pyproject_binds_torch_to_pytorch_cu130_under_cu130_extra():
    """[tool.uv.sources] must route torch to the pytorch-cu130 index when
    the cu130 extra is active. Without this binding, the index exists
    but torch resolution still hits PyPI.

    uv uses a dedicated `extra` key (not a PEP 508 marker string) to scope
    a source binding to an optional-dependency group. The correct schema is:
        torch = [{ index = "pytorch-cu130", extra = "cu130" }]
    See: https://docs.astral.sh/uv/concepts/projects/dependencies/#sources-for-optional-dependencies
    """
    data = tomllib.loads(PYPROJECT.read_text())
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    torch_source = sources.get("torch")
    assert torch_source is not None, (
        "expected [tool.uv.sources] to define a 'torch' binding"
    )
    # The binding may be a single mapping or a list-of-mappings depending
    # on uv schema version. Normalize.
    bindings = torch_source if isinstance(torch_source, list) else [torch_source]
    matched = [
        b for b in bindings
        if b.get("index") == "pytorch-cu130"
        and b.get("extra") == "cu130"
    ]
    assert matched, (
        f"expected at least one torch binding referencing index='pytorch-cu130' "
        f"with extra='cu130'; found {bindings}"
    )
