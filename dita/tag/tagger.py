import os
import readline  # pylint: disable=unused-import
import shlex
import shutil
import subprocess as sp
import time
import unicodedata
from shutil import rmtree
from typing import Optional

import mutagen.wave
import pandas as pd
import readchar
import requests
from mutagen._file import File
from mutagen.mp3 import MP3
from mutagen.mp3 import EasyMP3
from mutagen.mp3 import HeaderNotFoundError
from natsort import natsort_keygen
from numpy import nan
from titlecase import titlecase

from dita.config import SOURCE_DIR
from dita.config import TARGET_DIR
from dita.discogs.artist import get_transliterations
from dita.discogs.core import cli_search
from dita.discogs.core import d_get
from dita.discogs.core import search_release
from dita.discogs.release import apply_transliterations
from dita.discogs.release import display_release_results
from dita.discogs.release import get_discogs_tags
from dita.tag.core import FIELD_ALIASES
from dita.tag.core import align_lists
from dita.tag.core import eprint
from dita.tag.core import file_to_tags
from dita.tag.core import get_clipboard
from dita.tag.core import input_with_prefill
from dita.tag.core import is_ascii
from dita.tag.core import open_url
from dita.tag.core import save_tags
from dita.tag.core import select_from_list
from dita.tag.core import set_tag
from dita.tag.core import tcase_with_exc
from dita.tag.io import durations_match
from dita.tag.io import get_file_durations
from dita.tag.io import glob_full
from dita.tag.io import is_audio_file

REQUIRED_FIELDS = {
    "album",
    "artist",
    "date",
    "title",
    "tracknumber",
}


def edit_tag(
    tags: pd.Series,  # [EasyID3],
    field: str = "",
    new_val: str = "",
):
    """Manually edit tag value. If `field` and/or `new_val` are not passed,
    user input will be required.
    """
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
            curr_val = tags.iloc[0][field][0]
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

    for tag in tags:
        set_tag(tag, field, new_val)


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
            if delim in art:  # and TTY:
                composer = select_from_list(art.split(delim), "Composer")
                break

    # elif TTY:
    else:
        # TODO: refactor this out
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
            correct_album = album.partition("[")[0] + f"[{', '.join(perfs)}]"
        else:
            correct_album = album

        return composer, correct_album

    raise NotImplementedError


class Tagger:
    """Tagger object initialised by directory string. All data (including
    Mutagen tags) is contained within the attribute 'df'.

    Always attempts automatic Discogs tagging. Contains a barebones REPL that
    moves only forwards through directories.
    """

    quit = False
    ready = False
    staged = False

    def __init__(  # {{{
        self,
        album_dir: str,
        tty: bool = False,
    ):
        self.tty = tty
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

        self.df: pd.DataFrame = pd.DataFrame(
            [
                {"file": f, "tags": file_to_tags(f)}
                for f in all_files
                if os.path.isfile(f)
            ],
        )

        # better than lambda i guess
        # note: index.map() doesn't have args keyword!

        if self.df.empty or not self.df.file.apply(is_audio_file, args=["mp3"]).any():
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
            dups := self.df[(self.df.index.str.endswith(" (1).mp3"))].index.to_list(),
        ):
            for f in dups:
                os.remove(f)
            self.__init__(self.album_dir)

        if any(
            htoa := self.df[
                (self.df.index.str.contains("(HTOA)", regex=False))
            ].index.to_list(),
        ):
            for f in htoa:
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
            and not (alb := self.df.album.replace("", nan).dropna()).empty
            and (
                _out := sp.getoutput(
                    cmd := (
                        # only album needs to be checked, i -think-
                        f"mediainfo {os.path.dirname(SOURCE_DIR)}/downloading/* |"
                        " grep -F "
                        # is the number of spaces fixed?
                        "'Album                                    : '"
                        f"{shlex.quote(alb.iloc[0].strip())}"
                    ),
                )
            )
        ):
            print(cmd)
            # print(out)
            print("download in progress", album_dir)
            return

        self.files = self.df.index.to_list()

        # for all files, not all columns present
        if not REQUIRED_FIELDS.issubset(set(self.df.columns)):
            # TODO: error if tag exists
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

        self.ready = True

        if self.files and not self.df.tags.empty:
            self.staged = self.try_auto()
        else:
            os.system(f"ls -A {shlex.quote(self.album_dir)}")
            os.system(f"rm -rIv {shlex.quote(self.album_dir)}")
            return

    # }}}

    @staticmethod
    def add_headers(file: str) -> Optional[EasyMP3]:
        """Why did I duplicate this?"""
        # from mutagen.mp3 import HeaderNotFoundError

        try:
            tags = File(file, easy=True)
        except (
            mutagen.wave.error,
            HeaderNotFoundError,
        ):
            return None
        if tags is None:
            return None
        for field in REQUIRED_FIELDS:
            if field not in tags:
                try:
                    tags[field] = ""
                except TypeError:
                    # corner case: if you end up here, it means mp3 file is not
                    # actually mp3; probably better to check type(tags) outside
                    # the loop
                    return None
        save_tags(tags)
        return tags

    def regen_tag_columns(self) -> None:
        """Generate a new `df` from tags, and overwrite existing columns

        - `df.update` does NOT create new columns
        - `df.combine_first` only overwrites null columns
        """
        # https://github.com/pandas-dev/pandas/issues/39531#issuecomment-771346521
        right = self.df.tags.apply(dict).apply(pd.Series)
        self.df = self.df[self.df.columns.difference(right.columns)]
        self.df = self.df.merge(
            right,
            left_index=True,
            right_index=True,
        ).map(tags_to_columns)

    def try_auto(self) -> bool:  # {{{
        """For an automatic match, the following four conditions are required:

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
        )
        if results.empty:
            self.results = results
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
            if not self.tty:
                # 2s is long enough to avoid getting rate limited
                time.sleep(2)

            if result.type == "master":
                continue

            rel = d_get(result["resource_url"])

            # deleted release = { 'message' : 'Release not found' }
            # should probably not be triggered
            if "title" not in rel or "uri" not in rel:
                continue

            print()
            print(idx)
            print(rel["uri"].partition("-")[0])
            print(rel["title"])

            # while we only really need the tracklist for len and dur checks,
            # the tags are used for diagnostics, e.g. align_lists
            discogs_tags = get_discogs_tags(rel)

            if discogs_tags.empty:
                continue

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
                discogs_tags=discogs_tags,
                file_durations=file_durations,
            ):
                set_reason("dur")
                continue

            if not self.trans_ok(discogs_tags, rel):
                return False

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
        discogs_tags: pd.DataFrame,
        rel: dict,
    ) -> bool:
        """Check for 'artist' values that are not ASCII.

        Modifies df columns, but not file tags
        """
        # https://www.discogs.com/release/12168132

        if all(is_ascii(x) for x in discogs_tags.artist):
            return True

        transliterations = get_transliterations(rel)

        if not self.tty and not (
            # note: duplicated in apply_transliterations
            # 1 transliteration per artist
            max(len(x) for x in transliterations.values()) == 1
            # all artists have 1 translit
            and len(transliterations) == len(set(discogs_tags.artist))
        ):
            return False

        discogs_tags = apply_transliterations(transliterations, discogs_tags)

        if not all(is_ascii(x) for x in discogs_tags.artist):
            print("no trans")
            return False

        discogs_tags.artist = discogs_tags.artist.apply(tcase_with_exc)

        return True

    def summarize(self) -> None:  # {{{
        """Store metadata in `self.meta`.

        `self.meta` is a struct-like wrapper, for ease of constructing Discogs
        search queries. The function should be called after any change in tags
        (e.g. `apply_discogs_tags`). The metadata itself should not be used for
        tagging, especially if >1 artist.
        """
        self.regen_tag_columns()

        first = self.df.iloc[0]

        self.meta = {
            "artist": first["artist"],
            "album": first["album"],
            "date": first.get("date", 0),
        }

        # assert not self.meta["album"].endswith("\n")

        # print(self.meta["album"]+'xxx')

    # }}}

    def display_tracks(self) -> None:  # {{{
        """Summarize file tags.

        Example:
        -------
            ```
            Metallica
            Master of Puppets
            1986

            1 	Battery
            2 	Master of Puppets
            3 	The Thing That Should Not Be
            ...
            ```

        """

        def print_safe(_str: str) -> None:
            """Remove characters in non-standard (non-utf-8) encodings (e.g.
            ) before printing.

            Although this tends to lead to loss of
            characters, not performing this removal can lead to an unusable
            screen, a far more serious outcome that must be avoided.
            """
            # print(_str.encode("latin-1", "ignore").decode("utf-8", "ignore"))
            print("".join(c for c in str(_str) if unicodedata.category(c) != "Cc"))

        # print(self.df.artist)

        print()
        for val in self.meta.values():
            print_safe(val)
        print()

        # if durations:
        #     for (i, title), dur in zip(enumerate(self.df.title), durations):
        #         print(
        #             i + 1,
        #             time.strftime("%M:%S", time.gmtime(dur)),
        #             title,
        #             sep="\t",
        #         )
        # else:

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
        _id = _id.strip().removeprefix("[r").removesuffix("]")
        # assert not _id.startswith("[r")
        rel = d_get(_id)
        self.apply_discogs_tags(
            get_discogs_tags(release=rel),
            rel,
        )

    # }}}

    def search_and_replace(self):  # {{{
        """Modify existing field values to construct a new Discogs search
        query. Interface implementation is in `cli_search`.
        """
        result_ids = cli_search(
            artist=f"{self.meta['artist'].replace('/', ' ')}",
            album=f"{self.meta['album'].replace('/', ' ')}",
        )
        rel = display_release_results(
            result_ids,
            num_tracks=len(self.df),
        )

        if rel:
            self.apply_discogs_tags(get_discogs_tags(rel), rel)
        else:
            print("nothing selected")

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
        if len(self.df) != len(discogs_tags) and "dur_diff" in self.df.columns:
            print(
                (
                    "Tracklist lengths do not match: "
                    f"{len(self.df)} vs {len(discogs_tags)}"
                ),
                discogs_tags[["title", "dur_diff"]],
            )
            # allow writing artist/album fields, but not track titles
            for tags in self.df.tags:
                set_tag(tags, "artist", discogs_tags.artist.iloc[0])
                set_tag(tags, "album", discogs_tags.album.iloc[0])
            return

        self.trans_ok(discogs_tags, rel)

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

        if len(self.df) == len(discogs_tags):
            for tags, (_, df_row) in zip(
                self.df.tags,
                discogs_tags.iterrows(),
                strict=True,
            ):
                for field in fields:
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
        prevented automatic tagging will be ignored)
        """
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
        discogs_tags = get_discogs_tags(release=rel)
        self.apply_discogs_tags(discogs_tags, rel)

    # }}}

    def delete(self):
        """Delete current directory, as well as all parent directories after
        following any symlinks
        """
        for _dir in {os.path.dirname(os.path.realpath(f)) for f in self.files}:
            assert _dir != TARGET_DIR, (
                f"FATAL! Will not delete library root: {TARGET_DIR}"
            )
            rmtree(_dir)
            print("Removed:", _dir)

    def repl(self):  # {{{
        """May migrate to curses interface eventually.

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
                    # print({unicodedata.category(c) for c in self.meta["artist"]})
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
                        # TODO: .strip() always should be called
                        tag["title"] = titlecase(tag["title"][0]).strip()
                        tag["album"] = titlecase(tag["album"][0]).strip()

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
                    self.quit = True
                    print("Exited")
                    break

                case "z":
                    print("Skipped")
                    break

                case "z":
                    shutil.move(
                        self.album_dir,
                        TARGET_DIR + "_issues/" + os.path.basename(self.album_dir),
                    )
                    print("skip")
                    break

                case _:
                    print(f"Unrecognised: {action} (h: help)")

            self.display_tracks()
            # }}}
