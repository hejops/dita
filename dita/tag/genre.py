#!/usr/bin/env python3
"""

Module for reading/writing genre tags to MP3 files. The 'genre' tag receives
special treatment because it is somewhat subjective, and Discogs is largely
unconcerned with defining specific genres. Last.fm is generally better for
obtaining this information, but care must be taken with the data that is
available there.

"""

import argparse
import http.client as httplib
import json
import os
import readline
import sys
import urllib.parse

import pandas as pd
import requests
from Levenshtein import distance as levdist
from mutagen.easyid3 import EasyID3
from mutagen.id3._util import ID3NoHeaderError
from titlecase import titlecase

from dita.config import CONFIG
from dita.config import PATH
from dita.config import SOURCE_DIR
from dita.config import TARGET_DIR
from dita.file.convert import glob_full
from dita.tag.core import add_headers
from dita.tag.core import file_to_tags
from dita.tag.core import select_from_list
from dita.tag.core import set_tag
from dita.tag.io import get_audio_files
from dita.tag.io import shallow_recurse

GENRES_FILE = PATH + "/" + CONFIG["tag"]["genres"]
GENRE_SUFFIXES = CONFIG["tag"]["genre_suffixes_to_remove"].split(",")
LASTFM_TOKEN = CONFIG["lastfm"]["token"]


def have_internet() -> bool:
    """Check if internet connection available (via Google DNS)"""
    # https://stackoverflow.com/a/29854274
    conn = httplib.HTTPSConnection("8.8.8.8", timeout=1)
    try:
        conn.request("HEAD", "/")
        return True
    except (KeyboardInterrupt, OSError):
        return False
    finally:
        conn.close()


# CONNECTED = have_internet()


def dump_library_genres():
    """Recurse through library and dump a csv with columns 'artist',
    'genre'."""
    # 29681 artists = 26 mins
    dirs = shallow_recurse(TARGET_DIR)
    print(dirs[0])
    raise ValueError


if os.path.isfile(GENRES_FILE):
    GENRES_DF = pd.read_csv(
        GENRES_FILE,
        sep=",",
        index_col="artist",
    )  # .drop_duplicates()
    # https://stackoverflow.com/a/34297689
    GENRES_DF: pd.DataFrame = GENRES_DF[~GENRES_DF.index.duplicated(keep="first")]
else:
    if sys.__stdin__.isatty() and input(f"{GENRES_FILE} not found. Build?") == "y":
        dump_library_genres()
        sys.exit()
    else:
        GENRES_DF = pd.DataFrame(columns=["artist", "genre"])

GENRES: list[str] = GENRES_DF.genre.to_list()  # imported by mover only

# print(GENRES)
# raise ValueError


def fix_library_genres():
    """Find artists with 'non-standard' genre tags and rectify them."""
    genre_counts = GENRES_DF.groupby("genre").apply(len).sort_values()
    bad = genre_counts[genre_counts < 4].index.to_list()
    artists_to_fix = GENRES_DF[GENRES_DF.genre.isin(bad)].index.to_list()
    for art in artists_to_fix:
        path = f"{TARGET_DIR}/{art}"
        process_dirs(path, interactive=True, no_auto=True)


def get_closest_string(text: str) -> list[str]:
    """Return string matches within a Levenshtein distance"""

    genres: pd.DataFrame = GENRES_DF[["genre"]].drop_duplicates().reset_index(drop=True)

    # when input is short, use normal front-matching
    if len(text) < 5:
        return genres[genres.genre.str.startswith(text)].genre.to_list()

    genres["dist"] = genres.genre.apply(lambda x: levdist(x, text))
    return (
        genres[
            (genres.dist <= min(len(text) // 2, 5))
            & (genres.genre.str.len() >= len(text))
        ]
        # .sort_values("dist", ascending=True)	# readline sorts it by force anyway
        .genre.to_list()
    )


def completer(
    text: str,
    state: int,
    # fuzzy: bool = True,
) -> str | None:
    """Simple completer for CLI tab completion"""
    # https://github.com/prompt-toolkit/python-prompt-toolkit#installation
    # https://pymotw.com/2/readline/#completing-text
    # https://stackoverflow.com/a/5638688
    # https://docs.python.org/3/library/readline.html#readline.set_completer

    options = get_closest_string(text)

    if len(options) > state:
        return options[state]
    return None


def get_genre(file: str) -> str:
    """get genre tag of file"""
    if not os.path.isfile(file):
        return ""
    if genre := file_to_tags(file).get("genre"):
        return genre[0].strip()
    return ""


def get_lastfm_genres(artist: str) -> list[str]:
    """get genre tags from lastfm that are also used by user"""

    def remove_words(gen: str) -> str:
        return " ".join(w for w in gen.split() if w not in GENRE_SUFFIXES)

    print(artist)
    artist = urllib.parse.quote_plus(artist)

    try:
        # < 1 s
        url = (
            "https://ws.audioscrobbler.com/2.0/?method=artist.getTopTags"
            f"&api_key={LASTFM_TOKEN}&artist={artist}&format=json"
        )
        jsond: dict = json.loads(
            requests.get(url, allow_redirects=True, timeout=1).text
        )
    except KeyboardInterrupt:
        return []

    # pprint(jsond)

    try:
        tags_df = pd.DataFrame(jsond["toptags"]["tag"])
    except KeyError:
        return []

    if tags_df.empty:
        return []

    genres = tags_df.name.apply(titlecase)
    return list(set(genres.apply(remove_words)).intersection(GENRES))[:10]


def save_db(new: pd.DataFrame = None):
    """Write genres df to file (default location: /tag/genres_library.csv)"""
    if new is not None:
        pd.concat([GENRES_DF, new]).to_csv(GENRES_FILE)
    else:
        GENRES_DF.to_csv(GENRES_FILE)


def get_reference_genre(artist: str) -> str:
    """Get genre of a given artist based on library/json. No setting of tags is
    done.

    Args:
        artist: [TODO:description]

    Returns:
        str:
        Returns empty string if no match found.

    """

    if artist in GENRES_DF.index:
        # print(
        #     GENRES_DF.loc[artist].genre,
        #     # GENRES_DF.loc[artist].genre.values[0],
        # )
        result = GENRES_DF.loc[artist].genre  # .values[0]
        # print(result.genre)
        # raise ValueError
        source = "database"

    # library check can be very slow when scanning large artists, and is
    # generally less likely to succeed

    elif os.path.isdir(_dir := f"{TARGET_DIR}/{artist}"):
        found_files = glob_full(_dir, recursive=True, first_match="mp3")
        if not found_files:
            return ""
        library_genre = get_genre(found_files[0])
        source = "library"
        result = library_genre

    else:
        return ""

    assert isinstance(result, str), result

    print(f"Found artist in {source}: ({result}, {artist})")
    return result


def prompt_genre(
    _dir: str,
    tags_list: list[EasyID3],
    artist: str,
    curr_genre: str,
) -> None:
    """Retrieve tags from last.fm and ask for user input."""

    print()
    if curr_genre in GENRES:
        print(f"[Current tag: {curr_genre}]")

    if have_internet() and LASTFM_TOKEN:
        lastfm_genres = get_lastfm_genres(artist)

        if curr_genre in lastfm_genres:
            lastfm_genres.remove(curr_genre)
            lastfm_genres.insert(0, curr_genre)  # move to front
        elif curr_genre in GENRES:
            lastfm_genres.insert(0, curr_genre)  # move to front

    else:
        raise NotImplementedError
        lastfm_genres = GENRES

    if PLAYER:
        try:
            PLAYER.play(_dir)  # requires python-mpv 1.0.6
        except ValueError:
            pass

    if lastfm_genres:
        input_genre = select_from_list(
            items=lastfm_genres,
            msg=f"Genre for {artist}",
            sep=" / ",
        )
    else:
        input_genre = input(f"Genre for {artist}: ")

    if not input_genre:
        if curr_genre and (gen := titlecase(curr_genre)) in GENRES:
            input_genre = gen.removesuffix(" Metal")
        else:
            input_genre = input(f"Genre for {artist}: ")
            if not input_genre:
                input_genre = curr_genre

    if PLAYER:
        PLAYER.stop()

    assert input_genre in GENRES, input_genre

    GENRES_DF.at[artist, "genre"] = input_genre
    # GENRES_DF.loc[artist] = input_genre
    # print(GENRES_DF.loc[artist])

    for tags in tags_list:
        set_tag(tags, "genre", input_genre)

    print()


def process_dirs(
    root_dir: str,
    interactive: bool = True,
    no_auto: bool = False,
):
    """Iterates through subdirectories, attempts to overwrite genre tags if
    artist exists in the library csv.

    Folder-based iteration is a 'legacy' decision that was made to prevent
    every file triggering the library check.

    Args:
        root_dir: [TODO:description]
        interactive: [TODO:description]
    """

    def try_auto(artist: str) -> bool:
        """Expect errors to be raised here!"""
        if no_auto:
            return False

        if "artist" not in first_track_tags:
            return False

        artist = first_track_tags["artist"][0]
        ref_genre = get_reference_genre(artist)
        if ref_genre:
            for f in files:
                set_tag(file_to_tags(f), "genre", ref_genre)
            return True
        return False

    dirs = glob_full(root_dir=root_dir)
    # raise ValueError

    if not dirs:
        print("Nothing to do")
        sys.exit()

    num_dirs = len(dirs)
    print(f"{num_dirs} dirs found")

    success = 0

    for i, _dir in enumerate(dirs):
        print(f"{i + 1}/{num_dirs}: {os.path.basename(_dir)[:60]}")

        files = get_audio_files(_dir)

        if not files:
            continue

        # pprint(files)

        tags_list = []
        try:
            # 1. read 1st file tags (must read all if no headers)
            first_track_tags = file_to_tags(files[0])
            if not first_track_tags:
                continue
            artist = first_track_tags["artist"][0]
            # 2. artist's genre matches reference value
            # best case; iteration ends here
            if try_auto(artist):
                success += 1
                continue
            tags_list = [file_to_tags(f) for f in files]

        except (ID3NoHeaderError, TypeError):
            add_headers(files)
            if not interactive:
                continue

        except KeyError:  # no 'artist' field
            continue

        if not tags_list or not (first_track_tags := tags_list[0]):
            continue

        # 3. artist field should not be empty; this is actually caught in try_auto
        artist = first_track_tags["artist"]

        if try_auto(artist):
            success += 1
            continue

        if interactive:
            prompt_genre(
                _dir=_dir,
                tags_list=tags_list,
                artist=artist[0],
                curr_genre=get_genre(files[0]),
            )

    print(f"{success}/{num_dirs} OK")


def parse_args():
    parser = argparse.ArgumentParser()
    args = {
        "--dump": {"action": "store_true"},
        "--auto": {"action": "store_true"},
        "--directory": {"action": "store_const", "default": SOURCE_DIR},
    }

    for arg, arg_opts in args.items():
        parser.add_argument(arg, **arg_opts)

    return parser.parse_args()


def main():
    global PLAYER
    assert os.path.isdir(TARGET_DIR)

    args = parse_args()
    if not os.path.isdir(args.directory):
        return
    if args.dump:
        dump_library_genres()
        return

    if not args.auto:
        import mpv  # python-mpv

        # os.system("waitdie mpv ; vol --auto")
        # default = '`~!@#$%^&*()-=+[{]}\|;:'",<>/?'    # note how space is not included!
        readline.set_completer_delims("\t\n;")
        readline.parse_and_bind("tab: complete")
        readline.set_completer(completer)
        PLAYER = mpv.MPV(ytdl=False)
        PLAYER["video"] = "no"
        PLAYER["start"] = "50%"
        PLAYER["input-ipc-server"] = "/tmp/mp_pipe"

    try:
        process_dirs(
            args.directory,
            interactive=not args.auto,
        )
        save_db()

    # note: Exception does not handle KeyboardInterrupt
    except KeyboardInterrupt:
        print("Killed")
        save_db()
        sys.exit(1)

    except Exception as e:
        save_db()
        raise e


if __name__ == "__main__":
    PLAYER = None

    main()
