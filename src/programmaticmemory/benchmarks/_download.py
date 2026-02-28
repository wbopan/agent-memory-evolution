"""Shared download utilities for benchmark datasets."""

from __future__ import annotations

import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable


def _repo_root() -> Path:
    """Walk up from this file to find the repository root (contains pyproject.toml)."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def get_data_dir(subdir: str, data_dir: str | Path | None = None) -> Path:
    """Return ``{repo_root}/data/{subdir}/``, creating it if needed."""
    base = Path(data_dir) if data_dir else _repo_root() / "data"
    out = base / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


def download_file(url: str, dest: Path, *, skip_if_exists: bool = True) -> Path:
    """Download *url* to *dest*, skipping if the file already exists and is non-empty."""
    if skip_if_exists and dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    return dest


def download_and_extract_tar(
    url: str,
    dest_dir: Path,
    *,
    members_filter: Callable[[tarfile.TarInfo], bool] | None = None,
    skip_if_exists: bool = True,
) -> Path:
    """Download a tar.gz archive, extract (optionally filtered), then delete the archive."""
    tar_path = dest_dir / "archive.tar.gz"
    if skip_if_exists and any(dest_dir.iterdir()):
        return dest_dir
    download_file(url, tar_path, skip_if_exists=False)
    with tarfile.open(tar_path) as tf:
        if members_filter is not None:
            members = [m for m in tf.getmembers() if members_filter(m)]
        else:
            members = tf.getmembers()
        tf.extractall(dest_dir, members=members)
    tar_path.unlink()
    return dest_dir


def download_and_extract_zip(
    url: str,
    dest_dir: Path,
    *,
    members_filter: Callable[[str], bool] | None = None,
    skip_if_exists: bool = True,
) -> Path:
    """Download a zip archive, extract (optionally filtered), then delete the archive."""
    zip_path = dest_dir / "archive.zip"
    if skip_if_exists and any(dest_dir.iterdir()):
        return dest_dir
    download_file(url, zip_path, skip_if_exists=False)
    with zipfile.ZipFile(zip_path) as zf:
        if members_filter is not None:
            members = [m for m in zf.namelist() if members_filter(m)]
            zf.extractall(dest_dir, members=members)
        else:
            zf.extractall(dest_dir)
    zip_path.unlink()
    return dest_dir
