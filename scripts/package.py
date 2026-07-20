#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "click>=8.1",
# ]
# ///

import glob
import os
import shutil
import tempfile
from pathlib import Path
from shlex import join

import click

from _mixin import BROWSERBUILD_DIR, find_src_dir, get_moz_target, list_files, run, temp_cd

UNNEEDED_PATHS = {'uninstall', 'pingsender.exe', 'pingsender', 'vaapitest', 'glxtest'}

_MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def _looks_executable(path: Path) -> bool:
    if not path.is_file():
        return False

    try:
        with path.open("rb") as handle:
            header = handle.read(8)
    except OSError:
        return False

    if header.startswith(b"#!"):
        return True
    if header[:4] == b"\x7fELF":
        return True
    if header[:4] in _MACH_O_MAGICS:
        return True
    return False


def _restore_executable_bits(root: str) -> None:
    for path in Path(root).rglob("*"):
        if not _looks_executable(path):
            continue
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)


def add_includes_to_package(package_file, includes, fonts, new_file, target):
    with tempfile.TemporaryDirectory() as temp_dir:
        # Extract package
        run(join(['7z', 'x', package_file, f'-o{temp_dir}']), exit_on_fail=False)
        # Delete package_file
        os.remove(package_file)
        if package_file.endswith('.tar.xz'):
            # Rerun on the tar file
            package_tar = package_file[:-3]  # Remove ".xz"
            return add_includes_to_package(
                package_file=os.path.join(temp_dir, package_tar),
                includes=includes,
                fonts=fonts,
                new_file=new_file,
                target=target,
            )

        if target == 'macos':
            # Move Rotunda/Rotunda.app -> Rotunda.app
            nightly_dir = os.path.join(temp_dir, 'Rotunda')
            shutil.move(
                os.path.join(nightly_dir, 'Rotunda.app'), os.path.join(temp_dir, 'Rotunda.app')
            )
            # Remove old app dir and all content in it
            shutil.rmtree(nightly_dir)
        else:
            # Move contents out of rotunda folder if it exists
            old_rotunda_dir = os.path.join(temp_dir, 'rotunda')
            rotunda_dir = os.path.join(temp_dir, 'rotunda-folder')
            if os.path.exists(old_rotunda_dir):
                # Rename rotunda_dir
                os.rename(old_rotunda_dir, rotunda_dir)
                for item in os.listdir(rotunda_dir):
                    shutil.move(os.path.join(rotunda_dir, item), temp_dir)
                os.rmdir(rotunda_dir)

        # Create target_dir
        if target == 'macos':
            target_dir = os.path.join(temp_dir, 'Rotunda.app', 'Contents', 'Resources')
        else:
            target_dir = temp_dir

        # Add includes
        for include in includes or []:
            if os.path.exists(include):
                if os.path.isdir(include):
                    shutil.copytree(
                        include,
                        os.path.join(target_dir, os.path.basename(include)),
                        dirs_exist_ok=True,
                    )
                else:
                    shutil.copy2(include, target_dir)

        # Add the font folders under fonts/
        fonts_dir = os.path.join(target_dir, 'fonts')
        if target == 'linux':
            for font in fonts or []:
                shutil.copytree(
                    os.path.join(BROWSERBUILD_DIR, 'bundle', 'fonts', font),
                    os.path.join(fonts_dir, font),
                    dirs_exist_ok=True,
                )
        # Non-linux systems cannot read fonts within subfolders.
        # Instead, we walk the fonts/ directory and copy all files.
        else:
            os.makedirs(fonts_dir, exist_ok=True)
            for font in fonts or []:
                for file in list_files(
                    root_dir=os.path.join(BROWSERBUILD_DIR, 'bundle', 'fonts', font),
                    suffix='*',
                ):
                    shutil.copy2(file, os.path.join(fonts_dir, os.path.basename(file)))

        # Remove unneeded paths
        for path in UNNEEDED_PATHS:
            if os.path.isdir(os.path.join(target_dir, path)):
                shutil.rmtree(os.path.join(target_dir, path), ignore_errors=True)
            elif os.path.exists(os.path.join(target_dir, path)):
                os.remove(os.path.join(target_dir, path))

        # 7z extraction does not reliably preserve executable bits on Unix
        # binaries, which breaks helper subprocess launches after re-zipping.
        _restore_executable_bits(temp_dir)

        # Update package
        run(join(['7z', 'u', new_file, f'{temp_dir}/*', '-r', '-mx=9']))


@click.command()
@click.argument("target_os", metavar="OS", type=click.Choice(["linux", "macos", "windows"]))
@click.option(
    "--include",
    "--includes",
    "includes",
    multiple=True,
    help="File or directory to include in the package. Can be repeated.",
)
@click.option("--version", required=True, help="Rotunda version.")
@click.option("--release", required=True, help="Rotunda release number.")
@click.option("--arch", type=click.Choice(["x86_64", "arm64"]), help="Target architecture.")
@click.option(
    "--font",
    "--fonts",
    "fonts",
    multiple=True,
    help="Font directory under browserbuild/bundle/fonts to include. Can be repeated.",
)
def main(
    target_os: str,
    includes: tuple[str, ...],
    version: str,
    release: str,
    arch: str | None,
    fonts: tuple[str, ...],
) -> None:
    """Package Rotunda for different operating systems."""
    # Determine file extension based on OS
    file_extensions = {'linux': 'tar.xz', 'macos': 'dmg', 'windows': 'zip'}
    file_ext = file_extensions[target_os]

    # Build the package
    src_dir = find_src_dir('.', version, release)
    moz_target = get_moz_target(target=target_os, arch=arch)
    with temp_cd(src_dir):
        # Create package files
        run('./mach package')
        # Find package files
        search_path = os.path.abspath(
            f'obj-{moz_target}/dist/rotunda-{version}-{release}.*.{file_ext}'
        )

    # Copy package files
    for file in glob.glob(search_path):
        if 'xpt_artifacts' in file:
            print(f'Skipping xpt artifacts: {file}')
            continue
        print(f'Found package: {file}')
        # Copy to root
        shutil.copy2(file, '.')
        break
    else:
        raise click.ClickException(f"No package file found matching pattern: {search_path}")

    # Find the package file
    package_pattern = f'rotunda-{version}-{release}.en-US.*.{file_ext}'
    package_files = glob.glob(package_pattern)
    if not package_files:
        raise click.ClickException(f"No package file found matching pattern: {package_pattern}")
    package_file = package_files[0]

    # Add includes to the package
    new_name = f'rotunda-{version}-{release}-{target_os[:3]}.{arch}.zip'
    add_includes_to_package(
        package_file=package_file,
        includes=includes,
        fonts=fonts,
        new_file=new_name,
        target=target_os,
    )

    print(f"Packaging complete for {target_os}")


if __name__ == '__main__':
    main()
