"""Read the `COPY` sources of an emitted Dockerfile, for the build-context tests of both emitters."""

from __future__ import annotations


def copy_sources(dockerfile_text: str) -> list[str]:
    """The source arguments of every `COPY` in a Dockerfile, ignoring `--flag` options and the dest."""
    sources: list[str] = []
    for line in dockerfile_text.splitlines():
        if line.strip().startswith("COPY "):
            arguments = [token for token in line.split()[1:] if not token.startswith("--")]
            sources.extend(arguments[:-1])  # every argument but the destination
    return sources
