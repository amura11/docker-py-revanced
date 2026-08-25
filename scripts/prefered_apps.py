"""Update preferred apps."""

import os

import dotenv
from loguru import logger


def update_patch_apps() -> None:
    """Point this run's apps at PREFERRED_PATCH_APPS without touching the full PATCH_APPS roster.

    PATCH_APPS_OVERRIDE narrows which apps get built this run; RevancedConfig falls back to the full
    PATCH_APPS roster whenever it's unset. Overwriting PATCH_APPS itself would permanently lose the
    full roster for anything (like the Obtainium export) that needs to know every app this project
    builds, not just the subset selected for a given partial run.
    """
    dotenv_file = dotenv.find_dotenv()
    dotenv.load_dotenv(dotenv_file)
    preferred_apps = os.environ["PREFERRED_PATCH_APPS"]
    logger.info(f"Overriding this run's apps to {preferred_apps}")

    dotenv.set_key(dotenv_file, "PATCH_APPS_OVERRIDE", preferred_apps)


if __name__ == "__main__":
    update_patch_apps()
