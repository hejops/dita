import itertools
import os
from glob import glob

import filetype
import pandas as pd
import psutil
from mutagen.mp3 import MP3

from dita.tag.core import eprint


def durations_match(
    file_durations: list[int],
    discogs_tags: pd.DataFrame,
    max_dur_diff: int = 15,
) -> bool:
    """Only if not tty"""

    # TODO: try levenshtein dist

    if not all(file_durations):
        eprint("Warning: Some file durations blank\n")
        return False

    if not any(discogs_tags.dur):
        eprint("No durations listed\n")
        return False

    if not all(discogs_tags.dur):
        eprint("Incomplete durations listed\n")
        return False

    # lprint(discogs_durations)
    discogs_tags["dur_diff"] = discogs_tags.dur - file_durations

    if (
        abs(max(discogs_tags.dur_diff)) > max_dur_diff
        and abs(sum(discogs_tags.dur) - sum(file_durations)) > max_dur_diff * 2
    ):
        print("File durations do not match discogs tracklist")
        print(discogs_tags[discogs_tags.dur_diff > max_dur_diff][["title", "dur_diff"]])
        return False

    return True


def get_file_durations(files: list[str]) -> list[int]:
    """MP3(file) can fail, even if tags are valid!"""
    durs = []
    for file in files:
        try:
            durs.append(int(MP3(file).info.length))
        except KeyboardInterrupt:
            durs.append(0)

    return durs


def file_in_use(fpath: str) -> bool:
    """Mimic `tail --pid=<pid> -f /dev/null`"""
    # ~ 0.3 s
    # https://stackoverflow.com/a/44615315
    for proc in psutil.process_iter():
        try:
            for item in proc.open_files():
                if fpath == item.path:
                    return True
        except psutil.AccessDenied:
            pass
    return False


def shallow_recurse(
    parent: str,
    maxdepth: int = 2,
) -> list[str]:
    """Breadth-first search algorithm with upper bound on depth. Returns list
    of full paths."""
    if maxdepth == 0:
        return [parent]

    children = [d.path for d in os.scandir(parent) if d.is_dir()]

    while maxdepth > 1:
        grandch = [shallow_recurse(chi, maxdepth - 1) for chi in children]
        grandch = list(itertools.chain.from_iterable(grandch))
        return grandch

    return children


def glob_full(
    root_dir: str,
    recursive: bool = True,
    dirs_only: bool = True,
    first_match: str = "",
    mindepth: int = 1,
) -> list[str]:
    """Attempts to mimic the general functionality of GNU find, with the
    exception of -maxdepth.

    Methods like os.listdir are annoying to use because root_dir is not
    included (and has to be rejoined to all results). This takes care of that
    problem.

    TODO: os.scandir

    Returns deepest directories by default (to avoid duplication). For files,
    use get_audio_files() instead.

    Args:

    Returns:
    """
    # basically just listdir
    if not recursive:
        return [p.path for p in os.scandir(root_dir)]

    # # don't think this does anything
    # if mindepth == 0:
    #     return [root_dir]

    items = glob(
        "**",
        root_dir=root_dir,
        recursive=True,
    )
    # print(items)
    # assert False

    if first_match:
        gen = (x for x in items if x.endswith(first_match))
        try:
            return [os.path.join(root_dir, next(gen))]
        except StopIteration:
            return []

    if mindepth > 1:
        # depth 0 is root
        # depth 1 has 0 slashes
        # lprint(items)
        items = [x for x in items if x.count("/") >= mindepth]
        items = [os.path.join(root_dir, x) for x in items]
        return items

    items = [os.path.join(root_dir, x) for x in items]

    if dirs_only:
        # deepest only
        new_list = []
        for item in reversed(sorted(items)):
            # a/b/c
            # a/b
            # a
            if any(item in x for x in new_list):
                continue
            if not os.path.isdir(item):
                continue
            new_list.append(item)
        return new_list
        # return [x for x in items if os.path.isdir(x)]

    return sorted(
        [
            x
            for x in items
            # if os.path.isfile(x)
            # allow dead symlinks (will be cleared by is_audio_file)
            if not os.path.isdir(x)
            # a rather absurd corner case caused by 2 files containing a word
            # which was encoded differently but displayed the same. "Güld'ner"
            # is the 'invalid' encoding, as it contains an invisible
            # Nonspacing_Mark -- https://www.compart.com/en/unicode/category/Mn
            #
            # however, because some files will naturally have such chars, they
            # should not automatically be ruled out.
            # and not any(unicodedata.category(c) == "Mn" for c in x)
        ]
    )


def is_audio_file(
    file: str,
    extensions: list[str],
) -> bool:
    """Check that `file`:
        1. is a file
        2. has correct filename extension (string)
        3. has correct file magic numbers (binary)

    As there is no stdlib module for #3, an external dependency is required
    (https://github.com/h2non/filetype.py). This is faster than the similar
    python-magic, but can yield false negatives (e.g. ape).
    """
    # m4a ext = mp4 filetype
    if "m4a" in extensions:
        extensions.append("mp4")

    ext = file.split(".")[-1].lower()

    # TODO: uhh why is this here?
    if ext == "ape":
        return True

    if os.path.isfile(file) and ext in extensions:
        # if the byte check fails, expect to see it caught by any media player.
        # it is not our responsibility to fix this

        if Path(file).stat().st_size == 0:
            return False

        # in rare cases, guess_extension can produce false positive (header is
        # present, but file is truncated), leading to failure on MP3(f). in
        # this case, check the last 16 bytes (which should all be 0xa or 0x5)
        with open(file, "rb") as f:
            # print(file)
            f.seek(-16, os.SEEK_END)
            fb = f.read()
            if len(set(fb)) > 1:
                eprint("File has corrupt tail:", file)
                return False

        if (e := filetype.guess_extension(file)) and e in extensions:
            # print("ok", file)
            return True

        eprint("bad file header:", file)
        return False

    if os.path.islink(file) and not os.path.isfile(file):
        eprint("Removing broken symlink:", file)
        os.unlink(file)
        return False

    return False


def get_audio_files(src_dir: str) -> list[str]:
    """Return a sorted list of audio files in a directory (non-recursive by
    default, i.e. top-level), omitting hidden files (starting with '.').

    Wrapper for glob_full + is_audio_file.
    """
    files = glob_full(
        src_dir,
        recursive=True,
        dirs_only=False,
    )
    return sorted(f for f in files if is_audio_file(f, ["mp3"]))
