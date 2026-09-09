#!/usr/bin/env python3
"""Build dist/solr-doc-meta-backfill (+ zip) for DevOps. Part 3 only.

  python build_solr_backfill_dist.py              # source (.py, cần Python trên server)
  python build_solr_backfill_dist.py --standalone # binary OS hiện tại (không cần Python)
  python build_solr_backfill_dist.py --linux      # binary Linux amd64 qua Docker (PRD)
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_ROOT = ROOT / "dist"
PACKAGE_NAME = "solr-doc-meta-backfill"
PACKAGE_DIR = DIST_ROOT / PACKAGE_NAME
PACKAGING = ROOT / "packaging"
BINARY_NAME = PACKAGE_NAME

PYTHON_FILES = [
    "solr_doc_meta_backfill.py",
    "solr_backfill_config.py",
    "solr_backfill_db.py",
    "solr_backfill_solr.py",
    "solr_backfill_text.py",
    "solr_backfill_report.py",
]


def _copy(src: Path, dest: Path) -> None:
    if not src.is_file():
        raise SystemExit(f"Missing source file: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _stage_python(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in PYTHON_FILES:
        _copy(ROOT / name, dest / name)


def _copy_runtime_docs(dest: Path, *, standalone: bool) -> None:
    readme = PACKAGING / "README.standalone.md" if standalone else PACKAGING / "README.md"
    _copy(readme, dest / "README.md")
    _copy(ROOT / ".env.example", dest / ".env.example")
    _copy(ROOT / "new_oracle_tenants.example.json", dest / "new_oracle_tenants.example.json")
    if not standalone:
        _copy(ROOT / "requirements.txt", dest / "requirements.txt")


def _zip_package() -> Path:
    zip_path = DIST_ROOT / f"{PACKAGE_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            if not path.is_file():
                continue
            arcname = Path(PACKAGE_NAME) / path.relative_to(PACKAGE_DIR)
            info = zipfile.ZipInfo(str(arcname).replace("\\", "/"))
            info.compress_type = zipfile.ZIP_DEFLATED
            mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
            info.date_time = (mtime.year, mtime.month, mtime.day, mtime.hour, mtime.minute, mtime.second)
            if path.stat().st_mode & stat.S_IXUSR:
                info.external_attr = 0o755 << 16
            else:
                info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return zip_path


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        req = PACKAGING / "requirements-build.txt"
        print(f"Installing PyInstaller from {req} ...", flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(req)],
            cwd=ROOT,
        )


def _pyinstaller_cmd(work_dir: Path, *, onefile: bool) -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--name",
        BINARY_NAME,
        "--collect-all",
        "oracledb",
        "--collect-all",
        "openpyxl",
        "--hidden-import",
        "dotenv",
        "--distpath",
        str(work_dir / "pyi-dist"),
        "--workpath",
        str(work_dir / "pyi-work"),
        "--specpath",
        str(work_dir),
        str(work_dir / "solr_doc_meta_backfill.py"),
    ]


def _copy_pyinstaller_output(pyi_dist: Path, dest: Path, *, onefile: bool) -> None:
    if onefile:
        candidates = [pyi_dist / BINARY_NAME, pyi_dist / f"{BINARY_NAME}.exe"]
        binary = next((p for p in candidates if p.is_file()), None)
        if binary is None:
            raise SystemExit(f"PyInstaller onefile output not found in {pyi_dist}")
        target = dest / binary.name
        shutil.copy2(binary, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return

    folder = pyi_dist / BINARY_NAME
    if not folder.is_dir():
        raise SystemExit(f"PyInstaller onedir output not found: {folder}")
    for item in folder.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
            if item.name in {BINARY_NAME, f"{BINARY_NAME}.exe"}:
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build_standalone_local(*, onefile: bool) -> None:
    _ensure_pyinstaller()
    work = DIST_ROOT / "_pyi_src"
    if work.exists():
        shutil.rmtree(work)
    _stage_python(work)
    print("Running PyInstaller (this OS) ...", flush=True)
    subprocess.check_call(_pyinstaller_cmd(work, onefile=onefile), cwd=work)
    _copy_pyinstaller_output(work / "pyi-dist", PACKAGE_DIR, onefile=onefile)
    shutil.rmtree(work, ignore_errors=True)
    spec = ROOT / f"{BINARY_NAME}.spec"
    if spec.is_file():
        spec.unlink()


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _build_linux_docker(*, onefile: bool) -> None:
    if not _docker_available():
        raise SystemExit(
            "Docker is required for --linux (Linux amd64 binary).\n"
            "Install Docker, or build on a Linux amd64 machine:\n"
            "  python build_solr_backfill_dist.py --standalone"
        )
    stage = DIST_ROOT / "_docker_src"
    if stage.exists():
        shutil.rmtree(stage)
    _stage_python(stage)
    _copy(ROOT / "requirements.txt", stage / "requirements.txt")
    _copy(PACKAGING / "requirements-build.txt", stage / "requirements-build.txt")
    _copy(PACKAGING / "Dockerfile", stage / "Dockerfile")
    out = DIST_ROOT / "_docker_out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    print("Docker build Linux amd64 (PyInstaller) ...", flush=True)
    cmd = [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-f",
        str(stage / "Dockerfile"),
        "--build-arg",
        f"ONEFILE={'1' if onefile else '0'}",
        "--output",
        str(out),
        str(stage),
    ]
    try:
        subprocess.check_call(cmd, cwd=ROOT)
    except subprocess.CalledProcessError as ex:
        raise SystemExit(
            "Docker build failed. Need Docker Buildx (docker build --output).\n"
            f"Command: {' '.join(cmd)}"
        ) from ex
    if onefile:
        binary = out / BINARY_NAME
        if not binary.is_file():
            raise SystemExit(f"Linux binary missing in docker output: {out}")
        target = PACKAGE_DIR / BINARY_NAME
        shutil.copy2(binary, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    else:
        folder = out / BINARY_NAME
        src = folder if folder.is_dir() else out
        for item in src.iterdir():
            target = PACKAGE_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
                if item.name == BINARY_NAME:
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Part 3 dist zip for DevOps.")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="PyInstaller binary for this OS (no Python on the target)",
    )
    parser.add_argument(
        "--linux",
        action="store_true",
        help="Linux amd64 binary via Docker (use this for PRD). Implies --standalone",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="With --standalone/--linux: folder + libs (safer if /tmp is noexec). Default is one file",
    )
    args = parser.parse_args()
    standalone = args.standalone or args.linux
    onefile = standalone and not args.onedir

    if DIST_ROOT.exists():
        shutil.rmtree(DIST_ROOT)
    PACKAGE_DIR.mkdir(parents=True)

    if args.linux:
        _build_linux_docker(onefile=onefile)
    elif args.standalone:
        _build_standalone_local(onefile=onefile)
    else:
        _stage_python(PACKAGE_DIR)

    _copy_runtime_docs(PACKAGE_DIR, standalone=standalone)
    zip_path = _zip_package()

    print(f"Package: {PACKAGE_DIR}")
    print(f"Zip:     {zip_path}")
    if args.linux:
        print("Linux amd64 binary. DevOps: unzip, cp .env.example .env, ./solr-doc-meta-backfill")
    elif args.standalone:
        print(
            f"Standalone binary for {sys.platform}. "
            "PRD Linux needs: python build_solr_backfill_dist.py --linux"
        )
    else:
        print("Source package (Python required). For no-Python PRD: python build_solr_backfill_dist.py --linux")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
