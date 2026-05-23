import hashlib
import os
import platform
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import total_ordering
from io import BufferedWriter, BytesIO
from pathlib import Path
from typing import Any, Literal, Optional, TypeAlias
from zipfile import ZipFile

import orjson
import requests
from platformdirs import user_cache_dir
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from yaml import CLoader, load

from .__version__ import installed_library_version
from .assets import get_asset_by_name
from .exceptions import (
    MissingRelease,
    RotundaNotInstalled,
    UnsupportedArchitecture,
    UnsupportedOS,
    UnsupportedVersion,
)

DownloadBuffer: TypeAlias = BytesIO | tempfile._TemporaryFileWrapper | BufferedWriter

ARCH_MAP: dict[str, str] = {
    'amd64': 'x86_64',
    'x86_64': 'x86_64',
    'x86': 'x86_64',
    'i686': 'i686',
    'i386': 'i686',
    'arm64': 'arm64',
    'aarch64': 'arm64',
    'armv5l': 'arm64',
    'armv6l': 'arm64',
    'armv7l': 'arm64',
}
OS_MAP: dict[str, Literal['mac', 'win', 'lin']] = {'darwin': 'mac', 'linux': 'lin', 'win32': 'win'}

if sys.platform not in OS_MAP:
    raise UnsupportedOS(f"OS {sys.platform} is not supported")

OS_NAME: Literal['mac', 'win', 'lin'] = OS_MAP[sys.platform]

INSTALL_DIR: Path = Path(user_cache_dir("rotunda"))

OS_ARCH_MATRIX: dict[str, list[str]] = {
    'win': ['x86_64'],
    'mac': ['x86_64', 'arm64'],
    'lin': ['x86_64', 'arm64'],
}

LAUNCH_FILE = {
    'win': 'rotunda.exe',
    'mac': '../MacOS/rotunda',
    'lin': 'rotunda-bin',
}

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

console = Console()


def rprint(msg: str, fg: str | None = None, nl: bool = True) -> None:
    """
    Print a styled message
    """
    style = f"bold {fg}" if fg else "bold"
    console.print(msg, style=style, end="\n" if nl else "", highlight=False)


@dataclass
class RepoConfig:
    """
    Configuration for a Rotunda repository
    """

    repos: list[str]  # Primary + fallback GitHub repos
    name: str
    pattern: str
    os_map: dict[str, Literal['mac', 'win', 'lin']]
    arch_map: dict[str, str]
    python_library_version: str = field(default_factory=installed_library_version)

    @property
    def repo(self) -> str:
        """
        Primary GitHub repo
        """
        return self.repos[0]

    @staticmethod
    def load_repos(spoof_library_version: str | None = None) -> list['RepoConfig']:
        """
        Load repository configurations from repos.yml
        """
        with open(get_asset_by_name('repos.yml')) as f:
            data = load(f, Loader=CLoader)
        return [RepoConfig.from_dict(r, spoof_library_version) for r in data.get('browsers', [])]

    @staticmethod
    def get_default_name() -> str:
        """
        Get the default repo name from repos.yml
        """
        with open(get_asset_by_name('repos.yml')) as f:
            data = load(f, Loader=CLoader)
        return data.get('default', {}).get('browser', 'Official')

    @staticmethod
    def from_dict(d: dict[str, Any], spoof_library_version: str | None = None) -> 'RepoConfig':
        """
        Create RepoConfig from dictionary
        """
        if 'pattern' not in d:
            raise ValueError(f"Repo '{d.get('name', 'unknown')}' missing required pattern")

        library_version = spoof_library_version or installed_library_version()

        # Parse comma separated repos list (primary + fallbacks)
        raw_repo = d['repo']
        repos = [r.strip() for r in raw_repo.split(',')] if isinstance(raw_repo, str) else raw_repo

        return RepoConfig(
            repos=repos,
            name=d['name'],
            pattern=d['pattern'],
            os_map=OS_MAP,
            arch_map=ARCH_MAP,
            python_library_version=library_version,
        )

    @staticmethod
    def get_default() -> 'RepoConfig':
        """
        Get the default repository config
        """
        default_name = RepoConfig.get_default_name()
        repo = RepoConfig.find_by_name(default_name)
        if repo:
            return repo
        return RepoConfig.load_repos()[0]

    @staticmethod
    def find_by_name(name: str) -> Optional['RepoConfig']:
        """
        Find a repo config by name (case-insensitive)
        """
        name_lower = name.lower()
        for repo in RepoConfig.load_repos():
            if repo.name.lower() == name_lower:
                return repo
        return None

    def get_os_name(self, spoof_os: str | None = None) -> str:
        """
        Get the mapped OS name
        """
        if spoof_os:
            return spoof_os
        os_name = self.os_map.get(sys.platform)
        if not os_name:
            raise UnsupportedOS(f"OS {sys.platform} is not supported")
        return os_name

    def get_arch(self, spoof_arch: str | None = None) -> str:
        """
        Get the mapped architecture
        """
        if spoof_arch:
            return spoof_arch
        plat_arch = platform.machine().lower()
        arch = self.arch_map.get(plat_arch)
        if not arch:
            raise UnsupportedArchitecture(f"Architecture {plat_arch} is not supported")
        return arch

    def build_pattern(
        self, spoof_os: str | None = None, spoof_arch: str | None = None
    ) -> re.Pattern:
        """
        Build asset regex from the config pattern string
        """
        replacements = {
            'name': r'(?P<name>\w+)',
            'version': r'(?P<version>[^-]+)',
            'build': r'(?P<build>[^-]+)',
            'os': re.escape(self.get_os_name(spoof_os)),
            'arch': re.escape(self.get_arch(spoof_arch)),
        }
        pattern = self.pattern.replace('.', r'\.')
        regex = re.sub(r'\{(\w+)\}', lambda m: replacements.get(m[1], m[0]), pattern)
        return re.compile(regex)

    def is_version_supported(self, version: 'Version') -> bool:
        """
        Check if a browser asset belongs to this installed Python package version.
        """
        return version.version == self.python_library_version


@total_ordering
@dataclass
class Version:
    """
    A comparable version string (up to 5 parts)
    """

    build: str
    version: str | None = None

    def __post_init__(self) -> None:
        self.sorted_rel = tuple(
            [
                *(int(x) if x.isdigit() else ord(x[0]) - 1024 for x in self.build.split('.')),
                *(0 for _ in range(5 - self.build.count('.'))),
            ]
        )

    @property
    def full_string(self) -> str:
        return f"{self.version}-{self.build}"

    def __eq__(self, other) -> bool:
        return self.sorted_rel == other.sorted_rel

    def __lt__(self, other) -> bool:
        return self.sorted_rel < other.sorted_rel

    def is_supported(self) -> bool:
        return self.version == installed_library_version()

    @staticmethod
    def from_path(path: Path | None = None) -> 'Version':
        """
        Get the version from version.json at the given path
        """
        version_path = (path or INSTALL_DIR) / 'version.json'
        if not os.path.exists(version_path):
            raise FileNotFoundError(
                f"Version information not found at {version_path}. "
                "Please run `rotunda fetch` to install."
            )
        with open(version_path, 'rb') as f:
            version_data = orjson.loads(f.read())
            if 'release' in version_data:
                version_data['build'] = version_data.pop('release')
            elif 'tag' in version_data:
                version_data['build'] = version_data.pop('tag')
            return Version(
                build=version_data['build'],
                version=version_data.get('version'),
            )

    @staticmethod
    def is_supported_path(path: Path) -> bool:
        """
        Check if the version at the given path is supported
        """
        return Version.from_path(path).is_supported()


class GitHubDownloader:
    """
    Manages fetching GitHub releases with fallback repos
    """

    def __init__(self, github_repos: str | list[str]) -> None:
        if isinstance(github_repos, str):
            github_repos = [github_repos]
        self.github_repos = github_repos
        self.github_repo = github_repos[0]
        self.is_prerelease: bool = False

    def check_asset(self, asset: dict[str, Any], release: dict[str, Any] | None = None) -> Any:
        """
        Return truthy data if this is the desired asset, else None
        """
        return asset.get('browser_download_url')

    def missing_asset_error(self) -> None:
        """
        Raise a MissingRelease exception
        """
        raise MissingRelease(f"Could not find a release asset in {self.github_repo}.")

    def _get_releases(self, github_repo: str) -> list[dict]:
        """
        Fetch releases from a single GitHub repo
        """
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
        api_url = f"https://api.github.com/repos/{github_repo}/releases"
        resp = requests.get(api_url, timeout=20, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_asset(self) -> Any:
        """
        Fetch the first matching release asset, trying fallback repos on failure
        """
        last_error = None
        for repo in self.github_repos:
            try:
                releases = self._get_releases(repo)
                for release in releases:
                    for asset in release['assets']:
                        if data := self.check_asset(asset, release):
                            self.github_repo = repo
                            self.is_prerelease = release.get('prerelease', False)
                            return data
            except Exception as e:
                last_error = e
                continue

        if last_error:
            raise last_error
        self.missing_asset_error()
        raise AssertionError("unreachable")


@dataclass
class AvailableVersion:
    """
    Information about an available Rotunda version from GitHub
    """

    version: Version
    url: str
    is_prerelease: bool
    # GitHub metadata for tracking changes
    asset_id: int | None = None
    asset_size: int | None = None
    asset_updated_at: str | None = None
    # Optional pinned SHA-256 checksum for integrity verification
    expected_sha256: str | None = None

    @property
    def display(self) -> str:
        """
        Display string for the version
        """
        pre = " (prerelease)" if self.is_prerelease else ""
        return f"v{self.version.full_string}{pre}"

    def to_metadata(self) -> dict[str, Any]:
        """
        Return metadata dict for storing in version.json
        """
        return {
            'version': self.version.version,
            'build': self.version.build,
            'prerelease': self.is_prerelease,
            'asset_id': self.asset_id,
            'asset_size': self.asset_size,
            'asset_updated_at': self.asset_updated_at,
        }


class RotundaFetcher(GitHubDownloader):
    """
    Handles fetching and installing Rotunda
    """

    def __init__(
        self,
        repo_config: RepoConfig | None = None,
        selected_version: AvailableVersion | None = None,
    ) -> None:
        self.repo_config = repo_config or RepoConfig.get_default()
        super().__init__(self.repo_config.repos)

        self.arch = self.get_platform_arch()
        self._version_obj: Version | None = None
        self._selected_version: AvailableVersion | None = None
        self.pattern: re.Pattern = self.repo_config.build_pattern()

        if selected_version:
            self._selected_version = selected_version
            self._version_obj = selected_version.version
            self._url = selected_version.url
            self.is_prerelease = selected_version.is_prerelease
        else:
            self.fetch_latest()

    def check_asset(
        self, asset: dict, release: dict | None = None
    ) -> tuple[Version, str] | None:
        """
        Match a release asset against version constraints, OS, and arch
        """
        match = self.pattern.match(asset['name'])
        if not match:
            return None

        version = Version(build=match['build'], version=match['version'])
        if not self.repo_config.is_version_supported(version):
            return None

        return version, asset['browser_download_url']

    def missing_asset_error(self) -> None:
        raise MissingRelease(
            f"No matching release found for Rotunda {self.repo_config.python_library_version} "
            f"on {OS_NAME} {self.arch}. Please update the Python library."
        )

    def get_platform_arch(self) -> str:
        """
        Get the current platform architecture
        """
        arch = self.repo_config.get_arch()
        if arch not in OS_ARCH_MATRIX[OS_NAME]:
            raise UnsupportedArchitecture(f"Architecture {arch} is not supported for {OS_NAME}")

        return arch

    def fetch_latest(self) -> None:
        """
        Fetch the latest rotunda release for the current platform
        """
        self._version_obj, self._url = self.get_asset()

    @staticmethod
    def download_file(file: DownloadBuffer, url: str, *, expected_sha256: str | None = None) -> DownloadBuffer:
        """
        Download a file from the given URL.
        If expected_sha256 is provided, verifies the integrity of the downloaded file.
        Raises ValueError if the checksum does not match.
        """
        rprint(f'Downloading package: {url}')
        result = webdl(url, buffer=file)
        # Compute SHA-256 of downloaded content
        result.seek(0)
        sha256 = hashlib.sha256(result.read()).hexdigest()
        result.seek(0)
        if expected_sha256:
            if sha256.lower() != expected_sha256.lower():
                raise ValueError(
                    f"Binary integrity check FAILED for {url}\n"
                    f"  Expected SHA-256: {expected_sha256}\n"
                    f"  Actual  SHA-256: {sha256}\n"
                    "Download aborted — the file may be tampered or corrupted."
                )
            rprint(f"[green]SHA-256 verified: {sha256}[/green]")
        else:
            rprint(
                f"[yellow]WARNING: No expected SHA-256 provided for {url}.\n"
                f"         Downloaded file SHA-256: {sha256}\n"
                "         Pin this checksum to enable integrity verification.[/yellow]"
            )
        return result

    def extract_zip(self, zip_file: DownloadBuffer) -> None:
        """
        Extract a zip file to the installation directory
        """
        rprint(f'Extracting Rotunda: {INSTALL_DIR}')
        unzip(zip_file, str(INSTALL_DIR))

    @staticmethod
    def cleanup() -> bool:
        """
        Clean up the old installation
        """
        if INSTALL_DIR.exists():
            rprint(f'Cleaning up cache: {INSTALL_DIR}')
            shutil.rmtree(INSTALL_DIR)
            return True
        return False

    def set_version(self) -> None:
        """
        Write version.json to INSTALL_DIR
        """
        with open(INSTALL_DIR / 'version.json', 'wb') as f:
            f.write(orjson.dumps({'version': self.version, 'build': self.build}))

    def install(self, replace: bool = False) -> None:
        """
        Download and install rotunda to a versioned subdirectory
        """
        from .multiversion import install_versioned

        install_versioned(self, replace=replace)

    @property
    def url(self) -> str:
        if self._url is None:
            raise ValueError("Url is not available. Make sure to run fetch_latest first.")
        return self._url

    @property
    def version(self) -> str:
        if self._version_obj is None or not self._version_obj.version:
            raise ValueError("Version is not available. Make sure to run the fetch_latest first.")

        return self._version_obj.version

    @property
    def build(self) -> str:
        if self._version_obj is None:
            raise ValueError(
                "Build information is not available. Make sure to run the installation first."
            )

        return self._version_obj.build

    @property
    def verstr(self) -> str:
        if self._version_obj is None:
            raise ValueError("Version is not available. Make sure to run the installation first.")
        return self._version_obj.full_string


def list_available_versions(
    repo_config: RepoConfig | None = None,
    include_prerelease: bool = True,
    spoof_os: str | None = None,
    spoof_arch: str | None = None,
) -> list[AvailableVersion]:
    """
    Fetch all supported versions from GitHub for the current platform
    """
    config = repo_config or RepoConfig.get_default()
    pattern = config.build_pattern(spoof_os=spoof_os, spoof_arch=spoof_arch)

    os_name = spoof_os or OS_NAME
    arch = config.get_arch(spoof_arch)
    if arch not in OS_ARCH_MATRIX.get(os_name, []):
        raise UnsupportedArchitecture(f"Architecture {arch} is not supported for {os_name}")

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    releases = []
    last_error = None
    for repo in config.repos:
        try:
            api_url = f"https://api.github.com/repos/{repo}/releases"
            resp = requests.get(api_url, timeout=20, headers=headers)
            resp.raise_for_status()
            releases = resp.json()
            break
        except Exception as e:
            last_error = e
            continue
    if not releases and last_error:
        raise last_error

    versions: list[AvailableVersion] = []
    seen_builds: set = set()

    for release in releases:
        is_prerelease = release.get('prerelease', False)

        if is_prerelease and not include_prerelease:
            continue

        for asset in release['assets']:
            match = pattern.match(asset['name'])
            if not match:
                continue

            version = Version(build=match['build'], version=match['version'])
            if not config.is_version_supported(version):
                continue

            if version.build in seen_builds:
                continue
            seen_builds.add(version.build)

            versions.append(
                AvailableVersion(
                    version=version,
                    url=asset['browser_download_url'],
                    is_prerelease=is_prerelease,
                    asset_id=asset.get('id'),
                    asset_size=asset.get('size'),
                    asset_updated_at=asset.get('updated_at'),
                )
            )

    versions.sort(key=lambda x: x.version, reverse=True)
    return versions


def installed_verstr() -> str:
    """
    Get the full version string of the active install
    """
    from .multiversion import get_active_path

    active = get_active_path()
    if active is None:
        from .multiversion import get_default_channel, load_config

        config = load_config()
        pinned = config.get("pinned")
        channel = config.get("channel") or get_default_channel()
        active_display = f"{channel}/{pinned}" if pinned else channel
        raise RotundaNotInstalled(
            f"{active_display} is not installed. " f"Please run `rotunda fetch` to install."
        )
    return Version.from_path(active).full_string


def rotunda_path(download_if_missing: bool = True) -> Path:
    """
    Full path to the active rotunda folder
    """
    from .multiversion import COMPAT_FLAG, get_active_path

    # Clean up incompatible old data directory
    if os.path.exists(INSTALL_DIR) and os.listdir(INSTALL_DIR) and not COMPAT_FLAG.exists():
        import shutil

        rprint("Cleaning old data...", fg="yellow")
        shutil.rmtree(INSTALL_DIR)

    active = get_active_path()
    if active and Version.from_path(active).is_supported():
        return active

    if not os.path.exists(INSTALL_DIR) or not os.listdir(INSTALL_DIR):
        if not download_if_missing:
            from .multiversion import get_default_channel, load_config

            config = load_config()
            pinned = config.get("pinned")
            channel = config.get("channel") or get_default_channel()
            active_display = f"{channel}/{pinned}" if pinned else channel
            raise RotundaNotInstalled(
                f"{active_display} is not installed. " f"Please run `rotunda fetch` to install."
            )

    elif os.path.exists(INSTALL_DIR) and Version.from_path().is_supported():
        return INSTALL_DIR

    else:
        if not download_if_missing:
            raise UnsupportedVersion("Rotunda executable is outdated.")

    RotundaFetcher().install()
    return rotunda_path()


def get_path(file: str) -> str:
    """
    Get the path to a file in the rotunda directory
    """
    if OS_NAME == 'mac':
        return os.path.abspath(rotunda_path() / 'Rotunda.app' / 'Contents' / 'Resources' / file)
    return str(rotunda_path() / file)


def launch_path(browser_path: Path | None = None) -> str:
    """
    Get the path to the rotunda executable
    """
    if browser_path:
        if OS_NAME == 'mac':
            exec_path = os.path.abspath(
                browser_path / 'Rotunda.app' / 'Contents' / 'Resources' / LAUNCH_FILE[OS_NAME]
            )
        else:
            exec_path = str(browser_path / LAUNCH_FILE[OS_NAME])
    else:
        exec_path = get_path(LAUNCH_FILE[OS_NAME])

    if not os.path.exists(exec_path):
        raise RotundaNotInstalled(
            f"Rotunda is not installed at {browser_path or rotunda_path()}. Please run `rotunda fetch` to install."
        )
    return exec_path


ProgressCallback: TypeAlias = Callable[[int, int], None]


def webdl(
    url: str,
    desc: str | None = None,
    buffer: DownloadBuffer | None = None,
    bar: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> DownloadBuffer:
    """
    Download a file from the given URL
    """
    headers = (
        {"Authorization": f"Bearer {GITHUB_TOKEN}"} if "api.github" in url and GITHUB_TOKEN else {}
    )
    response = requests.get(url, stream=True, headers=headers)
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    block_size = 8192
    if buffer is None:
        buffer = BytesIO()

    if progress_callback:
        downloaded = 0
        last_update = 0
        for data in response.iter_content(block_size * 4):
            size = buffer.write(data)
            downloaded += size
            if downloaded - last_update >= 65536 or downloaded == total_size:
                progress_callback(downloaded, total_size)
                last_update = downloaded
    elif bar:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(desc or "Downloading", total=total_size)
            for data in response.iter_content(block_size):
                size = buffer.write(data)
                progress.update(task, advance=size)
    else:
        downloaded = 0
        for data in response.iter_content(block_size):
            size = buffer.write(data)
            downloaded += size
            if total_size:
                pct = (downloaded / total_size) * 100
                print(f"\r{desc}: {pct:.0f}%", end="", flush=True)
        print(f"\r{desc}: Complete" if desc else "")

    buffer.seek(0)
    return buffer


def unzip(
    zip_file: DownloadBuffer,
    extract_path: str,
    desc: str | None = None,
    bar: bool = True,
) -> None:
    """
    Extract a zip file to the given path
    """
    with ZipFile(zip_file) as zf:
        members = zf.infolist()
        if bar:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(desc or "Extracting", total=len(members))
                for member in members:
                    zf.extract(member, extract_path)
                    progress.update(task, advance=1)
        else:
            for i, member in enumerate(members):
                zf.extract(member, extract_path)
                if desc:
                    pct = ((i + 1) / len(members)) * 100
                    print(f"\r{desc}: {pct:.0f}%", end="", flush=True)
            if desc:
                print(f"\r{desc}: Complete")


def load_yaml(file: str) -> dict[str, Any]:
    """
    Load a local YAML file as a dictionary
    """
    with open(get_asset_by_name(file)) as f:
        return load(f, Loader=CLoader)
