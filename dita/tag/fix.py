"""CLI for writing Discogs tags to directories."""

import os
import sys

from dita.config import CONFIG
from dita.config import PATH
from dita.config import SOURCE_DIR
from dita.config import TARGET_DIR
from dita.config import load_staged_dirs
from dita.discogs.core import d_get
from dita.file import mover
from dita.tag.io import glob_full
from dita.tag.io import shallow_recurse
from dita.tag.tagger import Tagger

STAGED_DIRS = load_staged_dirs()
TTY = sys.__stdin__.isatty()


def dump_staged_dirs() -> None:
    """Write list of dirs that were cleared (whether automatically or manually)
    to file.
    """
    from dita.config import STAGED_FILE

    with open(STAGED_FILE, "w+", encoding="utf-8") as fobj:
        fobj.writelines({line + "\n" for line in STAGED_DIRS})


def dump_library_dirs() -> None:
    """Can be extremely fast when warm -- <1s for 56k (depth 2), otherwise < 6
    min.

    Since this is so fast, it might be worth considering using
    `shallow_recurse` for `dump_library_genres`.
    """
    assert CONFIG["play"]["database"]
    db_path = PATH + "/" + CONFIG["play"]["database"]

    dirs = shallow_recurse(TARGET_DIR)
    dirs = sorted(d.removeprefix(f"{TARGET_DIR}/") + "\n" for d in dirs)

    with open(db_path, "w+", encoding="utf-8") as f:
        f.writelines(dirs)


def tag_all(dirs_to_tag: list[str]) -> None:
    """Initialise a Tagger on all directories.

    Recursion is not performed. Can automate tagging without user input.
    """
    auto = not TTY

    if STAGED_DIRS:
        pass

    staged = len(STAGED_DIRS)
    total = len(dirs_to_tag)

    # TODO: check discogs auth

    for i, _dir in enumerate(sorted(dirs_to_tag)):
        print(_dir)
        if total > 1 and auto:
            if auto:
                print(f"({staged})", end=" ")
            print(f"{i}/{total}")

        album = Tagger(_dir, TTY)

        if not album.ready:
            continue

        if TTY and album.files:
            album.repl()  # when user leaves menu, self.staged is set to True

        if album.quit:
            dump_staged_dirs()
            sys.exit(1)  # break?

        # / avoids false negatives (if SOURCE_DIR contains TARGET_DIR)
        if _dir.startswith(TARGET_DIR + "/"):
            mover.Mover(_dir).move()

        elif TTY or album.staged:
            staged += 1
            STAGED_DIRS.append(_dir)

    if TTY:
        print(f"All {len(dirs_to_tag)} directories processed")
    else:
        print(f"{staged}/{len(dirs_to_tag)} directories passed")


def main() -> None:
    assert SOURCE_DIR
    assert TARGET_DIR

    # if len(sys.argv) == 2 and sys.argv[-1] == "-h":
    #     print("help message")
    #     return

    if len(sys.argv) == 2 and sys.argv[-1] in ["-a", "--auto"]:
        sys.argv.pop()
        global TTY
        TTY = False

    if len(sys.argv) == 1:
        dirs = glob_full(SOURCE_DIR)
        dirs = [d for d in dirs if d not in STAGED_DIRS]
        if not dirs:
            sys.exit(0)

    elif len(sys.argv) == 2 and os.path.isdir(sys.argv[1]):
        dirs = [os.path.realpath(sys.argv[1])]

    else:
        if os.path.isfile(sys.argv[1]):
            pass

        elif len(sys.argv) == 2 and os.path.isdir(art := f"{TARGET_DIR}/{sys.argv[1]}"):
            mover.multi_move(glob_full(art, dirs_only=True))

        # last resort: multiple dirs already in library
        elif len(sys.argv) > 2:
            mover.multi_move(sys.argv[1:])

        sys.exit(0)

    try:
        # https://www.discogs.com/forum/thread/1084315
        if len(dirs) > 1:
            assert "sub_tracks" in d_get(6538534)["tracklist"][0], (
                "discogs tracklist unusable for now"
            )

        tag_all(dirs)
        dump_staged_dirs()
    except Exception:
        dump_staged_dirs()
        raise


if __name__ == "__main__":
    main()
