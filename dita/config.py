"""Parse user's config file"""

import configparser
import os
import sys
from collections.abc import Iterator

PATH = os.path.dirname(__file__)
CONFIG_FILE = f"{PATH}/config"

CONFIG = configparser.ConfigParser()
CONFIG.read(f"{PATH}/config")


def init_config_value(
    section: str,
    subsection: str,
    description: str,
):
    if CONFIG[section].get(subsection):
        return
    if "pytest" in sys.modules:
        CONFIG[section][subsection] = "foo"
    else:
        CONFIG[section][subsection] = input(description + ": ")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        CONFIG.write(f)


for args in [
    ("paths", "source_dir", "Source directory"),
    ("paths", "target_dir", "Target directory"),
    ("discogs", "token", "Discogs API token"),
    ("discogs", "username", "Discogs username"),
]:
    init_config_value(*args)


SOURCE_DIR = CONFIG["paths"]["source_dir"]
TARGET_DIR = CONFIG["paths"]["target_dir"]

QUEUE_FILE = PATH + "/" + CONFIG["play"]["queue"]  # list of queued albums
STAGED_FILE = PATH + "/" + CONFIG["tag"]["staged"]  # list of successfully tagged albums


# boilerplate file loads
def load_titlecase_exceptions() -> Iterator[tuple[str, str]]:
    try:
        with open(
            PATH + "/" + CONFIG["tag"]["titlecase_exceptions"],
            "r+",
            encoding="utf-8",
        ) as f:
            # return {l.strip().lower(): l.strip() for l in f.readlines()}
            for line in f.readlines():
                yield line.strip().lower(), line.strip()
    except FileNotFoundError:
        pass
        # return {}


TITLECASE_EXCEPTIONS = load_titlecase_exceptions()


def load_staged_dirs() -> list[str]:
    # assert os.path.isfile(STAGED_FILE)
    # print("loading", STAGED_FILE)
    try:
        with open(STAGED_FILE, "r+", encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []
