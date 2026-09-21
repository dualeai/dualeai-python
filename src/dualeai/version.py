"""Version information for the Duale AI SDK package."""

import importlib.metadata


def get_version(package_name: str = "dualeai", fallback: str = "0.0.0-unknown") -> str:
    """Get the version of a package using importlib.metadata.

    Args:
        package_name: The name of the package to get the version for.
        fallback: The fallback version if the package is not found.

    Returns:
        The version string of the package.
    """
    try:
        return importlib.metadata.version(package_name)
    except (importlib.metadata.PackageNotFoundError, ValueError):
        return fallback


__version__ = get_version("dualeai", fallback="0.0.0-unknown")
