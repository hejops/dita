"""Parse user's config file"""
import configparser
import os
import sys

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
def load_titlecase_exceptions() -> dict[str, str]:
    try:
        with open(
            PATH + "/" + CONFIG["tag"]["titlecase_exceptions"],
            "r+",
            encoding="utf-8",
        ) as f:
            return {l.strip().lower(): l.strip() for l in f.readlines()}
    except FileNotFoundError:
        return {}


def load_staged_dirs() -> list[str]:
    try:
        with open(STAGED_FILE, "r+", encoding="utf-8") as f:
            # could be set[str]
            return f.read().splitlines()
    except FileNotFoundError:
        return []
