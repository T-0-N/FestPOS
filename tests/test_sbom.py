"""Release (T-0-N/FestPOS#14): Paketliste (SBOM, CycloneDX) für Update-Paket und Image, Herkunftsnachweis im
Release-Workflow, Prüfanleitung im Release-Text, Dependabot."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args) -> dict:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "sbom.py"), *args], capture_output=True, text=True,
                       check=True)
    return json.loads(r.stdout)


def test_zip_sbom_lists_python_packages_and_rqlite():
    d = _run("zip")
    assert d["bomFormat"] == "CycloneDX" and d["specVersion"] == "1.5"
    assert d["metadata"]["component"]["version"] == (ROOT / "VERSION").read_text().strip()
    names = {c["name"]: c for c in d["components"]}
    req = (ROOT / "requirements.txt").read_text()
    assert "fastapi" in names and f"fastapi=={names['fastapi']['version']}" in req
    assert names["fastapi"]["purl"].startswith("pkg:pypi/fastapi@") and names["fastapi"]["hashes"][0]["alg"] == "SHA-256"
    rq = names["rqlite"]
    assert f"RQLITE_SHA256={rq['hashes'][0]['content']}" in (ROOT / "deploy" / "rqlite.env").read_text()


def test_image_sbom_adds_debian_packages(tmp_path):
    lst = tmp_path / "festpos-pakete.txt"
    lst.write_text("bash\t5.2.37-2\tarm64\nlibc6\t2.41-12\tarm64\nkaputt\n")
    d = _run("image", str(lst))
    debs = [c for c in d["components"] if c["purl"].startswith("pkg:deb/")]
    assert [c["name"] for c in debs] == ["bash", "libc6"]
    assert debs[1]["purl"] == "pkg:deb/debian/libc6@2.41-12?arch=arm64"
    assert d["metadata"]["component"]["type"] == "operating-system"


def test_release_workflow_attests_and_notes_explain_verification():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "actions/attest-build-provenance@" in wf and "id-token: write" in wf and "attestations: write" in wf
    assert "tools/sbom.py zip" in wf and "tools/sbom.py image" in wf
    for subj in ("out/festpos.zip", "out/festpos-*.img.xz", "out/*.apk"):
        assert subj in wf
    assert "festpos-pakete.txt" in (ROOT / "deploy" / "pi-gen" / "stage-festpos" / "00-festpos" / "01-run.sh").read_text()
    notes = subprocess.run([sys.executable, str(ROOT / "tools" / "release_notes.py"), "v1.1.0"], capture_output=True,
                           text=True, check=True).stdout
    assert "gh attestation verify festpos.zip -R T-0-N/FestPOS" in notes and "cdx.json" in notes
    dep = (ROOT / ".github" / "dependabot.yml").read_text()
    for eco in ("pip", "gradle", "github-actions"):
        assert f"package-ecosystem: {eco}" in dep
