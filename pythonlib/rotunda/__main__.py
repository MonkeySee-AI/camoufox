"""
CLI package manager for Rotunda
"""

import tempfile
import uuid
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from os import environ
from pathlib import Path
from typing import Any

import rich_click as click

from .addons import DefaultAddons, maybe_download_addons
from .geo.geolocation import (
    ALLOW_GEOIP,
    GEOIP_DIR,
    _load_geoip_repos,
    download_mmdb,
    get_mmdb_path,
    load_geoip_config,
    save_geoip_config,
)
from .multiversion import (
    BROWSERS_DIR,
    COMPAT_FLAG,
    CONFIG_FILE,
    REPO_CACHE_FILE,
    InstalledVersion,
    get_default_channel,
    list_installed,
    load_config,
    load_repo_cache,
    print_tree,
    remove_version,
    save_config,
    save_repo_cache,
    set_active,
)
from .pkgman import (
    INSTALL_DIR,
    AvailableVersion,
    RepoConfig,
    RotundaFetcher,
    console,
    installed_verstr,
    list_available_versions,
    rprint,
)
from .settings import RotundaSettings


def _inquirer_select(
    choices: list[tuple[str, Any]],
    message: str,
) -> Any | None:
    """
    Generic inquirer selection. Returns selected value or None
    """
    import inquirer
    from inquirer.themes import GreenPassion

    try:
        result = inquirer.prompt(
            [inquirer.List("item", message=message, choices=choices, carousel=True)],
            theme=GreenPassion(),
        )
        return result["item"] if result else None
    except KeyboardInterrupt:
        return None


def _find_installed(specifier: str) -> InstalledVersion | None:
    """
    Find installed version by channel path, build, or full version string
    """
    spec = specifier.lower()
    installed = list_installed()

    parts = spec.split("/")

    for v in installed:
        if any(
            [
                v.channel_path.lower() == spec,
                v.relative_path.lower() == spec,
                v.version.build.lower() == spec,
                v.version.full_string.lower() == spec,
            ]
        ):
            return v
        # Match repo/version without channel for example official/134.0.2-beta.20
        if len(parts) == 2:
            repo, ver = parts
            if v.repo_name == repo and v.version.full_string.lower() == ver:
                return v

    # Match repo/channel for example official/stable gets latest installed for that channel
    if len(parts) == 2:
        repo, ctype = parts
        if ctype in ("stable", "prerelease"):
            is_pre = ctype == "prerelease"
            for v in installed:
                if v.repo_name == repo and v.is_prerelease == is_pre:
                    return v

    return None


def _get_geoip_source_name() -> str:
    """
    Get the name of the active GeoIP source
    """
    try:
        return load_geoip_config().get("name", "Default")
    except Exception:
        return "Default"


def _do_sync(spoof_os=None, spoof_arch=None) -> bool:
    """
    Sync available versions from remote repositories. Returns True on success
    """
    rprint("Syncing repositories...", fg="yellow")

    cache = {"repos": [], "spoof_os": spoof_os, "spoof_arch": spoof_arch}

    for repo_config in RepoConfig.load_repos():
        rprint(f"  {repo_config.name}...", fg="cyan", nl=False)
        try:
            versions = list_available_versions(
                repo_config=repo_config,
                include_prerelease=True,
                spoof_os=spoof_os,
                spoof_arch=spoof_arch,
            )
            repo_data = {
                "name": repo_config.name,
                "repo": repo_config.repo,
                "python_library": repo_config.python_library_version,
                "versions": [
                    {
                        "version": v.version.version,
                        "build": v.version.build,
                        "url": v.url,
                        "is_prerelease": v.is_prerelease,
                        "asset_id": v.asset_id,
                        "asset_size": v.asset_size,
                        "asset_updated_at": v.asset_updated_at,
                    }
                    for v in versions
                ],
            }
            cache["repos"].append(repo_data)
            rprint(f" {len(versions)} versions", fg="green")
        except Exception as e:
            rprint(f" Error: {e}", fg="red")

    save_repo_cache(cache)
    total = sum(len(r["versions"]) for r in cache["repos"])
    platform_str = f" ({spoof_os}/{spoof_arch})" if spoof_os else ""
    rprint(
        f"\nSynced {total} versions from {len(cache['repos'])} repos{platform_str}.",
        fg="green",
    )

    return True


def _ensure_synced() -> bool:
    """
    Ensure repo cache exists. Returns True if synced, False if not
    """
    if not REPO_CACHE_FILE.exists():
        rprint("No repo cache found. Run 'rotunda sync' first.", fg="red")
        return False
    return True


class RotundaUpdate(RotundaFetcher):
    """
    Checks & updates Rotunda
    """

    def __init__(
        self,
        repo_config: RepoConfig | None = None,
        selected_version: AvailableVersion | None = None,
    ) -> None:
        super().__init__(repo_config=repo_config, selected_version=selected_version)
        try:
            self.current_verstr = installed_verstr()
        except FileNotFoundError:
            self.current_verstr = None

    def is_updated_needed(self) -> bool:
        return self.current_verstr is None or self.current_verstr != self.verstr

    def update(self, replace: bool = False, i_know_what_im_doing: bool = False) -> None:
        if not self.is_updated_needed() and not replace:
            rprint("Rotunda binaries up to date!", fg="green")
            rprint(f"Current version: v{self.current_verstr}", fg="green")
            return

        if self.is_prerelease and not i_know_what_im_doing:
            rprint(f"Warning: v{self.verstr} is a prerelease version!", fg="yellow")
            if not click.confirm("Continue with prerelease installation?"):
                rprint("Installation cancelled.", fg="red")
                return

        action = "Installing" if self.current_verstr else "Fetching"
        rprint(f"{action} Rotunda v{self.verstr}...", fg="yellow")
        self.install(replace=replace)


@click.group()
def cli() -> None:
    pass


@cli.command(name="sync")
@click.option(
    "--spoof-os",
    type=click.Choice(["auto", "mac", "win", "lin"]),
    help="Spoof OS (auto = native)",
)
@click.option(
    "--spoof-arch",
    type=click.Choice(["auto", "x86_64", "arm64"]),
    help="Spoof architecture (auto = native)",
)
def sync(spoof_os, spoof_arch):
    """
    Sync available versions from remote repositories
    """
    if spoof_os == "auto":
        spoof_os = None
    if spoof_arch == "auto":
        spoof_arch = None
    _do_sync(spoof_os=spoof_os, spoof_arch=spoof_arch)


@cli.command(name="fetch")
@click.argument("version", default=None, required=False)
def fetch(version):
    """
    Install the active version, or a specific version

    \b
    Examples:
      rotunda fetch                         # install active version
      rotunda fetch official/135.0-beta.25  # install specific version
    """
    # Clean up incompatible old data directory
    if INSTALL_DIR.exists() and any(INSTALL_DIR.iterdir()) and not COMPAT_FLAG.exists():
        import shutil

        rprint("Cleaning old data...", fg="yellow")
        shutil.rmtree(INSTALL_DIR)

    _do_sync()

    cache = load_repo_cache()
    config = load_config()

    if version:
        parts = version.split("/")
        if len(parts) == 3:
            # official/stable/135.0.1-beta.24
            repo_name = parts[0]
            ver_str = parts[2].lstrip("v")
        elif len(parts) == 2:
            # official/135.0.1-beta.24
            repo_name = parts[0]
            ver_str = parts[1].lstrip("v")
        else:
            rprint(
                "Format: <repo>/<version> or <repo>/<channel>/<version>",
                fg="red",
            )
            return
    elif config.get("pinned"):
        channel = config.get("channel", "")
        repo_name = channel.split("/")[0] if "/" in channel else channel
        ver_str = config["pinned"]
    else:
        channel = config.get("channel") or get_default_channel()
        if "/" in channel:
            repo_name, ctype = channel.split("/", 1)
        else:
            repo_name, ctype = channel, "stable"
        for repo_data in cache.get("repos", []):
            if repo_data["name"].lower() != repo_name.lower():
                continue
            versions = repo_data.get("versions", [])
            if ctype == "prerelease":
                candidates = [v for v in versions if v.get("is_prerelease")]
            else:
                candidates = [v for v in versions if not v.get("is_prerelease")]
            if candidates:
                ver_str = f"{candidates[0]['version']}-{candidates[0]['build']}"
                break
        else:
            rprint(f"No versions found for channel '{channel}'.", fg="red")
            return

    for repo_data in cache.get("repos", []):
        if repo_data["name"].lower() != repo_name.lower():
            continue
        for v in repo_data["versions"]:
            if f"{v['version']}-{v['build']}" == ver_str:
                from .pkgman import Version

                selected = AvailableVersion(
                    version=Version(v["build"], v["version"]),
                    url=v["url"],
                    is_prerelease=v.get("is_prerelease", False),
                )
                repo_config = RepoConfig.find_by_name(repo_data["name"])
                try:
                    RotundaUpdate(repo_config=repo_config, selected_version=selected).update()
                except Exception as e:
                    msg = str(e)
                    if "404" in msg or "Not Found" in msg:
                        rprint(
                            "Release not found (404). Asset may have been removed.",
                            fg="red",
                        )
                        rprint(
                            "Run 'rotunda sync' to refresh available versions.",
                            fg="yellow",
                        )
                    else:
                        rprint(f"Error: {msg}", fg="red")
                    return
                if ALLOW_GEOIP:
                    download_mmdb()
                maybe_download_addons(list(DefaultAddons))
                return

    rprint(f"Version '{version or ver_str}' not found in cache.", fg="red")


def _set_channel(repo_name: str, channel_type: str):
    """
    Set to track a channel (fetches latest on fetch)
    """
    config = load_config()
    config["channel"] = f"{repo_name}/{channel_type}"
    config.pop("pinned", None)

    # Check if latest for this channel is already installed
    is_pre = channel_type == "prerelease"
    cache = load_repo_cache()
    for repo_data in cache.get("repos", []):
        if repo_data["name"].lower() != repo_name.lower():
            continue
        versions = repo_data.get("versions", [])
        candidates = [v for v in versions if v.get("is_prerelease", False) == is_pre]
        if candidates:
            latest_build = candidates[0]["build"]
            for inst in list_installed():
                if inst.version.build == latest_build and inst.repo_name == repo_name.lower():
                    config["active_version"] = inst.relative_path
                    save_config(config)
                    click.secho(
                        f"Channel: {repo_name.lower()}/{channel_type}", fg="cyan", bold=True
                    )
                    click.secho(f"Using latest: {inst.channel_path} (installed)", fg="green")
                    return
        break

    config.pop("active_version", None)
    save_config(config)
    click.secho(f"Channel: {repo_name.lower()}/{channel_type}", fg="cyan", bold=True)
    click.secho("Run 'rotunda fetch' to install latest.", fg="yellow")


def _set_pinned(repo_name: str, channel_type: str, ver_data: dict, inst):
    """
    Pin to a specific version
    """
    config = load_config()
    config["channel"] = f"{repo_name}/{channel_type}"
    config["pinned"] = f"{ver_data['version']}-{ver_data['build']}"
    ver_str = f"{ver_data['version']}-{ver_data['build']}"
    display = f"{repo_name.lower()}/{channel_type}/{ver_str}"
    if inst:
        config["active_version"] = inst.relative_path
        save_config(config)
        click.secho(f"Pinned: {display} (installed)", fg="green")
    else:
        config.pop("active_version", None)
        save_config(config)
        click.secho(f"Pinned: {display}", fg="cyan", bold=True)
        click.secho("Run 'rotunda fetch' to install.", fg="yellow")


@cli.command(name="set")
@click.argument("specifier", required=False)
@click.option("--geoip", is_flag=True, help="Select GeoIP source instead")
def set_cmd(specifier, geoip):
    """
    \b
    Set the active Rotunda version to use & fetch.
    By default, this opens an interactive selector for versions and settings.
    You can also pass a specifier to activate directly:
    Pin version:
        rotunda set official/stable/134.0.2-beta.20
    Automatically find latest in a channel source:
        rotunda set official/stable
    """
    if geoip:
        _select_geoip_source()
        return

    if specifier:
        parts = specifier.lower().split("/")

        # 2-part: set channel (e.g. official/stable)
        if len(parts) == 2:
            repo_name, ctype = parts
            if ctype not in ("stable", "prerelease"):
                rprint(
                    f"Unknown channel type '{ctype}'. Use 'stable' or 'prerelease'.",
                    fg="red",
                )
                return
            _set_channel(repo_name, ctype)
            return

        # 3-part: pin version (e.g. official/stable/146.0.1-beta.25)
        if len(parts) == 3:
            repo_name, ctype, ver_str = parts
            if ctype not in ("stable", "prerelease"):
                rprint(
                    f"Unknown channel type '{ctype}'. Use 'stable' or 'prerelease'.",
                    fg="red",
                )
                return
            # Activate if already installed
            target = _find_installed(specifier)
            if target:
                set_active(target.relative_path)
                rprint(f"Pinned: {target.channel_path} (installed)", fg="green")
            else:
                click.secho(f"Pinned: {repo_name}/{ctype}/{ver_str}", fg="cyan", bold=True)
                rprint("Run 'rotunda fetch' to install.", fg="yellow")
            # Save pin config either way
            config = load_config()
            config["channel"] = f"{repo_name}/{ctype}"
            config["pinned"] = ver_str
            save_config(config)
            return

        rprint(f"Invalid specifier '{specifier}'.", fg="red")
        rprint("Use: repo/channel or repo/channel/version", fg="yellow")
        return

    if not _ensure_synced():
        return

    import inquirer
    from inquirer.themes import GreenPassion

    cache = load_repo_cache()
    installed = {v.version.build: v for v in list_installed()}

    if not cache.get("repos"):
        rprint("No versions in cache. Run 'rotunda sync' first.", fg="red")
        return

    channels = []
    for repo_data in cache["repos"]:
        name = repo_data["name"]
        versions = repo_data.get("versions", [])
        stable = [v for v in versions if not v.get("is_prerelease")]
        prereleases = [v for v in versions if v.get("is_prerelease")]
        if stable:
            channels.append((name, "stable", stable[0]))
        if prereleases:
            channels.append((name, "prerelease", prereleases[0]))

    config = load_config()
    channel = config.get("channel") or get_default_channel()
    pinned = config.get("pinned")

    if pinned:
        click.secho(f"Pinned: {channel.lower()}/{pinned}", fg="cyan")
    else:
        click.secho(f"Channel: {channel.lower()}", fg="cyan")
    click.echo()

    channel_versions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for repo_data in cache["repos"]:
        name = repo_data["name"]
        versions = repo_data.get("versions", [])
        stable = [v for v in versions if not v.get("is_prerelease")]
        prereleases = [v for v in versions if v.get("is_prerelease")]
        if stable:
            channel_versions[(name, "stable")] = stable
        if prereleases:
            channel_versions[(name, "prerelease")] = prereleases

    while True:
        choices: list[tuple[str, Any]] = [("Set channel", "channel")]
        for (name, ctype), versions in channel_versions.items():
            label = f"Pin version: {click.style(f'{name.lower()}/{ctype}', fg='cyan', bold=True)}"
            choices.append((label, ("pin", name, ctype, versions)))
        choices.append((click.style("Exit", fg="bright_black"), "exit"))

        answer = inquirer.prompt(
            [inquirer.List("action", message="Select", choices=choices, carousel=True)],
            theme=GreenPassion(),
        )
        if not answer:
            return

        action = answer["action"]

        if action == "exit":
            return

        if action == "channel":
            ch_choices = []
            for name, ctype, latest in channels:
                ver_str = f"v{latest['version']}-{latest['build']}"
                is_current = channel == f"{name}/{ctype}"
                label = f"{name.lower()}/{ctype} (latest: {ver_str})"
                if is_current:
                    label = click.style(label, fg="green", bold=True) + " (current)"
                ch_choices.append((label, (name, ctype, latest)))
            ch_choices.append((click.style("Back", fg="bright_black"), None))

            ch_answer = inquirer.prompt(
                [
                    inquirer.List(
                        "channel",
                        message="Set channel",
                        choices=ch_choices,
                        carousel=True,
                    )
                ],
                theme=GreenPassion(),
            )
            if not ch_answer or ch_answer["channel"] is None:
                continue

            repo_name, ctype, _ = ch_answer["channel"]
            _set_channel(repo_name, ctype)
            return

        if isinstance(action, tuple) and action[0] == "pin":
            _, rname, ctype, versions = action

            v_choices = []
            for i, v in enumerate(versions):
                build = v["build"]
                full_ver = f"{v['version']}-{build}"
                inst = installed.get(build)
                is_last = i == len(versions) - 1

                prefix = "└── " if is_last else "├── "

                is_pinned = pinned == full_ver
                if is_pinned and inst:
                    color = "green"
                    bold = True
                    suffix = " (pinned)"
                elif is_pinned:
                    color = "cyan"
                    bold = True
                    suffix = " (pinned, not installed)"
                elif inst:
                    color = None  # white
                    bold = False
                    suffix = " (installed)"
                else:
                    color = "bright_black"  # grayed out
                    bold = False
                    suffix = ""

                ver_str = click.style(f"v{full_ver}", fg=color, bold=bold)
                v_choices.append((f"{prefix}{ver_str}{suffix}", v))

            v_choices.append((click.style("Back", fg="bright_black"), None))

            default_val = versions[0] if versions else None
            v_answer = inquirer.prompt(
                [
                    inquirer.List(
                        "version",
                        message=f"Pin version ({rname.lower()}/{ctype})",
                        choices=v_choices,
                        default=default_val,
                    )
                ],
                theme=GreenPassion(),
            )
            if not v_answer or v_answer["version"] is None:
                continue

            ver_data = v_answer["version"]
            inst = installed.get(ver_data["build"])
            _set_pinned(rname, ctype, ver_data, inst)
            return


def _select_geoip_source():
    """
    Interactive selection of GeoIP source
    """
    repos, _ = _load_geoip_repos()
    if not repos:
        rprint("No GeoIP sources configured.", fg="red")
        return

    current = load_geoip_config().get("name", "")
    choices = [(r["name"] + (" [active]" if r.get("name") == current else ""), r) for r in repos]

    selected = _inquirer_select(choices, "Select GeoIP source")
    if not selected:
        return

    save_geoip_config(selected)
    rprint(f"GeoIP source: {selected['name']}", fg="green")


@cli.command(name="list")
@click.argument("mode", default="installed", type=click.Choice(["installed", "all"]))
@click.option("--path", "show_paths", is_flag=True, help="Show full paths")
def list_cmd(mode, show_paths):
    """
    List Rotunda versions

    installed  Show installed versions (default)
    all        Show all available versions from synced repos
    """
    if mode == "all":
        _list_all(show_paths)
    else:
        _list_installed(show_paths)


def _list_installed(show_paths: bool):
    """
    List installed versions
    """
    print_tree(show_paths=show_paths)

    click.echo()
    click.secho("geoip/", fg="cyan", bold=True, nl=False)
    if show_paths and GEOIP_DIR.exists():
        click.secho(f" -> {GEOIP_DIR}", fg="bright_black")
    else:
        click.echo()

    if GEOIP_DIR.exists():
        mmdb = get_mmdb_path()
        if mmdb.exists():
            click.echo(f"    └── {mmdb.name} ", nl=False)
            click.secho(f"({_get_geoip_source_name()})", fg="green")
        else:
            rprint("    └── Not downloaded", fg="yellow")
    else:
        rprint("    └── Not configured", fg="yellow")


def _list_all(_show_paths: bool):
    """
    List all available versions from synced repos
    """
    if not _ensure_synced():
        return

    cache = load_repo_cache()
    installed = {v.version.build: v for v in list_installed()}

    rprint("Available versions:\n", fg="yellow")

    for repo_data in cache.get("repos", []):
        rname = repo_data["name"]
        versions = repo_data.get("versions", [])

        click.secho(f"{rname}/", fg="cyan", bold=True)

        for i, v in enumerate(versions):
            build = v["build"]
            full_ver = f"{v['version']}-{build}"
            inst = installed.get(build)
            is_last = i == len(versions) - 1

            prefix = "└── " if is_last else "├── "
            color = "green" if inst and inst.is_active else None

            click.echo(f"    {prefix}", nl=False)
            click.secho(f"v{full_ver}", fg=color, bold=inst and inst.is_active, nl=False)

            if v.get("is_prerelease"):
                click.secho(" (prerelease)", fg="yellow", nl=False)
            else:
                click.secho(" (stable)", fg="blue", nl=False)

            if inst:
                if inst.is_active:
                    click.secho(" (installed, active)", fg="green", bold=True, nl=False)
                else:
                    click.secho(" (installed)", fg="green", nl=False)

            click.echo()

        click.echo()


@cli.command(name="remove")
@click.argument("version_path", required=False)
@click.option("--select", is_flag=True, help="Interactively select a version to remove")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def remove(version_path, select, yes):
    """
    \b
    Remove downloaded data. By default, this removes everything.
    Pass --select to pick a browser version to remove.
    """
    import shutil

    # Select mode: interactively pick a single version
    if select:
        installed = list_installed()
        if not installed:
            rprint("No browser versions installed.", fg="yellow")
            return
        choices = [(v.channel_path + (" [active]" if v.is_active else ""), v) for v in installed]
        target = _inquirer_select(choices, "Select version to remove")
        if not target:
            rprint("Cancelled.", fg="yellow")
            return
        if yes or click.confirm(f"Remove {target.channel_path}?"):
            remove_version(target.path)
            rprint(f"Removed {target.channel_path}", fg="green")
        return

    # Specific version: remove just that one
    if version_path:
        target = _find_installed(version_path)
        if not target:
            rprint(f"Version '{version_path}' not found.", fg="red")
            return
        if yes or click.confirm(f"Remove {target.channel_path}?"):
            remove_version(target.path)
            rprint(f"Removed {target.channel_path}", fg="green")
        return

    # Default: remove everything
    if not INSTALL_DIR.exists() or not any(INSTALL_DIR.iterdir()):
        rprint("Nothing to remove.", fg="yellow")
        return

    if yes or click.confirm(f"Remove the rotunda data directory ({INSTALL_DIR})?"):
        shutil.rmtree(INSTALL_DIR)
        rprint("Removed rotunda data directory.", fg="green")


@cli.command(name="test")
@click.option("--executable-path", help="Path to the Rotunda executable", default=None)
@click.option("--debug", is_flag=True, help="Print launch progress and fingerprint debug logs.")
@click.argument("url", default=None, required=False)
def test(
    url: str | None = None,
    executable_path: str | None = None,
    debug: bool = False,
) -> None:
    """
    Open a Playwright inspector session backed by a fingerprinted Rotunda context.
    """
    from .fingerprints import generate_fingerprint
    from .sync_api import NewContext, Rotunda

    executable_path = executable_path or RotundaSettings().executable_path
    if executable_path:
        if not Path(executable_path).exists():
            hint = ""
            if "<version>" in executable_path or "<release>" in executable_path:
                hint = (
                    " The README placeholder was used literally. Run `source upstream.sh` first, "
                    "then export a path built from `$version` and `$release`."
                )
            raise click.ClickException(f"Rotunda executable not found: {executable_path}.{hint}")
        rprint(f"Using executable: {executable_path}", fg="cyan")
    else:
        rprint("Using installed Rotunda executable from the local cache.", fg="cyan")

    ff_version = installed_verstr().split(".", 1)[0]
    if executable_path:
        application_ini = Path(executable_path).parent.parent / "Resources" / "application.ini"
        if application_ini.exists():
            from configparser import ConfigParser

            parser = ConfigParser()
            parser.read(application_ini, encoding="utf-8")
            ff_version = parser.get("App", "Version", fallback=ff_version).split(".", 1)[0]

    rprint("Generating shared BrowserForge fingerprint...", fg="yellow")
    fingerprint = generate_fingerprint(debug=debug)
    rprint("Shared fingerprint ready.", fg="green")
    rprint("Launching Rotunda browser...", fg="yellow")

    with Rotunda(
        headless=False,
        env=environ,
        config={"showcursor": False},
        executable_path=executable_path,
        fingerprint=fingerprint,
        debug=debug,
    ) as browser:
        rprint("Browser launched.", fg="green")
        rprint("Creating fingerprinted context...", fg="yellow")
        from playwright.sync_api import BrowserContext

        assert not isinstance(browser, BrowserContext)
        context = NewContext(browser, fingerprint=fingerprint, ff_version=ff_version, debug=debug)
        rprint("Context ready.", fg="green")
        page = context.new_page()
        if url:
            rprint(f"Navigating to {url}...", fg="yellow")
            page.goto(url)
            rprint("Navigation complete.", fg="green")
        rprint("Opening Playwright inspector...", fg="yellow")
        page.pause()


@cli.command(name="server")
def server():
    """
    Launch a Playwright server
    """
    from .server import launch_server

    launch_server()


@cli.command(name="gui")
@click.option("--debug", is_flag=True, help="Enable debug options in the GUI.")
def gui(debug):
    """
    Launch the Rotunda Manager GUI (requires PySide6)
    """
    try:
        from .gui import main

        main(debug=debug)
    except ImportError:
        rprint(
            "GUI requires PySide6. Install with: pip install 'rotunda\\[gui]'",
            fg="red",
        )


class AgentCommand(click.RichCommand):
    def get_rich_table_row(self, ctx, formatter, panel=None):
        row = super().get_rich_table_row(ctx, formatter, panel)
        name = _agent_registered_command_name(ctx, self)
        usage = _agent_command_usage(ctx, name, self)
        if usage and row:
            from rich.text import Text

            row[0] = Text(f"{name} {usage}", style=formatter.config.style_command)
        return row


class AgentCommandGroup(click.RichGroup):
    command_class = AgentCommand


def _agent_registered_command_name(ctx, cmd) -> str:
    group = ctx.command
    if isinstance(group, click.Group):
        for name, registered in group.commands.items():
            if registered is cmd:
                return name
    return cmd.name or ""


def _agent_command_usage(ctx, subcommand: str, cmd) -> str:
    sub_ctx = click.Context(cmd, info_name=subcommand, parent=ctx)
    pieces = [piece for piece in cmd.collect_usage_pieces(sub_ctx) if piece != cmd.options_metavar]
    return " ".join(pieces)


@cli.group(name="agent", cls=AgentCommandGroup)
def agent_cmd():
    """
    Agent-friendly browser control commands.
    """


@agent_cmd.command(name="new-profile")
@click.option("--name", default=None, help="Optional profile label.")
@click.option(
    "--headless/--headed",
    default=False,
    show_default=True,
    help="Run the browser without visible UI.",
)
def agent_new_profile(name: str | None, headless: bool) -> None:
    """
    Create an agent profile under ~/.rotunda.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    profile = store.create_profile(name=name, headless=headless)
    resource = store.register(
        kind="profile",
        id=profile["id"],
        label=profile["name"],
    )
    click.echo(f"[{resource.idx}] profile {profile['id']}")
    click.echo(f"    path: {profile['profile_dir']}")
    click.echo("    browser: rotunda")


@agent_cmd.command(name="new-context")
@click.argument("profile")
def agent_new_context(profile: str) -> None:
    """
    Create or attach to a browser context for a profile reference.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    profile_resource = _agent_resolve(store, profile, kind="profile")
    with console.status(
        f"Starting or attaching agent daemon for profile {profile_resource.idx}...",
        spinner="dots",
    ):
        client = _agent_client(store, profile_resource.id)
    with console.status(
        "Launching Rotunda and creating browser context...",
        spinner="dots",
    ):
        data = _agent_post(client, "/new-context")
    context_resource = store.register(
        kind="context",
        id=data["context_id"],
        profile_id=profile_resource.id,
        parent_id=profile_resource.id,
        label=f"profile {profile_resource.idx}",
        runtime_id=client.session.get("instance_id"),
    )
    click.echo(f"[{context_resource.idx}] context {context_resource.id}")
    for page in data.get("pages", []):
        _agent_register_page(store, page, context_resource)


@agent_cmd.command(name="new-page")
@click.argument("context")
def agent_new_page(context: str) -> None:
    """
    Create a page from a context idx.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    context_resource = _agent_resolve(store, context, kind="context")
    if not context_resource.profile_id:
        raise click.ClickException("Context has no profile.")
    client = _agent_client(store, context_resource.profile_id)
    data = _agent_post(client, "/new-page")
    _agent_register_page(store, data["page"], context_resource)


@agent_cmd.command(name="pages")
@click.argument("context", required=False, metavar="[CONTEXT_OR_PROFILE]")
def agent_pages(context: str | None) -> None:
    """
    List pages known to a browser context.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    context_resource = _agent_context_from_ref(store, context)
    if not context_resource.profile_id:
        raise click.ClickException("Context has no profile.")
    client = _agent_client(store, context_resource.profile_id)
    data = _agent_post(client, "/pages")
    _agent_register_pages(store, context_resource, data.get("pages", []))


@agent_cmd.command(name="close-page")
@click.argument("page")
def agent_close_page(page: str) -> None:
    """
    Close a page and remove its saved resource refs.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    _agent_post(client, "/close-page", {"page_id": page_resource.id})
    store.remove_children(page_resource.id, kind="element")
    store.remove(kind="page", id=page_resource.id)
    click.echo(f"closed page {page_resource.id}")


@agent_cmd.command(name="navigate")
@click.argument("page")
@click.argument("url")
@click.option(
    "--wait-until",
    default="domcontentloaded",
    type=click.Choice(["commit", "domcontentloaded", "load", "networkidle"]),
    help="Playwright navigation wait state.",
)
def agent_navigate(page: str, url: str, wait_until: str) -> None:
    """
    Navigate a page idx to a URL.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/navigate",
        {
            "page_id": page_resource.id,
            "url": _agent_normalize_url(url),
            "wait_until": wait_until,
        },
    )
    _agent_update_page(store, data["page"], page_resource)


@agent_cmd.command(name="back")
@click.argument("page")
def agent_back(page: str) -> None:
    """
    Navigate a page back in history.
    """
    _agent_page_endpoint(page, "/back")


@agent_cmd.command(name="forward")
@click.argument("page")
def agent_forward(page: str) -> None:
    """
    Navigate a page forward in history.
    """
    _agent_page_endpoint(page, "/forward")


@agent_cmd.command(name="reload")
@click.argument("page")
def agent_reload(page: str) -> None:
    """
    Reload a page.
    """
    _agent_page_endpoint(page, "/reload")


@agent_cmd.command(name="screenshot")
@click.argument("page")
@click.argument("path", required=False)
@click.option("--full-page/--viewport", default=False, show_default=True, help="Capture the full page.")
@click.option("--element", "element_ref", default=None, help="Capture one described element ref.")
def agent_screenshot(page: str, path: str | None, full_page: bool, element_ref: str | None) -> None:
    """
    Capture a page or element screenshot.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    if path:
        output = Path(path).expanduser().resolve()
    else:
        output = Path(tempfile.gettempdir()) / f"rotunda-agent-screenshot-{uuid.uuid4().hex}.png"
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/screenshot",
        {
            "page_id": page_resource.id,
            "path": str(output),
            "full_page": full_page,
            "ref": element_ref,
        },
    )
    _agent_update_page(store, data["page"], page_resource)
    # Keep the absolute screenshot path in stdout so agents can read the image
    # as a follow-up observation without guessing where the file was written.
    click.echo(f"screenshot: {data['path']}")


@agent_cmd.command(name="wait")
@click.argument("page")
@click.argument("value", required=False)
@click.option(
    "--for",
    "wait_for",
    default="load",
    show_default=True,
    type=click.Choice(["load", "domcontentloaded", "networkidle", "selector", "text", "url", "timeout"]),
    help="What to wait for.",
)
@click.option(
    "--state",
    default="visible",
    show_default=True,
    type=click.Choice(["attached", "detached", "visible", "hidden"]),
    help="Selector/text wait state.",
)
@click.option("--timeout-ms", default=15_000, show_default=True, help="Maximum wait time.")
def agent_wait(page: str, value: str | None, wait_for: str, state: str, timeout_ms: int) -> None:
    """
    Wait for page load, URL, text, selector, or a fixed timeout.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/wait",
        {
            "page_id": page_resource.id,
            "target": wait_for,
            "value": value,
            "state": state,
            "timeout_ms": timeout_ms,
        },
    )
    _agent_update_page(store, data["page"], page_resource)


@agent_cmd.command(name="describe")
@click.argument("page")
@click.option("--max-items", default=200, show_default=True, help="Maximum DOM items.")
def agent_describe(page: str, max_items: int) -> None:
    """
    Dump an agent-friendly DOM representation for a page idx.
    """
    _agent_describe(page, max_items)


@agent_cmd.command(name="list", hidden=True)
@click.argument("page")
@click.option("--max-items", default=200, show_default=True, help="Maximum DOM items.")
def agent_list(page: str, max_items: int) -> None:
    """
    Deprecated alias for describe.
    """
    _agent_describe(page, max_items)


def _agent_describe(page: str, max_items: int) -> None:
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/describe",
        {"page_id": page_resource.id, "max_items": max_items},
    )
    page_resource = _agent_update_page(store, data["page"], page_resource)
    _agent_register_elements(store, page_resource, data.get("items", []))
    if data.get("text"):
        click.echo(data["text"])


@agent_cmd.command(name="click")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF")
def agent_click(args: tuple[str, ...]) -> None:
    """
    Click a DOM ref from the last describe output.

    \b
    Arguments:
      REF   Element ref returned by `rotunda agent describe <page>`.
      PAGE  Optional page index/reference for the legacy page-qualified form.

    \b
    Forms:
      rotunda agent click <ref>
      rotunda agent click <page> <ref>
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref = _agent_target_from_click_args(store, args)
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(client, "/click", {"page_id": page_resource.id, "ref": ref})
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="info")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF")
def agent_info(args: tuple[str, ...]) -> None:
    """
    Dump detailed information for one DOM ref.

    \b
    Arguments:
      REF   Element ref returned by `rotunda agent describe <page>`.
      PAGE  Optional page index/reference for the legacy page-qualified form.

    \b
    Forms:
      rotunda agent info <ref>
      rotunda agent info <page> <ref>
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref = _agent_target_from_click_args(store, args)
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(client, "/info", {"page_id": page_resource.id, "ref": ref})
    _agent_update_page(store, data["page"], page_resource)
    if data.get("text"):
        click.echo(data["text"])


@agent_cmd.command(name="fill")
@click.option("--submit", is_flag=True, help="Press Enter after filling text.")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF TEXT")
def agent_fill(args: tuple[str, ...], submit: bool) -> None:
    """
    Fill text into a DOM ref from the last describe output.

    \b
    Arguments:
      REF   Input element ref returned by `rotunda agent describe <page>`.
      TEXT  Replacement text. Existing field contents are cleared first.
      PAGE  Optional page index/reference for the legacy page-qualified form.

    \b
    Forms:
      rotunda agent fill <ref> <text>
      rotunda agent fill <page> <ref> <text>
    """
    _agent_fill(args, submit=submit, command_name="fill")


def _agent_fill(args: tuple[str, ...], *, submit: bool, command_name: str) -> None:
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref, text = _agent_target_from_text_args(store, args, command_name=command_name)
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/fill",
        {
            "page_id": page_resource.id,
            "ref": ref,
            "text": text,
            "submit": submit,
        },
    )
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="select")
@click.option(
    "--by",
    "select_by",
    default="value",
    show_default=True,
    type=click.Choice(["value", "label", "index"]),
    help="How to match the option.",
)
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF VALUE...")
def agent_select(args: tuple[str, ...], select_by: str) -> None:
    """
    Select one or more options in a dropdown DOM ref.

    \b
    Arguments:
      REF     Select element ref returned by `rotunda agent describe <page>`.
      VALUE   Option value, label, or index depending on `--by`.
      PAGE    Optional page index/reference for the legacy page-qualified form.

    \b
    Forms:
      rotunda agent select <ref> <value> [value...]
      rotunda agent select <page> <ref> <value> [value...]

    Use `rotunda agent info <ref>` to inspect available option values.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref, values = _agent_target_from_values_args(
        store,
        args,
        command_name="select",
    )
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/select",
        {
            "page_id": page_resource.id,
            "ref": ref,
            "values": values,
            "by": select_by,
        },
    )
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="type")
@click.option("--submit", is_flag=True, help="Press Enter after typing text.")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF TEXT")
def agent_type(args: tuple[str, ...], submit: bool) -> None:
    """
    Type text into a DOM ref from the last describe output.

    \b
    Arguments:
      REF   Input element ref returned by `rotunda agent describe <page>`.
      TEXT  Text to insert at the focused cursor position.
      PAGE  Optional page index/reference for the legacy page-qualified form.

    \b
    Forms:
      rotunda agent type <ref> <text>
      rotunda agent type <page> <ref> <text>
    """
    _agent_type(args, submit=submit)


@agent_cmd.command(name="typing", hidden=True)
@click.option("--submit", is_flag=True, help="Press Enter after typing text.")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF TEXT")
def agent_typing(args: tuple[str, ...], submit: bool) -> None:
    """
    Deprecated alias for type.
    """
    _agent_type(args, submit=submit)


def _agent_type(args: tuple[str, ...], *, submit: bool) -> None:
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref, text = _agent_target_from_text_args(store, args, command_name="type")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/type",
        {
            "page_id": page_resource.id,
            "ref": ref,
            "text": text,
            "submit": submit,
        },
    )
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="press")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] [REF] KEY")
def agent_press(args: tuple[str, ...]) -> None:
    """
    Press a keyboard key globally or on a DOM ref.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref, key = _agent_target_from_key_args(store, args, command_name="press")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/press",
        {
            "page_id": page_resource.id,
            "ref": ref,
            "key": key,
        },
    )
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="hover")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF")
def agent_hover(args: tuple[str, ...]) -> None:
    """
    Hover a DOM ref.
    """
    _agent_ref_action(args, "/hover")


@agent_cmd.command(name="scroll")
@click.option("--amount", default=600, show_default=True, help="Scroll distance in pixels.")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] [REF] DIRECTION")
def agent_scroll(args: tuple[str, ...], amount: int) -> None:
    """
    Scroll a page or scrollable DOM ref.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref, direction = _agent_target_from_direction_args(store, args, command_name="scroll")
    if direction not in {"up", "down", "left", "right"}:
        raise click.ClickException("Direction must be one of: up, down, left, right.")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/scroll",
        {
            "page_id": page_resource.id,
            "ref": ref,
            "direction": direction,
            "amount": amount,
        },
    )
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="drag")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] FROM_REF TO_REF")
def agent_drag(args: tuple[str, ...]) -> None:
    """
    Drag one DOM ref onto another.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, source_ref, target_ref = _agent_target_from_drag_args(store, args)
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/drag",
        {
            "page_id": page_resource.id,
            "source_ref": source_ref,
            "target_ref": target_ref,
        },
    )
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="check")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF")
def agent_check(args: tuple[str, ...]) -> None:
    """
    Check a checkbox or radio DOM ref.
    """
    _agent_ref_action(args, "/check")


@agent_cmd.command(name="uncheck")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF")
def agent_uncheck(args: tuple[str, ...]) -> None:
    """
    Uncheck a checkbox DOM ref.
    """
    _agent_ref_action(args, "/uncheck")


@agent_cmd.command(name="upload")
@click.argument("args", nargs=-1, required=True, metavar="[PAGE] REF PATH...")
def agent_upload(args: tuple[str, ...]) -> None:
    """
    Upload one or more files through a file input DOM ref.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref, paths = _agent_target_from_paths_args(store, args, command_name="upload")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    resolved_paths = [str(Path(path).expanduser().resolve()) for path in paths]
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/upload",
        {
            "page_id": page_resource.id,
            "ref": ref,
            "paths": resolved_paths,
        },
    )
    _agent_handle_action_result(store, page_resource, data)


@agent_cmd.command(name="downloads")
@click.argument("context", required=False, metavar="[CONTEXT_OR_PROFILE]")
def agent_downloads(context: str | None) -> None:
    """
    List downloads captured by a browser context.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    context_resource = _agent_context_from_ref(store, context)
    if not context_resource.profile_id:
        raise click.ClickException("Context has no profile.")
    client = _agent_client(store, context_resource.profile_id)
    data = _agent_post(client, "/downloads")
    downloads = data.get("downloads", [])
    resources = _agent_register_downloads(store, context_resource, downloads)
    for item in downloads:
        _agent_print_download(item, resources.get(str(item.get("id") or "")))


@agent_cmd.command(name="save-download")
@click.argument("download")
@click.argument("path")
def agent_save_download(download: str, path: str) -> None:
    """
    Save a captured download to a path.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    download_resource = _agent_resolve(store, download, kind="download")
    if not download_resource.profile_id:
        raise click.ClickException("Download has no profile.")
    output = Path(path).expanduser().resolve()
    client = _agent_client(store, download_resource.profile_id)
    data = _agent_post(
        client,
        "/save-download",
        {
            "download_id": download_resource.id,
            "path": str(output),
        },
    )
    _agent_print_download(data["download"], download_resource)


@agent_cmd.command(name="dialog")
@click.argument("page")
@click.argument("action", type=click.Choice(["list", "accept", "dismiss", "fill"]))
@click.argument("text", required=False)
def agent_dialog(page: str, action: str, text: str | None) -> None:
    """
    List handled dialogs or arm how the next dialog should be handled.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/dialog",
        {
            "page_id": page_resource.id,
            "action": action,
            "text": text or "",
        },
    )
    if action == "list":
        for item in data.get("dialogs", []):
            suffix = f" error={item['error']!r}" if item.get("error") else ""
            click.echo(
                f"[{item.get('id')}] {item.get('type', 'dialog')} "
                f"{item.get('action', '')} {item.get('message', '')!r}{suffix}"
            )
    else:
        armed = data.get("armed", {})
        click.echo(f"dialog armed: {armed.get('action')}")


@agent_cmd.command(name="extract")
@click.argument("page")
@click.option(
    "--format",
    "extract_format",
    default="text",
    show_default=True,
    type=click.Choice(["text", "html", "markdown", "links", "forms"]),
    help="Extraction format.",
)
@click.option("--output", type=click.Path(path_type=Path), default=None, help="Write extracted content to a file.")
def agent_extract(page: str, extract_format: str, output: Path | None) -> None:
    """
    Extract text, HTML, markdown, links, or forms from a page.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(
        client,
        "/extract",
        {
            "page_id": page_resource.id,
            "format": extract_format,
        },
    )
    text = str(data.get("text") or "")
    if output:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        click.echo(f"extracted: {output}")
    else:
        click.echo(text)


@agent_cmd.command(name="resources")
@click.option(
    "--kind",
    type=click.Choice(["profile", "context", "page", "element", "download"]),
    default=None,
    help="Filter resource kind.",
)
def agent_resources(kind: str | None) -> None:
    """
    List saved agent resource indexes.
    """
    from .agent.store import AgentStore

    store = AgentStore()
    for resource in store.list_resources(kind=kind):
        suffix = f" {resource.label}" if resource.label else ""
        click.echo(f"[{resource.idx}] {resource.kind} {resource.id}{suffix}")


@agent_cmd.command(name="stop")
@click.argument("profile", required=False)
def agent_stop(profile: str | None) -> None:
    """
    Stop the daemon for a profile idx.
    """
    from contextlib import suppress

    from .agent.client import AgentClient, AgentClientError, discover_daemon
    from .agent.store import AgentStore

    store = AgentStore()
    if profile is None:
        client = discover_daemon(store=store)
        if client is None:
            store.clear_runtime_state()
            click.echo("No running daemon.")
            return
        stopped_profile = str(client.session.get("profile_id") or "")
        with suppress(AgentClientError):
            client.post("/shutdown")
        store.clear_runtime_state()
        click.echo(f"Stopped profile {stopped_profile}.")
        return

    profile_resource = _agent_resolve(store, profile, kind="profile")
    session = store.load_session(profile_resource.id)
    if not session:
        client = discover_daemon(store=store)
        if client is None or client.session.get("profile_id") != profile_resource.id:
            click.echo(f"No running daemon for profile {profile_resource.id}.")
            return
        session = client.session
    with suppress(AgentClientError):
        AgentClient(session).post("/shutdown")
    store.clear_runtime_state()
    click.echo(f"Stopped profile {profile_resource.id}.")


def _agent_client(store, profile_id: str):
    from .agent.client import AgentClientError, ensure_daemon

    try:
        return ensure_daemon(profile_id, store=store)
    except AgentClientError as exc:
        raise click.ClickException(_agent_clean_error(exc)) from None


def _agent_post(client, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from .agent.client import AgentClientError

    try:
        return client.post(path, payload)
    except AgentClientError as exc:
        raise click.ClickException(_agent_clean_error(exc)) from None


def _agent_resolve(store, ref: str | None, *, kind: str):
    try:
        return store.resolve(ref, kind=kind)
    except KeyError as exc:
        raise click.ClickException(_agent_clean_error(exc)) from None


def _agent_context_from_ref(store, ref: str | None):
    if not ref:
        return _agent_resolve(store, None, kind="context")

    try:
        return store.resolve(ref, kind="context")
    except KeyError:
        pass

    try:
        profile_resource = store.resolve(ref, kind="profile")
    except KeyError as exc:
        raise click.ClickException(_agent_clean_error(exc)) from None

    matches = [
        resource
        for resource in store.list_resources(kind="context")
        if resource.profile_id == profile_resource.id or resource.parent_id == profile_resource.id
    ]
    if not matches:
        raise click.ClickException(f"No context has been created for profile {ref}.")
    return max(matches, key=lambda resource: resource.idx)


def _agent_context_for_page(store, page_resource):
    if not page_resource.parent_id:
        raise click.ClickException("Page has no context.")
    return _agent_resolve(store, page_resource.parent_id, kind="context")


def _agent_page_endpoint(page: str, endpoint: str) -> None:
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource = _agent_resolve(store, page, kind="page")
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(client, endpoint, {"page_id": page_resource.id})
    _agent_update_page(store, data["page"], page_resource)


def _agent_ref_action(args: tuple[str, ...], endpoint: str) -> None:
    from .agent.store import AgentStore

    store = AgentStore()
    page_resource, ref = _agent_target_from_click_args(store, args)
    if not page_resource.profile_id:
        raise click.ClickException("Page has no profile.")
    client = _agent_client(store, page_resource.profile_id)
    data = _agent_post(client, endpoint, {"page_id": page_resource.id, "ref": ref})
    _agent_handle_action_result(store, page_resource, data)


def _agent_handle_action_result(store, page_resource, data: dict[str, Any]) -> None:
    updated = _agent_update_page(store, data["page"], page_resource)
    _agent_register_response_pages(store, updated, data)
    _agent_register_elements(store, updated, data.get("items", []))
    if data.get("selected") is not None:
        click.echo(f"selected: {', '.join(str(value) for value in data['selected'])}")
    if data.get("text"):
        click.echo(data["text"])


def _agent_register_response_pages(store, page_resource, data: dict[str, Any]) -> None:
    pages = data.get("pages") or []
    if not pages:
        return
    context_resource = _agent_context_for_page(store, page_resource)
    _agent_register_pages(store, context_resource, pages)


def _agent_register_pages(store, context_resource, pages: list[dict[str, str]]) -> None:
    seen: set[str] = set()
    for page in pages:
        page_id = str(page.get("id") or "")
        if not page_id or page_id in seen:
            continue
        seen.add(page_id)
        _agent_register_page(store, page, context_resource)


def _agent_register_downloads(store, context_resource, downloads: list[dict[str, Any]]) -> dict[str, Any]:
    resources = {}
    for item in downloads:
        download_id = str(item.get("id") or "")
        if not download_id:
            continue
        label = str(item.get("suggested_filename") or item.get("url") or "download")
        resources[download_id] = store.register(
            kind="download",
            id=download_id,
            profile_id=context_resource.profile_id,
            parent_id=context_resource.id,
            label=label,
            runtime_id=context_resource.runtime_id,
        )
    return resources


def _agent_print_download(item: dict[str, Any], resource=None) -> None:
    filename = item.get("suggested_filename") or ""
    path = item.get("saved_as") or item.get("path") or ""
    suffix = f" {filename}" if filename else ""
    if path:
        suffix += f" path={path}"
    prefix = f"[{resource.idx}] " if resource else ""
    click.echo(f"{prefix}download {item.get('id', '')} {item.get('url', '')}{suffix}")


def _agent_target_from_click_args(store, args: tuple[str, ...]):
    if len(args) == 1:
        return _agent_page_and_ref_for_element_ref(store, args[0])
    if len(args) == 2:
        return _agent_resolve(store, args[0], kind="page"), args[1]
    raise click.ClickException("Usage: rotunda agent click <ref> or rotunda agent click <page> <ref>")


def _agent_target_from_text_args(store, args: tuple[str, ...], *, command_name: str):
    if len(args) == 2:
        return (*_agent_page_and_ref_for_element_ref(store, args[0]), args[1])
    if len(args) == 3:
        return _agent_resolve(store, args[0], kind="page"), args[1], args[2]
    raise click.ClickException(
        f"Usage: rotunda agent {command_name} <ref> <text> "
        f"or rotunda agent {command_name} <page> <ref> <text>"
    )


def _agent_target_from_values_args(store, args: tuple[str, ...], *, command_name: str):
    if len(args) < 2:
        raise click.ClickException(
            f"Usage: rotunda agent {command_name} <ref> <value> [value...] "
            f"or rotunda agent {command_name} <page> <ref> <value> [value...]"
        )

    try:
        page_resource, ref = _agent_page_and_ref_for_element_ref(store, args[0])
        return page_resource, ref, list(args[1:])
    except click.ClickException:
        if len(args) < 3:
            raise

    page_resource = _agent_resolve(store, args[0], kind="page")
    return page_resource, args[1], list(args[2:])


def _agent_target_from_paths_args(store, args: tuple[str, ...], *, command_name: str):
    if len(args) < 2:
        raise click.ClickException(
            f"Usage: rotunda agent {command_name} <ref> <path> [path...] "
            f"or rotunda agent {command_name} <page> <ref> <path> [path...]"
        )
    try:
        page_resource, ref = _agent_page_and_ref_for_element_ref(store, args[0])
        return page_resource, ref, list(args[1:])
    except click.ClickException:
        if len(args) < 3:
            raise
    page_resource = _agent_resolve(store, args[0], kind="page")
    return page_resource, args[1], list(args[2:])


def _agent_target_from_key_args(store, args: tuple[str, ...], *, command_name: str):
    if len(args) == 1:
        return _agent_resolve(store, None, kind="page"), None, args[0]
    if len(args) == 2:
        try:
            page_resource, ref = _agent_page_and_ref_for_element_ref(store, args[0])
            return page_resource, ref, args[1]
        except click.ClickException:
            return _agent_resolve(store, args[0], kind="page"), None, args[1]
    if len(args) == 3:
        return _agent_resolve(store, args[0], kind="page"), args[1], args[2]
    raise click.ClickException(
        f"Usage: rotunda agent {command_name} <key>, "
        f"rotunda agent {command_name} <ref> <key>, "
        f"or rotunda agent {command_name} <page> <ref> <key>"
    )


def _agent_target_from_direction_args(store, args: tuple[str, ...], *, command_name: str):
    if len(args) == 1:
        return _agent_resolve(store, None, kind="page"), None, args[0]
    if len(args) == 2:
        try:
            page_resource, ref = _agent_page_and_ref_for_element_ref(store, args[0])
            return page_resource, ref, args[1]
        except click.ClickException:
            return _agent_resolve(store, args[0], kind="page"), None, args[1]
    if len(args) == 3:
        return _agent_resolve(store, args[0], kind="page"), args[1], args[2]
    raise click.ClickException(
        f"Usage: rotunda agent {command_name} <direction>, "
        f"rotunda agent {command_name} <ref> <direction>, "
        f"or rotunda agent {command_name} <page> <ref> <direction>"
    )


def _agent_target_from_drag_args(store, args: tuple[str, ...]):
    if len(args) == 2:
        page_resource, source_ref = _agent_page_and_ref_for_element_ref(store, args[0])
        target_ref = _agent_ref_on_page(store, args[1], page_resource.id)
        return page_resource, source_ref, target_ref
    if len(args) == 3:
        page_resource = _agent_resolve(store, args[0], kind="page")
        return page_resource, args[1], args[2]
    raise click.ClickException("Usage: rotunda agent drag <from-ref> <to-ref> or rotunda agent drag <page> <from-ref> <to-ref>")


def _agent_ref_on_page(store, ref: str, page_id: str) -> str:
    try:
        element_resource = store.resolve(ref, kind="element")
    except KeyError:
        return ref
    if element_resource.parent_id and element_resource.parent_id != page_id:
        raise click.ClickException(f"Element ref {ref} is attached to a different page.")
    return element_resource.id


def _agent_page_and_ref_for_element_ref(store, ref: str):
    try:
        element_resource = store.resolve(ref, kind="element")
    except KeyError as exc:
        message = _agent_clean_error(exc)
        raise click.ClickException(
            f"{message}. Run `rotunda agent describe <page>` before using a global element ref."
        ) from None
    if not element_resource.parent_id:
        raise click.ClickException(f"Element ref {ref} is not attached to a page.")
    try:
        page_resource = store.resolve(element_resource.parent_id, kind="page")
    except KeyError as exc:
        message = _agent_clean_error(exc)
        raise click.ClickException(f"{message} for element ref {ref}.") from None
    return page_resource, element_resource.id


def _agent_register_page(store, page: dict[str, str], context_resource):
    page_resource = store.register(
        kind="page",
        id=page["id"],
        profile_id=context_resource.profile_id,
        parent_id=context_resource.id,
        label=page.get("url") or "about:blank",
        runtime_id=context_resource.runtime_id,
    )
    _agent_print_page(page_resource.idx, page)
    return page_resource


def _agent_update_page(store, page: dict[str, str], page_resource):
    updated = store.register(
        kind="page",
        id=page["id"],
        profile_id=page_resource.profile_id,
        parent_id=page_resource.parent_id,
        label=page.get("url") or page_resource.label,
        runtime_id=page_resource.runtime_id,
    )
    _agent_print_page(updated.idx, page)
    return updated


def _agent_register_elements(store, page_resource, items: list[dict[str, Any]]) -> None:
    store.remove_children(page_resource.id, kind="element")
    for item in items:
        ref = str(item.get("ref") or "")
        if not ref:
            continue
        store.register(
            kind="element",
            id=ref,
            profile_id=page_resource.profile_id,
            parent_id=page_resource.id,
            label=_agent_element_label(item),
            runtime_id=page_resource.runtime_id,
        )


def _agent_element_label(item: dict[str, Any]) -> str:
    kind = str(item.get("role") or item.get("tag") or "element")
    label = str(item.get("name") or item.get("text") or "")
    if label:
        return f'{kind} "{label}"'
    return kind


def _agent_print_page(idx: int, page: dict[str, str]) -> None:
    title = f" title={page['title']!r}" if page.get("title") else ""
    click.echo(f"[{idx}] page {page['id']} {page.get('url', '')}{title}")


def _agent_normalize_url(url: str) -> str:
    if "://" in url or url.startswith(("about:", "data:", "file:")):
        return url
    return f"https://{url}"


def _agent_clean_error(exc: BaseException) -> str:
    message = str(exc)
    if len(message) >= 2 and message[0] == message[-1] == "'":
        message = message[1:-1]
    return message


class VersionInfo:
    def __init__(self):
        from rich.table import Table
        from rich.text import Text

        from .pkgman import console

        self.Text = Text
        self.console = console
        self.t = Table.grid(padding=(0, 2))

    def _row(self, label, value, style="green"):
        """
        Print a row to the table
        """
        self.t.add_row(self.Text(f"  {label}", style="dim"), self.Text(value, style=style))

    def _header(self, title):
        """
        Print a section title to the table
        """
        self.t.add_row(self.Text(title, style="bold"), self.Text(""))

    def _pkg(self, label, pkg_name):
        try:
            self._row(label, f"v{pkg_version(pkg_name)}")
        except PackageNotFoundError:
            self._row(label, "?", style="dim")

    def packages(self):
        """
        Gets installed package versions
        """
        self._header("Python Packages")
        self._pkg("Rotunda", "rotunda")
        self._pkg("Browserforge", "browserforge")
        self._pkg("Apify Fingerprints", "apify_fingerprint_datapoints")
        self._pkg("Playwright", "playwright")

    def browser(self):
        """
        Gets active browser, installed version, and sync status
        """
        from datetime import datetime, timezone

        self._header("Browser")

        config = load_config()
        pinned = config.get("pinned")
        channel = config.get("channel") or get_default_channel()

        # Active: what was set (channel or pinned version)
        if pinned:
            self._row("Active", f"{channel.lower()}/{pinned}")
        else:
            self._row("Active", channel.lower())

        # Find the active installed version
        active_v = None
        for v in list_installed():
            if v.is_active:
                active_v = v
                break

        # Browser version
        if active_v:
            self._row("Current browser", f"v{active_v.version.full_string}")
        else:
            self._row("Current browser", "Not installed", style="dim")

        # Is installed?
        if active_v:
            self._row("Installed", "Yes", style="green")
        else:
            self._row("Installed", "No", style="red")

        # Check if installed version is the latest in its own channel
        if active_v:
            ctype = "prerelease" if active_v.is_prerelease else "stable"
            repo_ch = f"{active_v.repo_name}/{ctype}"
            is_latest = False
            cache = load_repo_cache()
            for repo_data in cache.get("repos", []):
                if repo_data["name"].lower() != active_v.repo_name.lower():
                    continue
                candidates = [
                    v
                    for v in repo_data.get("versions", [])
                    if v.get("is_prerelease", False) == active_v.is_prerelease
                ]
                if candidates and active_v.version.build == candidates[0]["build"]:
                    is_latest = True
                break
            self._row(
                f"Latest in {repo_ch}?",
                "Yes" if is_latest else "No",
                style="green" if is_latest else "red",
            )

        # Last repo sync time from cache file mtime
        if REPO_CACHE_FILE.exists():
            mtime = REPO_CACHE_FILE.stat().st_mtime
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone()
            self._row("Last Sync", dt.strftime("%Y-%m-%d %H:%M"), style="dim")
        else:
            self._row("Last Sync", "Never", style="red")

    def geoip(self):
        """
        Get info about the geoip db and check if its there
        """
        from datetime import datetime, timezone

        self._header("GeoIP")
        if not ALLOW_GEOIP:
            # geoip2 package not installed
            self._row("Status", "Not supported (install rotunda[geoip])", style="dim")
        else:
            mmdb_path = get_mmdb_path()
            if mmdb_path.exists():
                # Show active database name and last update time
                geoip_cfg = load_geoip_config()
                self._row("Database", geoip_cfg.get("name", "Unknown"))
                mtime = mmdb_path.stat().st_mtime
                dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone()
                self._row("Updated", dt.strftime("%Y-%m-%d %H:%M"), style="dim")
            else:
                self._row("Database", "Not installed", style="dim")

    def _dir_size(self, path) -> str:
        if not path.exists():
            return "Nothing here"
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        for unit in ("B", "KB", "MB"):
            if total < 1024:
                return f"{total:.1f} {unit}" if unit != "B" else f"{total} B"
            total /= 1024
        return f"{total:.1f} GB"

    def storage(self):
        """
        Get paths and directory sizes
        """
        self._header("Storage")
        self._row("Install path", str(INSTALL_DIR), style="cyan")
        self._row("Browser(s) directory size", self._dir_size(BROWSERS_DIR), style="dim")
        if ALLOW_GEOIP:
            self._row("GeoIP database size", self._dir_size(GEOIP_DIR), style="dim")
        self._row("Config file", str(CONFIG_FILE), style="cyan")
        self._row("Repo cache", str(REPO_CACHE_FILE), style="cyan")

    def print_all(self):
        self.packages()
        self.browser()
        self.geoip()
        self.storage()
        self.console.print(self.t)


@cli.command(name="version")
def version():
    """
    Display version, package, browser, and storage info
    """
    VersionInfo().print_all()


@cli.command(name="active")
def active_cmd():
    """
    Print the current active version
    """
    config = load_config()
    pinned = config.get("pinned")
    channel = config.get("channel") or get_default_channel()

    if pinned:
        # Check if the pinned version is installed
        display = f"{channel.lower()}/{pinned}"
        target = _find_installed(display)
        if target:
            click.echo(target.channel_path)
        else:
            click.echo(f"{display} ", nl=False)
            rprint("(not fetched)", fg="yellow")
    else:
        # Using channel, so find active installed version
        installed = list_installed()
        for v in installed:
            if v.is_active:
                click.echo(v.channel_path)
                return
        click.echo(f"{channel.lower()} ", nl=False)
        rprint("(not fetched)", fg="yellow")


@cli.command(name="path")
def path_cmd():
    """
    Print the install directory path
    """
    click.echo(INSTALL_DIR)


if __name__ == "__main__":
    cli()
