#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "click>=8.1",
#   "rich>=13.9",
# ]
# ///

from __future__ import annotations

import os
import posixpath
import random
import re
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import click
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from _mixin import BROWSERBUILD_DIR as BROWSERBUILD_DIR_RAW, PATCHES_DIR as PATCHES_DIR_RAW, list_patches


REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_SH = REPO_ROOT / "upstream.sh"
BROWSERBUILD_DIR = Path(BROWSERBUILD_DIR_RAW)
PATCHES_DIR = Path(PATCHES_DIR_RAW)
ADDITIONS_DIR = BROWSERBUILD_DIR / "additions"
SETTINGS_DIR = BROWSERBUILD_DIR / "settings"
ASSETS_DIR = BROWSERBUILD_DIR / "assets"
SCRIPTS_DIR = REPO_ROOT / "scripts"
BROWSERBUILD_ADDITIONS_REPO_PREFIX = ("browserbuild", "additions")
FIREFOX_TARBALL_URL = (
    "https://archive.mozilla.org/pub/firefox/releases/{version}/source/"
    "firefox-{version}.source.tar.xz"
)

PATCH_PATH_RE = re.compile(r"^(---|\+\+\+) ((?:[ab]/).+|/dev/null)$")

SOURCE_EXTENSIONS = {".cc", ".cpp", ".cxx"}
SKIPPED_SOURCE_EXTENSIONS = {".m", ".mm"}
HELPER_SEEDED_PATHS = {
    "build/vs/pack_vs.py",
    "browser/config/version.txt",
    "browser/config/version_display.txt",
    "lw/rotunda.cfg",
    "lw/chrome.css",
    "lw/local-settings.js",
    "lw/moz.build",
    "lw/mozfetch.sh",
    "lw/policies.json",
    "services/settings/dumps/main/search-config.json",
}
console = Console()


@dataclass(frozen=True)
class BuildMetadata:
    version: str
    release: str


@dataclass(frozen=True)
class VerifierOptions:
    cache_dir: Path
    tarball: Path | None
    workspace: Path | None
    source_tree: Path | None
    compile_commands_dir: Path | None
    keep_workdir: bool
    skip_syntax: bool
    jobs: int


@dataclass(frozen=True)
class PatchEntry:
    old_path: str | None
    new_path: str | None

    @property
    def extract_path(self) -> str | None:
        return self.old_path

    @property
    def target_path(self) -> str | None:
        return self.new_path or self.old_path

    @property
    def is_deleted(self) -> bool:
        return self.new_path is None


@dataclass(frozen=True)
class PreparationFailure:
    target: str
    reason: str
    skip: bool


@dataclass(frozen=True)
class PreparedTarget:
    display_path: str
    compile_path: Path
    command: list[str]
    working_directory: Path


@dataclass(frozen=True)
class SyntaxResult:
    display_path: str
    status: str
    reason: str | None = None
    output: str | None = None


@dataclass(frozen=True)
class SyntaxScope:
    changed_repo_files: list[str]
    changed_patch_files: list[Path]
    overlay_paths: list[str]
    syntax_targets: list[str]
    sampled_patch: Path | None = None


@dataclass(frozen=True)
class CompileCommandContext:
    source_tree: Path
    compile_commands_dir: Path
    commands_by_relative_path: dict[str, tuple[Path, list[str]]]


def parse_upstream_metadata(upstream_path: Path = UPSTREAM_SH) -> BuildMetadata:
    values: dict[str, str] = {}
    for line in upstream_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    version = os.environ.get("ROTUNDA_FIREFOX_VERSION", values["version"])
    release = os.environ.get("ROTUNDA_RELEASE", values["release"])
    return BuildMetadata(version=version, release=release)


def order_patch_paths(paths: list[Path]) -> list[Path]:
    non_roverfox: list[Path] = []
    roverfox: list[Path] = []
    for patch_path in paths:
        if "roverfox" in patch_path.parts:
            roverfox.append(patch_path)
        else:
            non_roverfox.append(patch_path)
    return non_roverfox + roverfox


def active_patch_paths() -> list[Path]:
    raw_paths = [Path(path) for path in list_patches(str(PATCHES_DIR))]
    return order_patch_paths(raw_paths)


def parse_patch_entries(patch_path: Path) -> list[PatchEntry]:
    entries: list[PatchEntry] = []
    pending_old: str | None = None
    pending_old_seen = False

    for line in patch_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = PATCH_PATH_RE.match(line)
        if not match:
            continue

        marker, raw_path = match.groups()
        parsed_path = None if raw_path == "/dev/null" else raw_path[2:]
        if marker == "---":
            pending_old = parsed_path
            pending_old_seen = True
            continue

        if not pending_old_seen:
            raise ValueError(f"Malformed patch header sequence in {patch_path}")

        entries.append(PatchEntry(old_path=pending_old, new_path=parsed_path))
        pending_old = None
        pending_old_seen = False

    if pending_old_seen:
        raise ValueError(f"Unterminated patch header sequence in {patch_path}")

    return entries


def collect_patch_entries(patch_paths: list[Path]) -> dict[Path, list[PatchEntry]]:
    return {patch_path: parse_patch_entries(patch_path) for patch_path in patch_paths}


def normalize_posix(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/"))


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def repo_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def unique_paths(paths: list[str]) -> list[str]:
    return sorted({normalize_posix(path) for path in paths})


class TarIndex:
    def __init__(self, tarball_path: Path):
        self.tarball_path = tarball_path
        self.raw_members_by_relative_path: dict[str, str] = {}
        self.candidates_by_basename: dict[str, list[str]] = defaultdict(list)
        self._build_index()

    def _build_index(self) -> None:
        result = subprocess.run(
            ["tar", "-tf", str(self.tarball_path)],
            capture_output=True,
            check=True,
            text=True,
        )
        for raw_line in result.stdout.splitlines():
            raw_member = raw_line.strip()
            if not raw_member or raw_member.endswith("/"):
                continue
            relative_path = self._normalize_member(raw_member)
            if relative_path is None:
                continue
            self.raw_members_by_relative_path[relative_path] = raw_member
            self.candidates_by_basename[PurePosixPath(relative_path).name].append(relative_path)

    @staticmethod
    def _normalize_member(raw_member: str) -> str | None:
        parts = [part for part in PurePosixPath(raw_member).parts if part not in ("", ".")]
        if len(parts) < 2:
            return None
        return PurePosixPath(*parts[1:]).as_posix()

    def has(self, relative_path: str) -> bool:
        return relative_path in self.raw_members_by_relative_path

    def basename_candidates(self, basename: str) -> list[str]:
        return self.candidates_by_basename.get(basename, [])

    def extract(self, relative_paths: list[str], destination: Path) -> None:
        raw_members = [self.raw_members_by_relative_path[path] for path in relative_paths]
        for raw_chunk in chunked(raw_members, 200):
            subprocess.run(
                [
                    "tar",
                    "-xJf",
                    str(self.tarball_path),
                    "-C",
                    str(destination),
                    "--strip-components=1",
                    *raw_chunk,
                ],
                check=True,
            )


class PatchVerifier:
    def __init__(self, metadata: BuildMetadata, options: VerifierOptions):
        self.metadata = metadata
        self.options = options
        self.patch_bin = shutil.which("gpatch") or shutil.which("patch") or "patch"
        self.patch_paths = active_patch_paths()
        self.patch_entries_by_path = collect_patch_entries(self.patch_paths)
        self.cache_dir = options.cache_dir
        self.tarball_path = self._resolve_tarball_path()
        self.tar_index = TarIndex(self.tarball_path)
        self.workdir = self._prepare_workdir()
        self.workspace_paths: set[str] = set()
        self.extracted_paths: set[str] = set()

    @staticmethod
    def progress() -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        )

    def _resolve_tarball_path(self) -> Path:
        if self.options.tarball is not None:
            console.print(f"[cyan]Using explicit tarball:[/cyan] {self.options.tarball}")
            return self.options.tarball

        local_tarball = REPO_ROOT / f"firefox-{self.metadata.version}.source.tar.xz"
        if local_tarball.exists():
            console.print(f"[cyan]Using local tarball:[/cyan] {local_tarball}")
            return local_tarball

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached_tarball = self.cache_dir / f"firefox-{self.metadata.version}.source.tar.xz"
        if cached_tarball.exists():
            console.print(f"[cyan]Using cached tarball:[/cyan] {cached_tarball}")
            return cached_tarball

        url = FIREFOX_TARBALL_URL.format(version=self.metadata.version)
        temp_tarball = cached_tarball.with_suffix(".tmp")
        console.print(f"[cyan]Downloading[/cyan] {url}")
        with urllib.request.urlopen(url) as response, temp_tarball.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        temp_tarball.replace(cached_tarball)
        console.print(f"[green]Cached tarball at[/green] {cached_tarball}")
        return cached_tarball

    def _prepare_workdir(self) -> Path:
        if self.options.workspace is not None:
            self.options.workspace.mkdir(parents=True, exist_ok=True)
            console.print(f"[cyan]Using workspace:[/cyan] {self.options.workspace}")
            return self.options.workspace

        workdir = Path(tempfile.mkdtemp(prefix="rotunda-patch-verify-"))
        console.print(f"[cyan]Created temp workspace:[/cyan] {workdir}")
        return workdir

    def register_workspace_file(self, relative_path: str) -> None:
        normalized = normalize_posix(relative_path)
        if normalized in self.workspace_paths:
            return
        self.workspace_paths.add(normalized)

    def workspace_file(self, relative_path: str) -> Path:
        return self.workdir / PurePosixPath(relative_path)

    def ensure_parent(self, relative_path: str) -> None:
        self.workspace_file(relative_path).parent.mkdir(parents=True, exist_ok=True)

    def extract_paths(self, relative_paths: list[str]) -> None:
        needed = sorted(
            {
                normalize_posix(path)
                for path in relative_paths
                if path not in self.extracted_paths and self.tar_index.has(path)
            }
        )
        if not needed:
            return
        self.tar_index.extract(needed, self.workdir)
        for path in needed:
            self.extracted_paths.add(path)
            self.register_workspace_file(path)

    def copy_file(self, source: Path, relative_destination: str) -> None:
        destination = self.workspace_file(relative_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        self.register_workspace_file(relative_destination)

    def copy_tree(self, source_root: Path) -> None:
        shutil.copytree(source_root, self.workdir, dirs_exist_ok=True)
        for path in source_root.rglob("*"):
            if path.is_file():
                relative_path = path.relative_to(source_root).as_posix()
                self.register_workspace_file(relative_path)

    def is_locally_seeded(self, relative_path: str) -> bool:
        normalized = normalize_posix(relative_path)
        if normalized in HELPER_SEEDED_PATHS:
            return True
        return (ADDITIONS_DIR / PurePosixPath(normalized)).is_file()

    def prepare_source_tree(self) -> None:
        patch_created_paths = {
            normalize_posix(entry.target_path)
            for entries in self.patch_entries_by_path.values()
            for entry in entries
            if entry.old_path is None and entry.target_path is not None
        }
        upstream_paths = sorted(
            {
                entry.extract_path
                for entries in self.patch_entries_by_path.values()
                for entry in entries
                if entry.extract_path is not None and not self.is_locally_seeded(entry.extract_path)
                and normalize_posix(entry.extract_path) not in patch_created_paths
            }
        )
        missing_paths = [path for path in upstream_paths if not self.tar_index.has(path)]
        if missing_paths:
            raise FileNotFoundError(
                "Firefox tarball is missing patch targets:\n" + "\n".join(missing_paths[:20])
            )

        console.print(f"[bold cyan]Extracting[/bold cyan] {len(upstream_paths)} upstream files")
        self.extract_paths(upstream_paths)

        console.print("[bold cyan]Copying additions and helper files[/bold cyan]")
        self.copy_tree(ADDITIONS_DIR)
        self.copy_file(
            ASSETS_DIR / "search-config.json",
            "services/settings/dumps/main/search-config.json",
        )
        self.copy_file(PATCHES_DIR / "librewolf" / "pack_vs.py", "build/vs/pack_vs.py")
        self.copy_file(SETTINGS_DIR / "rotunda.cfg", "lw/rotunda.cfg")
        self.copy_file(
            SETTINGS_DIR / "distribution" / "policies.json",
            "lw/policies.json",
        )
        self.copy_file(
            SETTINGS_DIR / "defaults" / "pref" / "local-settings.js",
            "lw/local-settings.js",
        )
        self.copy_file(SETTINGS_DIR / "chrome.css", "lw/chrome.css")
        self.copy_file(SCRIPTS_DIR / "mozfetch.sh", "lw/mozfetch.sh")
        self.ensure_parent("lw/moz.build")
        self.workspace_file("lw/moz.build").touch()
        self.register_workspace_file("lw/moz.build")

        for relative_path in (
            "browser/config/version.txt",
            "browser/config/version_display.txt",
        ):
            self.ensure_parent(relative_path)
            self.workspace_file(relative_path).write_text(
                f"{self.metadata.version}-{self.metadata.release}\n",
                encoding="utf-8",
            )
            self.register_workspace_file(relative_path)

        new_paths = [
            entry.target_path
            for entries in self.patch_entries_by_path.values()
            for entry in entries
            if entry.target_path is not None
        ]
        for relative_path in new_paths:
            self.workspace_file(relative_path).parent.mkdir(parents=True, exist_ok=True)

    def apply_patches(self) -> None:
        console.print(f"[bold cyan]Applying[/bold cyan] {len(self.patch_paths)} patches")
        console.print(f"[cyan]Using patch binary:[/cyan] {self.patch_bin}")
        failures: list[tuple[str, str]] = []
        with self.progress() as progress:
            task_id = progress.add_task("Applying patch set", total=len(self.patch_paths))
            for patch_path in self.patch_paths:
                patch_name = repo_relative(patch_path)
                progress.update(task_id, description=f"Applying {patch_name}")
                result = subprocess.run(
                    [
                        self.patch_bin,
                        "-p1",
                        "--batch",
                        "--forward",
                        "-l",
                        "--binary",
                        "-i",
                        str(patch_path),
                    ],
                    capture_output=True,
                    check=False,
                    cwd=self.workdir,
                    text=True,
                )
                if result.returncode != 0:
                    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
                    failures.append((patch_name, combined))
                progress.advance(task_id)

        if failures:
            for patch_name, output in failures:
                console.rule(f"[red]Patch failed[/red]: {patch_name}")
                if output:
                    console.print(output, markup=False)
            raise RuntimeError(f"{len(failures)} patch(es) failed to apply cleanly")

        for entries in self.patch_entries_by_path.values():
            for entry in entries:
                if entry.target_path is None:
                    continue
                path = self.workspace_file(entry.target_path)
                if path.is_file():
                    self.register_workspace_file(entry.target_path)

    def run_git(self, *args: str) -> str | None:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            check=False,
            cwd=REPO_ROOT,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def git_ref_exists(self, ref: str) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            capture_output=True,
            check=False,
            cwd=REPO_ROOT,
            text=True,
        )
        return result.returncode == 0

    def resolve_diff_base(self) -> str | None:
        # The fast verifier only wants to compile-check files touched by the
        # current change. On CI we prefer the PR merge-base, but when that
        # history is unavailable we gracefully fall back to whatever local git
        # state can describe "what changed" instead of inflating the scope back
        # to the whole Firefox patch stack.
        base_ref = os.environ.get("GITHUB_BASE_REF")
        if base_ref:
            remote_ref = f"origin/{base_ref}"
            if self.git_ref_exists(remote_ref):
                merge_base = self.run_git("merge-base", "HEAD", remote_ref)
                if merge_base:
                    return merge_base

        before_sha = os.environ.get("GITHUB_EVENT_BEFORE")
        if before_sha and before_sha != "0" * 40 and self.git_ref_exists(before_sha):
            return before_sha

        if self.git_ref_exists("HEAD^"):
            return "HEAD^"
        return None

    def collect_changed_repo_files(self) -> list[str]:
        changed: set[str] = set()

        status_output = self.run_git("status", "--porcelain", "--untracked-files=all")
        if status_output:
            for line in status_output.splitlines():
                if not line:
                    continue
                candidate = line[3:]
                if " -> " in candidate:
                    candidate = candidate.split(" -> ", 1)[1]
                changed.add(candidate)

        local_diff = self.run_git("diff", "--name-only", "HEAD", "--")
        if local_diff:
            changed.update(line for line in local_diff.splitlines() if line)

        staged_diff = self.run_git("diff", "--name-only", "--cached", "--")
        if staged_diff:
            changed.update(line for line in staged_diff.splitlines() if line)

        diff_base = self.resolve_diff_base()
        if diff_base is not None:
            range_diff = self.run_git("diff", "--name-only", f"{diff_base}...HEAD", "--")
            if range_diff:
                changed.update(line for line in range_diff.splitlines() if line)

        return sorted(changed)

    def collect_patch_overlay_and_targets(
        self,
        patch_path: Path,
        context: CompileCommandContext | None = None,
    ) -> tuple[list[str], list[str]]:
        overlay_paths: list[str] = []
        syntax_targets: list[str] = []

        for entry in self.patch_entries_by_path[patch_path]:
            if entry.target_path is None or entry.is_deleted:
                continue
            overlay_paths.append(entry.target_path)
            suffix = PurePosixPath(entry.target_path).suffix.lower()
            if suffix not in SOURCE_EXTENSIONS:
                continue
            if not self.workspace_file(entry.target_path).is_file():
                continue
            if context is not None and entry.target_path not in context.commands_by_relative_path:
                continue
            syntax_targets.append(entry.target_path)

        return unique_paths(overlay_paths), unique_paths(syntax_targets)

    def collect_syntax_scope(self) -> SyntaxScope:
        # The fast lane is intentionally diff-scoped. Re-validating every file
        # touched by the entire historical patch stack is what made the earlier
        # verifier drift into 20+ minute "mini build" territory. We only need
        # enough surface area to answer "did this push introduce obviously bad
        # patched C++?" before paying for the full Firefox build.
        changed_repo_files = self.collect_changed_repo_files()
        changed_repo_file_set = set(changed_repo_files)
        changed_patch_files = [
            patch_path
            for patch_path in self.patch_paths
            if repo_relative(patch_path) in changed_repo_file_set
        ]

        overlay_paths: list[str] = []
        syntax_targets: list[str] = []

        for patch_path in changed_patch_files:
            patch_overlay_paths, patch_syntax_targets = self.collect_patch_overlay_and_targets(patch_path)
            overlay_paths.extend(patch_overlay_paths)
            syntax_targets.extend(patch_syntax_targets)

        for repo_file in changed_repo_files:
            path = PurePosixPath(repo_file)
            if path.parts[:2] != BROWSERBUILD_ADDITIONS_REPO_PREFIX:
                continue
            relative_path = PurePosixPath(*path.parts[2:]).as_posix()
            overlay_paths.append(relative_path)
            if Path(relative_path).suffix.lower() in SOURCE_EXTENSIONS and self.workspace_file(relative_path).is_file():
                syntax_targets.append(relative_path)

        return SyntaxScope(
            changed_repo_files=changed_repo_files,
            changed_patch_files=changed_patch_files,
            overlay_paths=unique_paths(overlay_paths),
            syntax_targets=unique_paths(syntax_targets),
        )

    def collect_smoke_syntax_scope(
        self,
        context: CompileCommandContext,
        changed_repo_files: list[str],
        changed_patch_files: list[Path],
    ) -> SyntaxScope | None:
        # When a PR does not touch any patch-backed translation units we still
        # want one cheap compile-backed smoke test. That catches pipeline
        # regressions in the cached compile-command path itself without scaling
        # the fast lane back up to a broad Firefox syntax sweep.
        candidates: list[tuple[Path, list[str], list[str]]] = []
        for patch_path in self.patch_paths:
            overlay_paths, syntax_targets = self.collect_patch_overlay_and_targets(
                patch_path,
                context=context,
            )
            if not syntax_targets:
                continue
            candidates.append((patch_path, overlay_paths, syntax_targets))

        if not candidates:
            return None

        seed_material = (
            os.environ.get("GITHUB_SHA")
            or self.run_git("rev-parse", "HEAD")
            or f"{self.metadata.version}-{self.metadata.release}"
        )
        rng = random.Random(seed_material)
        patch_path, overlay_paths, syntax_targets = rng.choice(candidates)

        # We only compile one TU from the sampled patch to keep this fallback
        # cheap, but we keep the whole patch's overlay set so header edits from
        # that patch still affect the selected translation unit.
        sampled_target = rng.choice(syntax_targets)
        return SyntaxScope(
            changed_repo_files=changed_repo_files,
            changed_patch_files=changed_patch_files,
            overlay_paths=overlay_paths,
            syntax_targets=[sampled_target],
            sampled_patch=patch_path,
        )

    def detect_compile_command_context(self) -> CompileCommandContext | None:
        # Syntax checks for upstream Firefox files are only meaningful when we
        # can borrow compile flags, generated headers, and include paths from a
        # real prior build. Without that cached context we would either emit a
        # lot of false failures or rebuild enough of Firefox that this "fast"
        # lane stops being fast.
        source_tree = self.options.source_tree
        if source_tree is None:
            default_source_tree = REPO_ROOT / f"rotunda-{self.metadata.version}-{self.metadata.release}"
            if default_source_tree.exists():
                source_tree = default_source_tree

        compile_commands_dir = self.options.compile_commands_dir
        if compile_commands_dir is None and source_tree is not None:
            candidates = sorted(source_tree.glob("obj-*/clangd/compile_commands.json"))
            if candidates:
                compile_commands_dir = candidates[0].parent

        if compile_commands_dir is not None and source_tree is None:
            source_tree = compile_commands_dir.parent.parent

        if source_tree is None or compile_commands_dir is None:
            return None

        compile_commands_path = compile_commands_dir / "compile_commands.json"
        if not compile_commands_path.exists():
            return None

        commands_by_relative_path: dict[str, tuple[Path, list[str]]] = {}
        import json

        entries = json.loads(compile_commands_path.read_text(encoding="utf-8"))
        for entry in entries:
            file_path = Path(entry["file"])
            try:
                relative_path = file_path.relative_to(source_tree).as_posix()
            except ValueError:
                continue
            if "arguments" in entry:
                compile_command = list(entry["arguments"])
            else:
                compile_command = shlex.split(entry["command"])
            commands_by_relative_path[relative_path] = (Path(entry["directory"]), compile_command)

        return CompileCommandContext(
            source_tree=source_tree,
            compile_commands_dir=compile_commands_dir,
            commands_by_relative_path=commands_by_relative_path,
        )

    def build_overlay_dirs(self, scope: SyntaxScope) -> list[Path]:
        # We intentionally overlay only files touched by the current patch diff
        # onto a previously-built source tree. That keeps the check cheap while
        # still parsing the exact workspace versions of modified files. The
        # tradeoff is deliberate: we do not try to recreate a whole cold Firefox
        # objdir here, because that drifts too close to a full build.
        overlay_dirs = [self.workdir]
        for relative_path in scope.overlay_paths:
            candidate = self.workspace_file(relative_path).parent
            if candidate.exists():
                overlay_dirs.append(candidate)

        unique_dirs: list[Path] = []
        seen: set[Path] = set()
        for directory in overlay_dirs:
            if directory in seen:
                continue
            seen.add(directory)
            unique_dirs.append(directory)
        return unique_dirs

    def prepare_target(
        self,
        target: str,
        context: CompileCommandContext,
        overlay_dirs: list[Path],
    ) -> PreparedTarget | PreparationFailure:
        suffix = PurePosixPath(target).suffix.lower()
        if suffix in SKIPPED_SOURCE_EXTENSIONS:
            return PreparationFailure(
                target=target,
                reason="Objective-C or Objective-C++ sources are not portable in the fast verifier",
                skip=True,
            )

        if suffix not in SOURCE_EXTENSIONS:
            return PreparationFailure(
                target=target,
                reason="fast verifier only compile-checks .cpp/.cc/.cxx translation units",
                skip=True,
            )

        compile_entry = context.commands_by_relative_path.get(target)
        if compile_entry is None:
            return PreparationFailure(
                target=target,
                reason="no cached compile command exists for this file; full build must validate it",
                skip=True,
            )
        working_directory, compile_command = compile_entry

        workspace_file = self.workspace_file(target)
        if not workspace_file.exists():
            return PreparationFailure(
                target=target,
                reason="syntax target is missing from the prepared workspace",
                skip=False,
            )

        # We replay the original compiler invocation as faithfully as possible
        # and only change what is needed for a cheap front-end pass:
        # - add overlay include roots so patched files shadow the cached tree
        # - point the source path at the patched workspace file
        # - drop object output flags
        # - append -fsyntax-only to stop before codegen/linking
        #
        # Limitation: this only works for translation units that already have a
        # compile command in the cached build context. Brand-new upstream files
        # or header-only changes are intentionally left to the full build.
        command: list[str] = [compile_command[0]]
        for overlay_dir in overlay_dirs:
            command.extend(["-I", str(overlay_dir)])
        # Relocating a translation unit breaks its same-directory quoted
        # includes unless the original source directory remains searchable.
        command.extend(["-I", str((context.source_tree / PurePosixPath(target).parent))])

        skip_next = False
        original_source_file = (context.source_tree / PurePosixPath(target)).resolve()
        for arg in compile_command[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg == "-o":
                skip_next = True
                continue
            if arg == "-c":
                continue
            resolved_arg = Path(arg)
            if not resolved_arg.is_absolute():
                resolved_arg = (working_directory / resolved_arg).resolve()
            if resolved_arg == original_source_file:
                command.append(str(workspace_file))
                continue
            command.append(arg)

        command.append("-fsyntax-only")
        return PreparedTarget(
            display_path=target,
            compile_path=workspace_file,
            command=command,
            working_directory=working_directory,
        )

    def run_compiler(self, prepared: PreparedTarget) -> SyntaxResult:
        result = subprocess.run(
            prepared.command,
            capture_output=True,
            check=False,
            cwd=prepared.working_directory,
            text=True,
        )
        if result.returncode == 0:
            return SyntaxResult(display_path=prepared.display_path, status="passed")

        return SyntaxResult(
            display_path=prepared.display_path,
            status="failed",
            output="\n".join(part for part in (result.stdout, result.stderr) if part).strip(),
        )

    def run_syntax_checks(self) -> list[SyntaxResult]:
        # Limitations:
        # - This fast lane only checks translation units touched by the current
        #   patch diff, not every file in the whole Rotunda patch stack.
        # - It reuses compile flags and generated headers from a previously
        #   built source tree / syntax SDK.
        # - Header-only changes and brand-new files without cached compile
        #   commands are intentionally skipped here and remain the job of the
        #   authoritative full build.
        scope = self.collect_syntax_scope()
        console.print(
            f"[bold cyan]Collected syntax scope[/bold cyan] from {len(scope.changed_repo_files)} changed repo files "
            f"and {len(scope.changed_patch_files)} changed patch files"
        )

        context = self.detect_compile_command_context()
        if context is None:
            console.print(
                "[yellow]No cached compile-command context was found; patch application is verified, "
                "but compile-backed syntax checks are skipped.[/yellow]"
            )
            return []

        if not scope.syntax_targets:
            scope = self.collect_smoke_syntax_scope(
                context,
                changed_repo_files=scope.changed_repo_files,
                changed_patch_files=scope.changed_patch_files,
            ) or scope
            if not scope.syntax_targets:
                console.print(
                    "[yellow]No changed .cpp/.cc/.cxx files were found in the current patch scope, "
                    "and no compile-backed smoke target was available; skipping syntax checks.[/yellow]"
                )
                return []

            if scope.sampled_patch is not None:
                console.print(
                    "[yellow]No changed translation units were found in the current patch scope; "
                    f"running smoke syntax check on sampled patch {repo_relative(scope.sampled_patch)} "
                    f"via {scope.syntax_targets[0]}.[/yellow]"
                )

        overlay_dirs = self.build_overlay_dirs(scope)
        console.print(
            f"[bold cyan]Preparing syntax checks[/bold cyan] for {len(scope.syntax_targets)} changed translation units"
        )
        immediate_results: list[SyntaxResult] = []
        prepared_targets: list[PreparedTarget] = []

        with self.progress() as progress:
            task_id = progress.add_task("Preparing syntax targets", total=len(scope.syntax_targets))
            for target in scope.syntax_targets:
                prepared = self.prepare_target(target, context, overlay_dirs)
                if isinstance(prepared, PreparationFailure):
                    status = "skipped" if prepared.skip else "failed"
                    immediate_results.append(
                        SyntaxResult(
                            display_path=prepared.target,
                            status=status,
                            reason=prepared.reason,
                        )
                    )
                    progress.advance(task_id)
                    continue
                prepared_targets.append(prepared)
                progress.advance(task_id)

        if not prepared_targets:
            return immediate_results

        max_workers = max(1, min(self.options.jobs, len(prepared_targets)))
        console.print(
            f"[bold cyan]Running syntax checks[/bold cyan] on {len(prepared_targets)} prepared targets "
            f"with {max_workers} parallel jobs"
        )
        compiled_results: list[SyntaxResult] = []
        with self.progress() as progress:
            task_id = progress.add_task("Compiling syntax targets", total=len(prepared_targets))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(self.run_compiler, prepared) for prepared in prepared_targets]
                for future in as_completed(futures):
                    compiled_results.append(future.result())
                    progress.advance(task_id)

        return immediate_results + compiled_results

    def cleanup(self) -> None:
        if self.options.keep_workdir or self.options.workspace is not None:
            console.print(f"[yellow]Workspace preserved at[/yellow] {self.workdir}")
            return
        shutil.rmtree(self.workdir, ignore_errors=True)

    def run(self) -> int:
        try:
            self.prepare_source_tree()
            self.apply_patches()
            console.print("[green]Patch application passed[/green]")

            if self.options.skip_syntax:
                console.print("[yellow]Skipping syntax checks[/yellow]")
                return 0

            syntax_results = self.run_syntax_checks()
            passed = [result for result in syntax_results if result.status == "passed"]
            skipped = [result for result in syntax_results if result.status == "skipped"]
            failed = [result for result in syntax_results if result.status == "failed"]

            summary = Table(title="Syntax Summary")
            summary.add_column("Status", style="bold")
            summary.add_column("Count", justify="right")
            summary.add_row("[green]Passed[/green]", str(len(passed)))
            summary.add_row("[yellow]Skipped[/yellow]", str(len(skipped)))
            summary.add_row("[red]Failed[/red]", str(len(failed)))
            console.print(summary)
            if skipped:
                console.rule("[yellow]Skipped syntax targets[/yellow]")
                for result in skipped:
                    console.print(f"- {result.display_path}: {result.reason}")
            if failed:
                console.rule("[red]Failed syntax targets[/red]")
                for result in failed:
                    console.print(f"- {result.display_path}")
                    if result.reason:
                        console.print(f"  {result.reason}")
                    if result.output:
                        console.print(textwrap.indent(result.output, "  "), markup=False)
                return 1

            console.print("[green]Syntax checks passed[/green]")
            return 0
        finally:
            self.cleanup()


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path.home() / ".cache" / "rotunda" / "firefox-source",
    show_default=True,
    help="Directory for cached Firefox source tarballs.",
)
@click.option(
    "--tarball",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Use an existing Firefox source tarball instead of downloading one.",
)
@click.option(
    "--workspace",
    type=click.Path(path_type=Path, file_okay=False),
    help="Reuse or create a specific workspace directory instead of a temp dir.",
)
@click.option(
    "--source-tree",
    type=click.Path(path_type=Path, file_okay=False),
    help="Existing built Rotunda/Firefox tree to use as the cached syntax context.",
)
@click.option(
    "--compile-commands-dir",
    type=click.Path(path_type=Path, file_okay=False),
    help="Directory containing compile_commands.json for cached syntax checks.",
)
@click.option(
    "--keep-workdir",
    is_flag=True,
    help="Keep the temporary workspace after the run for debugging.",
)
@click.option(
    "--skip-syntax",
    is_flag=True,
    help="Only verify extraction and patch application.",
)
@click.option(
    "--jobs",
    type=click.IntRange(min=1),
    default=max(1, min(os.cpu_count() or 1, 8)),
    show_default=True,
    help="Maximum parallel syntax check jobs.",
)
def main(
    cache_dir: Path,
    tarball: Path | None,
    workspace: Path | None,
    source_tree: Path | None,
    compile_commands_dir: Path | None,
    keep_workdir: bool,
    skip_syntax: bool,
    jobs: int,
) -> None:
    metadata = parse_upstream_metadata()
    console.rule("[bold blue]Firefox Patch Verification[/bold blue]")
    console.print(
        f"Verifying Firefox [bold]{metadata.version}[/bold] with "
        f"Rotunda release [bold]{metadata.release}[/bold]"
    )
    options = VerifierOptions(
        cache_dir=cache_dir,
        tarball=tarball,
        workspace=workspace,
        source_tree=source_tree,
        compile_commands_dir=compile_commands_dir,
        keep_workdir=keep_workdir,
        skip_syntax=skip_syntax,
        jobs=jobs,
    )
    verifier = PatchVerifier(metadata=metadata, options=options)
    raise SystemExit(verifier.run())


if __name__ == "__main__":
    main()
