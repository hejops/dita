#!/usr/bin/env python3
"""
Use Discogs collection to select artist names for some file operation, e.g.
copying onto another drive. Assumes artist and directory names are mostly
equivalent.

To use with rsync:

    copy_artists.py > dirs.txt
    rsync -a --delete --files-from=dirs.txt <src> <dest>

or

    rsync -a --files-from=<(copy_artists.py) <src> <dest>

where <src> is typically the library root.

Note: symlinks are typically not supported on Android

"""
# from pprint import pprint
# from random import sample
import os
import sys
from pathlib import Path

import pandas as pd

from dita.config import TARGET_DIR
from dita.discogs.collection import get_percentiles
from dita.discogs.collection import group_collection_by_artist
from dita.discogs.collection import top_n_sum
from dita.discogs.core import DISCOGS_CSV
from dita.tagfuncs import eprint
from dita.tagfuncs import shallow_recurse

# from discogs.core import clean_artist


# will be replaced with glob_full
DIRNAMES_FOLD = {d.lower(): d for d in os.listdir(TARGET_DIR)}


def get_dirs_mb(dirs: list[str]) -> int:  # MB
    """Mimic du (unit: MB)"""
    return sum(
        f.stat().st_size
        #
        for _dir in dirs
        for f in Path(_dir).glob("**/*")
        if f.is_file()
    ) // (10**6)


def limit_albums_of_artist(
    all_dirs: list[str],
    max_mb: int = 10000,
) -> list[str]:
    """Limit the total dir size of any single artist to <max_mb>"""

    if get_dirs_mb(all_dirs) < max_mb:
        return all_dirs

    subset_mb = 0
    subset = set()
    all_dirs_set = set(all_dirs)
    while subset_mb < max_mb and all_dirs_set:
        alb = all_dirs_set.pop()
        subset.add(alb)
        subset_mb += get_dirs_mb([alb])
    # print(curr_mb, subset_mb)
    return list(subset)


def add_artists_with_translit(
    dirs: list[str],
    top_artists: list[str],
):
    """

    Artist names selected from library have transliterations, while artist
    names obtained via the Discogs collection do not. This puts back the
    'missed' artists from the collection back into the selection."""
    # (dir starts with artist + ' (')
    # this is very niche, only ~2 extra artists of note are caught this way
    # 'стекловата (steklovata)',
    # 'هاني شنودة (hani shenouda)',
    # and maybe kyary
    for art in top_artists:
        if art in dirs:
            dirs.append(DIRNAMES_FOLD[art])
        front_matches = [d for d in dirs if d.startswith(art + " (")]
        if front_matches:
            dirs.append(front_matches[0])
    return dirs


def main() -> list[str]:
    """Print paths (relative to library root) to be copied by rsync"""
    df = pd.read_csv(DISCOGS_CSV)

    one_5 = df[df.artist.map(df["artist"].value_counts() == 1)][df.r == 5][["artist"]]

    # perc: gb -> gb after outliers removed
    # 5: 285 -> 100 (too strict)
    # 10: 380 -> 160 (< 1 TB devices)
    # 20: 500 -> 225 (1-2 TB devices)
    # 30: 595 -> 285
    perc = 20 if TARGET_GB >= 1000 else 10

    df = group_collection_by_artist(
        pd.read_csv(DISCOGS_CSV),
        metric=lambda x: top_n_sum(x, strict=False, num=5),
    )
    df = pd.concat([one_5, get_percentiles(df)[df.perc <= perc]])

    # remove artists that do not have a dir (usually performers)
    has_dir = df.artist.str.lower().isin(DIRNAMES_FOLD)
    df = df[has_dir]

    # adjust artist case to its dirname equivalent
    df.artist = df.artist.apply(lambda x: DIRNAMES_FOLD[x.lower()])

    df["paths"] = df.artist.apply(lambda x: shallow_recurse(f"{TARGET_DIR}/{x}", 1))

    def outliers_present(max_weight: int = 4) -> bool:
        df["mb"] = df.paths.apply(get_dirs_mb)  # avoid .size (it is a pd attrib)
        df["mult"] = round(df.mb / df.mb.mean(), 1)
        return df.mult.max() > max_weight

    # iteratively shrink largest artist sizes towards mean
    while outliers_present():
        # print(df.mb.mean())
        df.paths = df.paths.apply(lambda x: limit_albums_of_artist(x, df.mb.mean()))

    assert df[df["paths"].eq(False)].empty

    # not strictly necessary
    df = df.set_index("artist")  # .drop("mult", axis=1)

    # # TODO:
    # perfs = df[~has_dir]
    # print(perfs)
    # raise ValueError
    # # "$HOME/.config/mpv/library" | grep perf

    # dirs_to_copy = add_artists_with_translit(dirs_to_copy)

    # rand = []
    # if ADD_RANDOM:
    #     # 10% of the rest
    #     remainder = [
    #         artist_fold[x]
    #         for x in set(artist_fold) - top_artists.intersection(artist_fold)
    #     ]
    #     rand += sample(
    #         remainder,
    #         int(len(remainder) * 0.1),
    #     )

    while df.mb.sum() > TARGET_MB:
        # sampling strategy for small TARGET_MB: drop random artists
        # don't drop albums, as i hate incomplete discographies
        df = df.sample(n=len(df) - 1)

    dirs = sorted(df.paths.explode().str.removeprefix(TARGET_DIR + "/").values)
    print("\n".join(dirs))

    # eprint(df.sort_values("mb"))
    eprint(df.drop("paths", axis=1).sort_index())
    eprint(df.mb.sum() // 1000, "GB")

    return dirs


if __name__ == "__main__":
    # target_gb refers to capacity of device
    TARGET_GB = int(sys.argv[1])
    if TARGET_GB < 250:
        TARGET_GB //= 2

    TARGET_MB = TARGET_GB * 1000
    ADD_RANDOM = TARGET_GB > 100
    main()
