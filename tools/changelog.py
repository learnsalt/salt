"""
These commands are used manage Salt's changelog.
"""

# pylint: disable=resource-leakage,broad-except,3rd-party-module-not-gated
from __future__ import annotations

import datetime
import logging
import os
import pathlib
import sys
import textwrap

from jinja2 import Environment, FileSystemLoader
from ptscripts import Context, command_group
from ptscripts.models import VirtualEnvPipConfig

from tools.utils import REPO_ROOT, Version

log = logging.getLogger(__name__)

# Define the command group
changelog = command_group(
    name="changelog",
    help="Changelog tools",
    description=__doc__,
    venv_config=VirtualEnvPipConfig(
        requirements_files=[
            REPO_ROOT
            / "requirements"
            / "static"
            / "ci"
            / "py{}.{}".format(*sys.version_info)
            / "changelog.txt",
        ],
    ),
)


def _get_changelog_contents(ctx: Context, version: Version):
    """
    Return the full changelog generated by towncrier.
    """
    ret = ctx.run(
        "towncrier",
        "build",
        "--draft",
        f"--version={version}",
        capture=True,
        check=False,
    )
    if ret.returncode:
        ctx.error(ret.stderr.decode())
        ctx.exit(1)
    return ret.stdout.decode()


def _get_pkg_changelog_contents(ctx: Context, version: Version):
    """
    Return a version of the changelog entries suitable for packaged changelogs.
    """
    changes = _get_changelog_contents(ctx, version)
    changes = "\n".join(changes.split("\n")[2:])
    changes = changes.replace("### ", "# ").replace("\n\n\n", "\n\n")
    return changes


def _get_salt_version(ctx, next_release=False):
    args = []
    if next_release:
        args.append("--next-release")
    ret = ctx.run("python3", "salt/version.py", *args, capture=True, check=False)
    if ret.returncode:
        ctx.error(ret.stderr.decode())
        ctx.exit(1)
    return Version(ret.stdout.decode().strip())


@changelog.command(
    name="update-rpm",
    arguments={
        "salt_version": {
            "help": (
                "The salt package version. If not passed "
                "it will be discovered by running 'python3 salt/version.py'."
            ),
            "nargs": "?",
            "default": None,
        },
        "draft": {
            "help": "Do not make any changes, instead output what would be changed.",
        },
    },
)
def update_rpm(ctx: Context, salt_version: Version, draft: bool = False):
    if salt_version is None:
        salt_version = _get_salt_version(ctx)
    changes = _get_pkg_changelog_contents(ctx, salt_version)
    str_salt_version = str(salt_version).replace("rc", "~rc")
    ctx.info(f"Salt version is {str_salt_version}")
    orig = ctx.run(
        "sed",
        f"s/Version: .*/Version: {str_salt_version}/g",
        "pkg/rpm/salt.spec",
        capture=True,
        check=True,
    ).stdout.decode()
    dt = datetime.datetime.utcnow()
    date = dt.strftime("%a %b %d %Y")
    header = f"* {date} Salt Project Packaging <saltproject-packaging@vmware.com> - {str_salt_version}\n"
    parts = orig.split("%changelog")
    tmpspec = "pkg/rpm/salt.spec.1"
    with open(tmpspec, "w", encoding="utf-8") as wfp:
        wfp.write(parts[0])
        wfp.write("%changelog\n")
        wfp.write(header)
        wfp.write(changes)
        wfp.write(parts[1])
    try:
        with open(tmpspec, encoding="utf-8") as rfp:
            if draft:
                ctx.info(rfp.read())
            else:
                with open("pkg/rpm/salt.spec", "w", encoding="utf-8") as wfp:
                    wfp.write(rfp.read())
    finally:
        os.remove(tmpspec)


@changelog.command(
    name="update-deb",
    arguments={
        "salt_version": {
            "help": (
                "The salt package version. If not passed "
                "it will be discovered by running 'python3 salt/version.py'."
            ),
            "nargs": "?",
            "default": None,
        },
        "draft": {
            "help": "Do not make any changes, instead output what would be changed.",
        },
    },
)
def update_deb(ctx: Context, salt_version: Version, draft: bool = False):
    if salt_version is None:
        salt_version = _get_salt_version(ctx)
    changes = _get_pkg_changelog_contents(ctx, salt_version)
    formated = "\n".join([f"  {_.replace('-', '*', 1)}" for _ in changes.split("\n")])
    dt = datetime.datetime.utcnow()
    date = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
    tmpchanges = "pkg/rpm/salt.spec.1"
    debian_changelog_path = "pkg/debian/changelog"
    tmp_debian_changelog_path = f"{debian_changelog_path}.1"
    with open(tmp_debian_changelog_path, "w", encoding="utf-8") as wfp:
        wfp.write(f"salt ({salt_version}) stable; urgency=medium\n\n")
        wfp.write(formated)
        wfp.write(
            f"\n -- Salt Project Packaging <saltproject-packaging@vmware.com>  {date}\n\n"
        )
        with open(debian_changelog_path, encoding="utf-8") as rfp:
            wfp.write(rfp.read())
    try:
        with open(tmp_debian_changelog_path, encoding="utf-8") as rfp:
            if draft:
                ctx.info(rfp.read())
            else:
                with open(debian_changelog_path, "w", encoding="utf-8") as wfp:
                    wfp.write(rfp.read())
    finally:
        os.remove(tmp_debian_changelog_path)


@changelog.command(
    name="update-release-notes",
    arguments={
        "salt_version": {
            "help": (
                "The salt version used to generate the release notes. If not passed "
                "it will be discovered by running 'python3 salt/version.py'."
            ),
            "nargs": "?",
            "default": None,
        },
        "draft": {
            "help": "Do not make any changes, instead output what would be changed.",
        },
        "release": {
            "help": "Update for an actual release and not just a temporary CI build.",
        },
        "template_only": {
            "help": "Only generate a template file.",
        },
        "next_release": {
            "help": "Generate release notes for the next upcoming release.",
        },
    },
)
def update_release_notes(
    ctx: Context,
    salt_version: Version,
    draft: bool = False,
    release: bool = False,
    template_only: bool = False,
    next_release: bool = False,
):
    if salt_version is None:
        salt_version = _get_salt_version(ctx, next_release=next_release)
    changes = _get_changelog_contents(ctx, salt_version)
    changes = "\n".join(changes.split("\n")[2:])
    if salt_version.local:
        # This is a dev release, let's pick up the latest changelog file
        versions = {}
        for fpath in pathlib.Path("doc/topics/releases").glob("*.md"):
            versions[(Version(fpath.stem))] = fpath
        latest_version = sorted(versions)[-1]
        release_notes_path = versions[latest_version]
        version = ".".join(str(part) for part in latest_version.release)
    else:
        version = ".".join(str(part) for part in salt_version.release)
        release_notes_path = pathlib.Path("doc/topics/releases") / "{}.md".format(
            version
        )

    template_release_path = (
        release_notes_path.parent / "templates" / f"{version}.md.template"
    )
    if not template_release_path.exists():
        template_release_path.write_text(
            textwrap.dedent(
                f"""\
                (release-{salt_version})=
                # Salt {salt_version} release notes{{{{ unreleased }}}}
                {{{{ warning }}}}

                <!--
                Add release specific details below
                -->

                <!--
                Do not edit the changelog below.
                This is auto generated.
                -->
                ## Changelog
                {{{{ changelog }}}}
                """
            )
        )
        ctx.run("git", "add", str(template_release_path))
        ctx.info(f"Created template {template_release_path} release notes file")
        if template_only:
            # Only generate the template for a new release
            return

    unreleased = " - UNRELEASED"
    warning = """
<!---
Do not edit this file. This is auto generated.
Edit the templates in doc/topics/releases/templates/
for a given release.
-->
    """
    if release is True:
        unreleased = ""

    tmp_release_notes_path = (
        release_notes_path.parent / f"{release_notes_path.name}.tmp"
    )

    # render the release notes jinja template
    environment = Environment(loader=FileSystemLoader(template_release_path.parent))
    template = environment.get_template(template_release_path.name)
    content = template.render(
        {"changelog": changes, "unreleased": unreleased, "warning": warning}
    )

    tmp_release_notes_path.write_text(content)
    try:
        contents = tmp_release_notes_path.read_text().strip()
        if draft:
            ctx.print(contents, soft_wrap=True)
        else:
            new_release_file = False
            if not release_notes_path.exists():
                new_release_file = True
            release_notes_path.write_text(contents)
            if new_release_file:
                ctx.run("git", "add", str(release_notes_path))
                ctx.info(f"Created bare {release_notes_path} release notes file")
    finally:
        os.remove(tmp_release_notes_path)


@changelog.command(
    name="update-changelog-md",
    arguments={
        "salt_version": {
            "help": (
                "The salt version to use in the changelog. If not passed "
                "it will be discovered by running 'python3 salt/version.py'."
            ),
            "nargs": "?",
            "default": None,
        },
        "draft": {
            "help": "Do not make any changes, instead output what would be changed.",
        },
    },
)
def generate_changelog_md(ctx: Context, salt_version: Version, draft: bool = False):
    if salt_version is None:
        salt_version = _get_salt_version(ctx)
    cmd = ["towncrier", "build", f"--version={salt_version}"]
    if draft:
        cmd.append("--draft")
    elif salt_version.is_prerelease:
        cmd.append("--keep")
    else:
        cmd.append("--yes")
    ctx.run(*cmd, check=True)
    ctx.run("git", "restore", "--staged", "CHANGELOG.md", "changelog/", check=True)
