#!/usr/bin/env python3
"""Module for writing Discogs tags to directories."""
import os
import readline  # pylint: disable=unused-import
import shlex
import shutil
import subprocess as sp
import sys
import time
import unicodedata
from shutil import rmtree

import pandas as pd
import readchar
import requests
from mutagen import File
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from natsort import natsort_keygen  # , natsorted
from numpy import nan
from titlecase import titlecase

from dita.config import CONFIG
from dita.config import load_staged_dirs
from dita.config import PATH
from dita.config import SOURCE_DIR
from dita.config import STAGED_FILE
from dita.config import TARGET_DIR
from dita.discogs import artist
from dita.discogs import release
from dita.discogs.core import cli_search
from dita.discogs.core import d_get
from dita.discogs.core import display_release_results
from dita.discogs.core import search_release
from dita.file import mover
from dita.tag.core import align_lists
from dita.tag.core import eprint
from dita.tag.core import FIELD_ALIASES
from dita.tag.core import file_to_tags
from dita.tag.core import glob_full
from dita.tag.core import input_with_prefill
from dita.tag.core import is_ascii
from dita.tag.core import is_audio_file
from dita.tag.core import open_url
from dita.tag.core import save_tags
from dita.tag.core import select_from_list
from dita.tag.core import set_tag
from dita.tag.core import shallow_recurse
from dita.tag.core import tcase_with_exc

STAGED_DIRS = load_staged_dirs()

REQUIRED_FIELDS = {
    "album",
    "artist",
    "date",
    "title",
    "tracknumber",
}


def tags_to_columns(val):
    if not val:
        return None
    if isinstance(val, list):
        return val[0]
    return val


def row_to_tags(row: pd.Series):
    for f in REQUIRED_FIELDS:
        assert isinstance(row[f], str)
        row.tags[f] = row[f]
    save_tags(row.tags)


class Tagger:
    """Tagger object initialised by directory string. All data (including
    Mutagen tags) is contained within the attribute 'df'.

    Always attempts automatic Discogs tagging. Contains a barebones REPL that
    moves only forwards through directories.
    """

    def __init__(  # {{{
        self,
        album_dir: str,
    ):
        self.staged = False
        self.files: list[str] = []  # only used for dur, bitrate, and delete/move
        self.meta: dict[str, str] = {}

        if not os.path.isdir(album_dir):
            print("Directory does not exist:", album_dir)
            return

        self.album_dir = album_dir

        # nests should already be flattened by convert
        # don't filter by filetype yet!
        all_files = glob_full(
            self.album_dir,
            dirs_only=False,
        )

        self.df = pd.DataFrame(
            [
                {"file": f, "tags": file_to_tags(f)}
                for f in all_files
                if os.path.isfile(f)
            ],
        )

        # better than lambda i guess
        # note: index.map() doesn't have args keyword!

        if self.df.empty or not self.df.file.apply(is_audio_file, args=["mp3"]).any():
            # raise ValueError
            return

        self.df.set_index("file", inplace=True)
        self.df.sort_index(key=natsort_keygen(), inplace=True)

        if not self.df.tags.isna().empty:
            self.df.tags = self.df.index.map(self.add_headers)

        self.df.dropna(subset=["tags"], inplace=True)

        if self.df.empty:
            return

        self.regen_tag_columns()

        # not entirely uncommon
        if any(
            dups := self.df[(self.df.index.str.endswith(" (1).mp3"))].index.to_list()
        ):
            for f in dups:
                os.remove(f)
            self.__init__(self.album_dir)

        # partially converted
        if any(self.df.index.map(lambda x: is_audio_file(x, ["flac", "m4a"]))):
            print("convert in progress", album_dir)
            return

        # partially downloaded
        # https://stackoverflow.com/a/32566415
        if (
            self.album_dir.startswith(SOURCE_DIR)
            and "album" in self.df.columns
            and any(self.df.album != "")
            and (alb := self.df.album.replace("", nan).dropna().iloc[0])
            and sp.getoutput(
                # TODO: prone to false positive, esp if short album title
                f"mediainfo {os.path.dirname(SOURCE_DIR)}/downloading/* | "
                f"grep -F {shlex.quote(alb)}"
            )
        ):
            # raise ValueError

            print("download in progress", album_dir)
            return

        self.files = self.df.index.to_list()

        # for all files, not all columns present
        if not REQUIRED_FIELDS.issubset(set(self.df.columns)):
            self.df.tags = self.df.index.map(self.add_headers)
            self.regen_tag_columns()
            # print("added tags")

        # for any file, not all columns present
        missing = self.df[list(REQUIRED_FIELDS)].isna().any(axis=1)
        if missing.any():
            print(
                self.df[missing].iloc[0],
            )
            # TODO: bfill only meaningful for fields with shared val
            self.df = self.df.bfill()
            self.df.apply(row_to_tags, axis=1)
            self.__init__(self.album_dir)

        # tracknumber will never be modified by discogs
        def set_tracknum(tracknum, tags):
            tags["tracknumber"] = tracknum
            save_tags(tags)

        # in principle, log10 should be used, but in practice 95% of albums are
        # <100, and -none- are >1000
        self.df.tracknumber = [
            str(i + 1).zfill(2 + (len(self.df) > 99)) for i in range(len(self.df))
        ]
        self.df.apply(lambda x: set_tracknum(x.tracknumber, x.tags), axis=1)

        if self.files and not self.df.tags.empty:
            self.staged = self.try_auto()
        else:
            os.system(f"ls -A {shlex.quote(self.album_dir)}")
            os.system(f"rm -rIv {shlex.quote(self.album_dir)}")
            return

    # }}}

    @staticmethod
    def add_headers(file: str):  # -> EasyMP3
        tags = File(file, easy=True)
        if tags is None:
            return
        if not tags:
            print(tags)
            tags.add_tags()
        else:
            for field in REQUIRED_FIELDS:
                if field not in tags:
                    tags[field] = ""
        save_tags(tags)
        return tags

    def regen_tag_columns(self) -> None:
        """
        Generate a new df from tags, and overwrite existing columns

            df.update does NOT create new columns
            df.combine_first only overwrites null columns
        """

        # https://github.com/pandas-dev/pandas/issues/39531#issuecomment-771346521
        right = self.df.tags.apply(pd.Series)
        self.df = self.df[self.df.columns.difference(right.columns)]
        # print(right, self.df)
        self.df = self.df.merge(right, left_index=True, right_index=True).applymap(
            tags_to_columns
        )

    def try_auto(self) -> bool:  # {{{
        """
        For an automatic match, the following four conditions are required:

            1. The existing tags produce at least one Discogs search result
            2. Number of files matches number of tracks in Discogs tracklist
            3. Durations match
            4. All Discogs artist names are at least partially ascii

        Any condition failure will result in an early return (False).

        """

        def set_reason(reason: str):  # TODO: use Enum
            # print(
            #     len(self.results),
            #     self.results.iloc[idx],
            #     self.results.columns,
            # )

            self.results.iat[idx, self.results.columns.get_loc("reason")] = reason

        if all(self.df.title.str.endswith(".mp3")):
            for t in self.df.tags:
                t["title"] = t["title"][0].removesuffix(".mp3").partition(" ")[2]
                save_tags(t)

        self.summarize()

        results = search_release(
            artist=self.meta["artist"],
            album=self.meta["album"],
            interactive=False,
        )
        if not results:
            self.results = pd.DataFrame()
            return False

        self.results = pd.DataFrame(results)

        # deleted releases may persist in search results before truly getting
        # deleted. they can be distinguished by having an empty 'thumb' field
        # (but i could be wrong...)
        # https://api.discogs.com/releases/14685597
        self.results = self.results[self.results["thumb"] != ""].reset_index(drop=True)

        self.results["reason"] = ""

        file_durations = get_file_durations(self.files)

        # for i, result in enumerate(results):
        for idx, result in self.results.iterrows():
            # print(self.results)
            if not TTY:
                # 2s is long enough to avoid getting rate limited
                time.sleep(2)

            if result.type == "master":
                continue

            rel = d_get(result["resource_url"])

            if "title" not in rel:
                continue

            # deleted release = { 'message' : 'Release not found' }
            # should probably not be triggered
            if "uri" not in rel:
                # raise ValueError
                continue

            print()
            print(idx)
            print(rel["uri"].partition("-")[0])
            print(rel["title"])

            # while we only really need the tracklist for len and dur checks,
            # the tags are used for diagnostics, e.g. align_lists
            discogs_tags = release.get_discogs_tags(rel)

            if discogs_tags.empty:
                continue

            if not self.trans_ok(discogs_tags, rel):
                # self.foo("translit")
                return False

            if len(self.df) != len(discogs_tags):
                aligned = align_lists(
                    discogs_tags.title.to_list(),
                    self.df.title.to_list(),
                )
                df = pd.DataFrame(aligned)
                df = df[df.columns[df.nunique() > 1]].T
                df.index += 1
                # print(df)

                # note: this check can be ignored in repl, to allow shared
                # fields (artist, album) to be edited

                print(
                    "Release contains",
                    len(discogs_tags),
                    "tracks (vs",
                    len(file_durations),
                    "files)\n",
                )

                set_reason("unequal len")
                continue

            if not durations_match(
                discogs_tags=discogs_tags, file_durations=file_durations
            ):
                set_reason("dur")
                continue

            # not a fan of this side effect tbh
            if "cover_image" in rel:
                img_url = rel["cover_image"]
                with open(f"{self.album_dir}/folder.jpg", "wb") as fobj:
                    fobj.write(requests.get(img_url, timeout=3).content)

            # all checks passed
            self.apply_discogs_tags(discogs_tags, rel)
            print("ok\n")

            return True

        # all results exhausted

        return False

    # }}}

    def trans_ok(
        self,
        discogs_tags,
        rel: dict,
    ) -> bool:
        """Check for 'artist' values that are not ASCII.

        Modifies columns, but not tags"""

        # https://www.discogs.com/release/12168132

        if all(is_ascii(x) for x in discogs_tags.artist):
            return True

        transliterations = artist.get_transliterations(rel)
        discogs_tags = release.apply_transliterations(transliterations, discogs_tags)

        if not all(is_ascii(x) for x in discogs_tags.artist):
            print("no trans")
            return False

        # elif (
        #     # 1 transliteration per artist
        #     all(len(x) == 1 for x in transliterations.values())
        #     # all artists have
        #     and len(transliterations) == len(set(discogs_tags.artist))
        # ):
        #     # print(123)
        #     # get(x, x) -- if name not in dict, default to name
        #     discogs_tags.artist = discogs_tags.artist.apply(
        #         lambda x: f"{x} ({transliterations[x.lower()][0]})"
        #     )

        # elif (
        #     # 1 transliteration per artist
        #     all(len(x) == 1 for x in transliterations.values())
        #     # all artists have 1 translit
        #     and len(transliterations) == len(set(discogs_tags.artist))
        # ):
        #     discogs_tags = discogs.release.apply_transliterations(
        #         transliterations, discogs_tags
        #     )
        #     if all(tag.core.is_ascii(x) for x in discogs_tags.artist):
        #         return True
        #
        # elif TTY:
        #     # print(transliterations)
        #     # foo = transliterations.copy()
        #     for native, trans_l in transliterations.items():
        #         if len(trans_l) == 1:
        #             trans = trans_l[0]
        #         elif not trans_l:
        #             # if artist["profile"]:
        #             #     eprint(artist["profile"])
        #             print("No transliterations found:")
        #             open_url("https://duckduckgo.com/?t=ffab&q=", native)
        #             trans = input(f"Provide transliteration for {native}: ")
        #         else:
        #             trans: str = select_from_list(trans_l, "Select transliteration")
        #
        #         n_trans = f"{native} ({trans})"
        #         discogs_tags.artist = discogs_tags.artist.apply(
        #             lambda n: n.lower().replace(native, n_trans)
        #         )

        discogs_tags.artist = discogs_tags.artist.apply(tcase_with_exc)

        return True

    def summarize(self):  # {{{
        """Store metadata in self.meta, a struct-like wrapper, for ease of
        constructing Discogs search queries. The function should be called
        after any change in tags (e.g. apply_discogs_tags). The metadata itself
        should not be used for tagging, especially if >1 artist.
        """

        self.regen_tag_columns()

        first = self.df.iloc[0]

        self.meta = {
            "artist": first["artist"],
            "album": first["album"],
        }

        # self.meta["date"] = self.df.date.dropna().iloc[0]

        if "date" in first:
            self.meta["date"] = first["date"]
        else:
            self.meta["date"] = 0

        # print(self.meta)

    # }}}

    def display_tracks(  # {{{
        self,
        durations: list[int] = None,
    ) -> None:
        """
        Summarize file tags. Example:

        Metallica
        Master of Puppets
        1986

        1 	Battery
        2 	Master of Puppets
        3 	The Thing That Should Not Be
        ...
        """

        def print_safe(_str: str) -> None:
            """Removes characters in non-standard (non-utf-8) encodings (e.g.
            ) before printing. Although this tends to lead to loss of
            characters, not performing this removal can lead to an unusable
            screen, a far more serious outcome that must be avoided."""
            # print(_str.encode("latin-1", "ignore").decode("utf-8", "ignore"))
            print("".join(c for c in str(_str) if unicodedata.category(c) != "Cc"))

        # print(self.df.artist)

        print()
        for val in self.meta.values():
            print_safe(val)
        print()

        if durations:
            for (i, title), dur in zip(enumerate(self.df.title), durations):
                print(
                    i + 1,
                    time.strftime("%M:%S", time.gmtime(dur)),
                    title,
                    sep="\t",
                )
        else:
            # for i, tags in enumerate(self.df.tags):
            for i, title in enumerate(self.df.title):
                print(i + 1, "\t", end="")
                print_safe(title)

        print()

        artists = set(self.df.artist.dropna())
        if len(artists) > 1:
            print("Compilation:", ", ".join(artists))

    # }}}

    def apply_from_id(self) -> None:  # {{{
        """Attempt to read clipboard for a release/master id ('Copy Release
        Code' in Discogs). If this fails, fallback to user input.
        """
        if _id := get_clipboard():
            ...
        elif not (_id := input("Release id: ")):
            return
        if _id.startswith("[r"):
            _id = _id[2:-1]
        rel = d_get(_id)
        discogs_tags = release.get_discogs_tags(release=rel)
        # lprint(discogs_tags)
        self.apply_discogs_tags(discogs_tags, rel)

    # }}}

    def search_and_replace(self):  # {{{
        """Modify existing field values to construct a new Discogs search
        query. Interface implementation is in cli_search().
        """
        result_ids = cli_search(
            artist=f"{self.meta['artist'].replace('/',' ')}",
            album=f"{self.meta['album'].replace('/',' ')}",
            # date=self.meta["date"],
        )
        rel = display_release_results(
            result_ids,
            num_tracks=len(self.df),
        )

        if rel:
            self.apply_discogs_tags(release.get_discogs_tags(rel), rel)
        else:
            print("nothing selected")
            # continue

    # }}}

    def apply_discogs_tags(  # {{{
        self,
        discogs_tags: pd.DataFrame,
        rel: dict,
    ) -> None:
        """Apply a fully processed discogs tracklist (df) to the existing
        'tags' column. No checks for file/metadata correctness are done here;
        these should all be done prior.

        Args:
            discogs_tags: from discogs (after processing -- independent of files)

        Returns:
            None
        """

        self.trans_ok(discogs_tags, rel)

        # if not self.trans_ok(discogs_tags, rel):
        #     return

        if len(self.df) != len(discogs_tags):
            # TODO: redirect master id to release id, more difficult than it seems
            print(
                (
                    "Tracklist lengths do not match: "
                    f"{len(self.df)} vs {len(discogs_tags)}"
                ),
                discogs_tags,
            )
            # allow writing artist/album fields, but not track titles
            for tags in self.df.tags:
                set_tag(tags, "artist", discogs_tags.artist.iloc[0])
                set_tag(tags, "album", discogs_tags.album.iloc[0])
            return

        # # أسامة الرحباني Featuring هبة طوجي -> ... (abc) Featuring ... (def)
        # # https://www.discogs.com/release/17698879
        # if len(artists_dict) > 1 and discogs_tags.artist.nunique() == 1:
        #     curr = discogs_tags.artist.iloc[0]
        #     for k, val in transliterations.items():
        #         if val not in curr:
        #             curr = curr.replace(k, val)
        #     discogs_tags.artist = curr

        # print(discogs_tags.date)
        # raise ValueError

        fields = discogs_tags.columns.to_list()
        comp = "1" if len(set(discogs_tags.artist)) > 1 else ""
        for tags, (_, df_row) in zip(
            self.df.tags,
            discogs_tags.iterrows(),
        ):
            for field in fields:
                # if field == "artist":
                #     print(df_row[field])
                if field not in tags:
                    continue
                set_tag(tags, field, df_row[field])

            set_tag(tags, "compilation", comp)
            # print(tags["artist"])

        print()

        self.summarize()

        assert self.df.iloc[0].artist == self.df.iloc[0].tags["artist"][0]

    # }}}

    def select_from_results(self):  # {{{
        """Select Discogs release from search results (the warning that
        prevented automatic tagging will be ignored)"""
        # note: set in try_auto
        if self.results.empty:
            print("nothing to do")
            return
        # print(self.results.columns)
        _id: pd.Series = select_from_list(
            # self.results[["id", "year", "format"]],
            self.results[["id", "format", "reason"]],
            "Release id",
        )
        # print(
        #     _id,
        #     type(_id),  # Series
        # )
        if isinstance(_id.id, pd.Series):
            rel = d_get(int(_id.id.iloc[0]), verbose=True)
        else:
            rel = d_get(int(_id.id), verbose=True)
        discogs_tags = release.get_discogs_tags(release=rel)
        self.apply_discogs_tags(discogs_tags, rel)

    # }}}

    def delete(self):
        """Delete current directory, as well as all parent directories after
        following any symlinks"""
        for _dir in {os.path.dirname(os.path.realpath(f)) for f in self.files}:
            assert (
                _dir != TARGET_DIR
            ), f"FATAL! Will not delete library root: {TARGET_DIR}"
            rmtree(_dir)
            print("Removed:", _dir)

    def repl(self):  # {{{
        """
        May migrate to curses interface eventually.

        Currently implemented actions:

            - space: Mark album as tagged, advance to next one in directory

            CLI
            - s: Search in discogs
            - i: Apply discogs tracklist using release ID
            - e: Edit tag (artist/album/date/genre)
            - c: Split composer and performer(s)

            Web
            - S: Search in discogs (web)
            - I: Apply discogs tracklist using release ID (clipboard/manual)

            etc:
            - r: Re-sort
            - p: Print tracklist
            - D: Delete directory without confirmation
            - l: List files (ls -1)
            - x: Exit
            - h: Help
        """

        if not self.files:
            return

        self.summarize()

        bitrate = MP3(self.files[0]).info.bitrate // 1000  # pylint: disable=no-member
        if bitrate % 32 == 0:
            eprint(f"CBR: {bitrate} kbps")

        if 0 in (zero_dur := [MP3(x).info.length for x in self.files]):
            eprint(f"Warning: {zero_dur.count(0)} tracks have zero duration")

        self.display_tracks()

        while action := readchar.readchar():
            match action:
                case " ":
                    self.staged = True
                    os.system("clear")
                    break

                case "s":
                    self.search_and_replace()

                case "S":
                    print({unicodedata.category(c) for c in self.meta["artist"]})
                    # bad {'No', 'Sm'}
                    # good {'Lu', 'Zs', 'Ll', 'Po', 'Lo'}

                    if not self.meta["artist"]:
                        query = [self.album_dir.split("/")[-1]]

                    else:
                        query = [self.meta["artist"], self.meta["album"]]

                    open_url(
                        "https://www.discogs.com/search/?format_exact=CD&type=release&q=",
                        query,
                    )

                case "i":
                    self.select_from_results()

                case "I":
                    self.apply_from_id()

                ###

                case "t":
                    for tag in self.df.tags.to_list():
                        tag["title"] = titlecase(tag["title"][0])
                        tag["album"] = titlecase(tag["album"][0])

                        tag["artist"] = tcase_with_exc(tag["artist"][0])
                        save_tags(tag)
                    self.summarize()

                case "e":
                    edit_tag(self.df.tags)
                    self.summarize()

                case "c":
                    (
                        self.meta["artist"],
                        self.meta["album"],
                    ) = split_composer_and_performers(
                        self.meta["artist"],
                        self.meta["album"],
                    )
                    for tag in self.df.tags:
                        set_tag(tag, "album", self.meta["album"])
                        set_tag(tag, "artist", self.meta["artist"])
                        save_tags(tag)

                case "p":
                    self.display_tracks()

                case "l":
                    # i don't really need filesize (or any other info)
                    # print(self.album_dir)
                    # sp.call(f'ls -1N "{self.album_dir}" | sort -V', shell=True)
                    os.system(f'exa --tree "{self.album_dir}"')
                    os.system(f'cat "{self.album_dir}/url"')
                    print()

                case "D":
                    self.delete()
                    break
                case "h":
                    print(self.repl.__doc__)
                case "x":
                    print("Exited")
                    dump_staged_dirs()
                    sys.exit(1)

                case "z":
                    shutil.move(
                        self.album_dir,
                        TARGET_DIR + "_issues/" + os.path.basename(self.album_dir),
                    )
                    print("skip")
                    break

                case _:
                    print(f"Unrecognised: {action} (h: help)")

                # case "b":
                #     browse_versions(
                #         # r[0]["resource_url"],
                #         artist=art,
                #         album=album,
                #     )

            self.display_tracks()
            # }}}


# track/tag checks {{{


def durations_match(
    file_durations: list[int],
    discogs_tags: pd.DataFrame,
    max_dur_diff: int = 15,
) -> bool:
    """Only if not tty.

    [TODO:description]

    Args:
        file_durations: [TODO:description]
        discogs_tracklist: [TODO:description]
        max_diff: [TODO:description]

    Returns:
        bool: [TODO:description]
    """

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
        print(discogs_tags[discogs_tags.dur_diff > max_dur_diff])
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


# }}}


# tag operations {{{
def edit_tag(
    tags_list: list[EasyID3],
    # tags: EasyID3,
    field: str = "",
    new_val: str = "",
):
    """Manually edit tag value. If <field> and/or <new_val> are not passed,
    user input will be required."""
    # only called by "e" and cli

    def completer(
        text: str,
        state: int,
        # fuzzy: bool = True,
    ) -> str | None:
        """Simple completer for CLI tab completion"""
        options = [a for a in os.listdir(TARGET_DIR) if a.startswith(text)]
        if len(options) > state:
            return options[state]
        return None

    if not field:
        field = input(f"Tag to edit ({'/'.join(FIELD_ALIASES.values())}): ")

        if field in FIELD_ALIASES:
            field = FIELD_ALIASES[field]
            curr_val = tags_list[0][field][0]
        else:
            curr_val = ""

        if field == "artist":
            readline.set_completer_delims("\t\n;")
            readline.parse_and_bind("tab: complete")
            readline.set_completer(completer)

        new_val = input_with_prefill(
            f"Edit {field}: ",
            curr_val,
        )

    for tags in tags_list:
        set_tag(tags, field, new_val)


# }}}


def get_clipboard() -> str:
    """Retrieve clipboard contents, attempts to detect if a Discogs id was
    copied (from the 'Copy Release Code' button). Note: if you use tridactyl,
    yank doesn't actually send the selection to xclip.
    """

    with sp.Popen(
        "xclip -o clipboard".split(),
        stdout=sp.PIPE,
    ) as clip:
        if not clip.stdout:
            eprint("could not access clipboard")
            return ""

        clip_str = clip.stdout.read().decode("utf-8")

        if clip_str.startswith("[r"):
            return clip_str[2:-1]

        if clip_str.startswith("[m"):
            return release.get_primary_url(d_get(f"/masters/{clip_str[2:-1]}")).split(
                "/"
            )[-1]

        return ""


def split_composer_and_performers(
    art: str,
    album: str,
) -> tuple[str, str]:
    """Should only be used if release has 1 composer credited"""

    # print(artist)
    # print(album)

    delims = [f" {c} " for c in "●-"]

    if any(d in art for d in delims):
        for delim in delims:
            if delim in art and TTY:
                composer = select_from_list(art.split(delim), "Composer")
                break

    elif TTY:
        if album.endswith("]"):
            # album.partition is cleaner, but fails if title itself has [
            perfs = album.rsplit("[", maxsplit=1)[-1].rstrip("]").split(", ")
        else:
            perfs = []

        perfs = set(perfs) | set(art.split(", "))
        perfs = {tcase_with_exc(x) for x in perfs}
        composer: str = select_from_list(list(perfs), "Composer")
        perfs.remove(composer)

        if perfs:
            correct_album = album.partition("[")[0] + f'[{", ".join((perfs))}]'
        else:
            correct_album = album

        return composer, correct_album

    raise NotImplementedError


def dump_staged_dirs() -> None:
    """Write list of dirs that were cleared (whether automatically or manually)
    to file"""
    with open(STAGED_FILE, "w+", encoding="utf-8") as fobj:
        fobj.writelines({l + "\n" for l in STAGED_DIRS})


def dump_library_dirs() -> None:
    """Can be extremely fast when warm -- <1s for 56k (depth 2), otherwise < 6
    min.

    Since this is so fast, it might be worth considering using
    shallow_recurse() for dump_library_genres().
    """

    assert CONFIG["play"]["database"]
    db_path = PATH + "/" + CONFIG["play"]["database"]
    print("dumping to", db_path)

    dirs = shallow_recurse(TARGET_DIR)
    dirs = sorted(d.removeprefix(f"{TARGET_DIR}/") + "\n" for d in dirs)
    print("Found", len(dirs), "dirs")

    with open(db_path, "w+", encoding="utf-8") as f:
        f.writelines(dirs)


def order_files_by_duration(
    rel: dict,
    files: list[str],
) -> list[str]:
    """Attempt to sort files to match durations from Discogs"""
    ref_df = release.get_release_tracklist(rel)

    assert all(ref_df.dur)

    # ref_df["fname"] = [""] * len(ref_df)
    ref_df["fname"] = ""

    print(ref_df)

    file_durs = get_file_durations(files)

    def dur_diff(dur):
        return abs(dur - file_dur)

    # release track order is constant
    for file_dur, file in zip(file_durs, files):
        # identify df row with lowest dur diff
        diffs = ref_df.dur.apply(dur_diff)
        idx = diffs[diffs == min(diffs)].index.values[0]
        # print(
        #     idx,
        #     ref_df.iloc[idx],
        # )
        ref_df.at[idx, "fname"] = file

    return ref_df.fname.to_list()


def main(dirs_to_tag: list[str]):
    """Initialise a Tagger on all directories (does not recurse for you). Can
    automate tagging without user input.
    """
    auto = not TTY

    if STAGED_DIRS:
        print(f"({len(STAGED_DIRS)} dirs already staged)")

    staged = len(STAGED_DIRS)
    total = len(dirs_to_tag)

    # TODO: check discogs auth

    for i, _dir in enumerate(sorted(dirs_to_tag)):
        if total > 1:
            if auto:
                print(f"({staged})", end=" ")
            print(f"{i}/{total}")

        print(_dir)

        album = Tagger(_dir)

        if TTY and album.files:  # and album.tags:
            album.repl()  # when user leaves menu, self.staged is set to True

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


TTY = sys.__stdin__.isatty()

if __name__ == "__main__":
    assert SOURCE_DIR
    assert TARGET_DIR

    if len(sys.argv) == 2 and sys.argv[-1] in ["-a", "--auto"]:
        sys.argv.pop()
        TTY = False

    if len(sys.argv) == 1:
        dirs = glob_full(SOURCE_DIR)
        dirs = [d for d in dirs if d not in STAGED_DIRS]
        if not dirs:
            print(f"All {len(STAGED_DIRS)} dirs already staged")
            sys.exit(0)

    elif len(sys.argv) == 2 and os.path.isdir(sys.argv[1]):
        dirs = [os.path.realpath(sys.argv[1])]

    else:
        if os.path.isfile(sys.argv[1]):
            print(EasyID3(sys.argv[1]))

        elif sys.argv[1] == "--dump":
            dump_library_dirs()

        elif len(sys.argv) == 2 and os.path.isdir(art := f"{TARGET_DIR}/{sys.argv[1]}"):
            mover.multi_move(glob_full(art, dirs_only=True))

        # last resort: multiple dirs already in library
        elif len(sys.argv) > 2:
            mover.multi_move(sys.argv[1:])

        sys.exit(0)

    try:
        main(dirs)
        dump_staged_dirs()
    except Exception as exc:
        dump_staged_dirs()
        raise exc
