"""Core utilities for working with MP3 tags."""

import itertools
import logging
import os
import re
import readline
import shlex
import string
import sys
from datetime import datetime
from math import isnan
from pprint import pformat
from typing import Any
from collections.abc import Iterator
from collections.abc import Sequence
from urllib.parse import quote

import pandas as pd
from mutagen._file import File
from mutagen.easyid3 import EasyID3
from mutagen.id3._util import ID3NoHeaderError
from termcolor import colored
from titlecase import titlecase

from dita.config import TITLECASE_EXCEPTIONS

TITLECASE_EXCEPTIONS = dict(TITLECASE_EXCEPTIONS)


def align_lists(left: list, right: list):  # {{{
    """Primitive, unoptimised sequence alignment algorithm. Requires exact
    match (so casefold etc should be performed beforehand). If an item is found
    in sequence A but not in sequence B, this is reflected as None in sequence
    B. For the sake of a primitive algorithm, pd.Series is to be avoided.

    A better algorithm would be Needleman-Wunsch:
    https://johnlekberg.com/blog/2020-10-25-seq-align.html
    """

    # cartesian product does most of the heavy lifting
    matches = [
        prod[0]
        for prod in itertools.product(left, right)
        #
        # alternative similarity metrics (e.g. Levenshtein) can be used here;
        # the result would have to be tuple though
        if len(set(prod)) == 1
    ]

    # pad both lists until their match indices are equal. the lists can have
    # unequal lengths, but their match indices will always have same length,
    # e.g.
    #
    # [0, 2, 4] [0, 1, 2]
    #
    # note: because this is based on .index, only the leftmost instance of each
    # pair is aligned. lists with repeated elements may end up with unaligned
    # pairs after the initial pair is aligned.

    l_idxs = [left.index(m) for m in matches]
    r_idxs = [right.index(m) for m in matches]
    while l_idxs != r_idxs:
        # print(l_idxs, r_idxs)
        for i, (l_idx, r_idx) in enumerate(zip(l_idxs, r_idxs)):
            if l_idx > r_idx:
                # TODO: insert none n times (not just once) -- https://stackoverflow.com/a/39541404
                # right[r_idxs[i]:r_idxs[i]] = [None] * diff
                right.insert(r_idxs[i], None)
                #              v
                # [0, 2, 4] [0, 1, 2] -- pad at i=1
                # [0, 2, 4] [0, 2, 3] -- slice both [2:]
                #       [4]       [3]
                #
                # instead of recalc'ing all the match indices again, take
                # advantage of the fact that all idxs up to the newly aligned
                # idx no longer need to be compared
                r_idxs = [idx + 1 for idx in r_idxs[i + 1 :]]
                l_idxs = l_idxs[i + 1 :]
                break
            if l_idx < r_idx:
                left.insert(l_idxs[i], None)
                l_idxs = [idx + 1 for idx in l_idxs[i + 1 :]]
                r_idxs = r_idxs[i + 1 :]
                break
            # to_pad.insert(idxs[i], None)
            # idxs = idxs[:i] + [idx + 1 for idx in idxs[i:]]

    # print(l_idxs, r_idxs)

    # all the matching items are aligned now, final padding to equalise length
    while len(left) != len(right):
        if len(left) < len(right):
            left += [None]
        elif len(left) > len(right):
            right += [None]

    # print(left)
    # print(right)

    # optionally, for each pair of intermediate items, we could force exactly
    # one to be None, e.g. the result...
    #
    # ['aaa', 'bbb', 'ccc', 'ddd', 'eee', None]
    # ['aaa', 'xxx', 'ccc', None, 'eee', 'fff']
    #
    # would become:
    #
    # ['aaa', 'bbb', None, 'ccc', 'ddd', 'eee', None]
    # ['aaa', None, 'xxx', 'ccc', None, 'eee', 'fff']
    #
    # if this is not the case, pad left list rightwards, and right list
    # leftwards.
    #
    # for my use case, i don't find this necessary

    # print(left)
    # print(right)

    assert len(left) == len(right)

    return left, right
    # return pd.DataFrame({"left": left, "right": right})}}}


# tag operations {{{


FIELD_ALIASES = {
    "l": "album",
    "a": "artist",
    "g": "genre",
    "d": "date",
}


def set_tag(
    tags: EasyID3,
    field: str,
    new_val: str,
) -> None:
    """
    For SRP and explicitness, this function only operates on a single
    track/file. Tags are saved.

    Args:
        tags (EasyID3): tags for a single file
        field (str): field to be written, e.g. "artist"
        new_val (str): new value to be written

    """
    field: str = FIELD_ALIASES.get(field) or field

    # squeeze whitespace
    new_val = " ".join(new_val.split())

    # print(new_val)
    assert tags
    if field == "date":
        tags[field] = [new_val]
    else:
        tags[field] = new_val.strip()

    save_tags(tags)


def save_tags(tags) -> None:
    """
    Required to generalise across MP3 (IDv2.3) and opus (which was once
    supported). Note: ID3v2.4 tags cannot be read by id3.
    """
    if isinstance(tags, EasyID3):
        tags.save(v2_version=3)
    else:
        tags.save()


def file_to_tags(file: str) -> EasyID3 | None:
    """Parse ID3 tags of an mp3 file using `EasyID3`.

    The file must have valid tag headers; no error handling is performed here.
    `EasyID3` is used for its convenient dict-like structure.
    """
    # alternative: https://github.com/devsnd/tinytag
    # https://mutagen.readthedocs.io/en/latest/api/oggopus.html
    # https://mutagen.readthedocs.io/en/latest/user/gettingstarted.html
    # https://mutagen.readthedocs.io/en/latest/user/id3.html
    # https://python.hotexamples.com/examples/mutagen/File/-/python-file-class-examples.html
    # https://stackoverflow.com/q/42231932

    # if file.endswith(".opus"):
    #     raise NotImplementedError
    #     # tags: OggOpus = OggOpus(file)
    #     # save_tags(tags)

    try:
        tags = EasyID3(file)
    except ID3NoHeaderError:
        return None

    # have tags, but no fields
    # i forgot if this ever gets triggered
    if "title" not in tags:
        tags["title"] = os.path.basename(file)
        save_tags(tags)

    return tags


def add_headers(
    files: list[str],
    add_empty_fields: bool = False,
):
    for file in files:
        tags = File(file, easy=True)
        assert tags
        if add_empty_fields:
            for field in ["genre", "artist", "album"]:
                tags[field] = ""
        if tags == {}:
            # print(tags)
            tags.add_tags()
        save_tags(tags)


def get_files_tags(
    audio_files: list[str],
    sort_tracknum: bool = True,
) -> list[EasyID3]:
    """Gather all files in a directory non-recursively.

    Also check if files are sorted properly, and generate EasyID3 tags for each
    file.

    For recursive calls, call `get_audio_files(dir)` directly

    Side-effect: 'tracknumber' field is also set.
    """
    eprint(f"Processing {len(audio_files)} files...")

    # files passed are assumed to be sorted by fname; this is not necessarily
    # correct if >99 files

    try:
        tags = [file_to_tags(f) for f in audio_files]
    except ID3NoHeaderError:
        add_headers(audio_files)
        tags = [file_to_tags(f) for f in audio_files]

    assert all(tags)
    tags: list[EasyID3]

    # Determine how to sort the files
    # priority: discnumber -> file prefix -> tracknumber

    # by default, if tags have tracknumber field, they are assumed to be sorted
    # properly; this overrides fname sorting

    if (
        len({t.get("album")[0] for t in tags if t.get("album")}) > 1
        #
        # or ...
    ):
        # multi-disc album with multiple album titles ['...CD1', ...] and
        # -without- discnumber will be listed as [1-01, 2-01, 3-01, 1-02, ...]
        # and must be sorted by filename
        ...
    elif sort_tracknum:
        try:
            # try disc num first
            def sortkey():
                # could probably do some int hack like 1*1000 + 2
                if all(t.get("discnumber") for t in tags):
                    eprint("sort discnum")
                    return lambda x: 1000 * int(front_int(x["discnumber"][0])) + int(
                        front_int(x["tracknumber"][0])
                    )
                return lambda x: int(front_int(x["tracknumber"][0]))

            tags = sorted(tags, key=sortkey())

        except KeyError:
            pass

    # print(tags_list)
    # raise ValueError

    for i, tag in enumerate(tags):
        set_tag(tag, "tracknumber", fill_tracknum(i + 1))

    return tags


def year_is_valid(year: int) -> bool:
    """
    The oldest album I have is from 1929:
    https://www.discogs.com/release/14916738
    """
    return 1925 <= year <= datetime.now().year


# }}}

# string operations {{{


def front_int(s: str) -> int:
    """Get frontmost int of a string"""
    x = ""
    for char in s:
        if char.isnumeric():
            x += char
        else:
            break
    if x == "":
        return 0
    return int(x)


def fill_tracknum(
    track: int,
    maxlen: int = 2,
) -> str:
    """Left-pad an int with zeros. Generally used with enumerate()"""
    num = track
    num = str(num)
    if "/" in num:
        num = num.partition("/")[0]
    return num.zfill(maxlen)


def tcase_with_exc(_str: str) -> str:
    """Apply titlecase, but allow exceptions. I would have liked certain
    non-English words (e.g. van, de, aus...) to remain uncapitalised, but there
    are simply too many edge cases to consider."""
    if _str.lower() in TITLECASE_EXCEPTIONS:
        return TITLECASE_EXCEPTIONS[_str.lower()]
    return titlecase(_str)


def extract_year(text: str) -> list[int]:
    """Use regex to extract years from any body of text"""
    # (?:xxx) = non-capturing
    return list(frozenset([int(x) for x in re.findall(r"(?:19|20)\d{2}", text)]))


def shuote(*args: str):
    """Hacky wrapper for `shlex.quote`, only used once (!)"""
    words = [x if not x.startswith("http") else x for x in args]
    return shlex.quote(" ".join(words)).replace("#", "")


def open_url(
    base_url: str,
    *words,
    suffix: str = "",  # default args must come after *args
    simulate: bool = False,  # True only for testing
) -> str:
    """Try to construct a URL in 3 parts:

    base/middle/suffix

    Everything in middle will be url-quoted!
    """
    # stripping '/' is done a lot, out of (a possibly ungrounded) paranoia
    base_url = base_url.strip("/")
    # base_url = shlex.quote(quote(base_url)).strip("/")

    if words:
        # not sure which order
        # words = shlex.quote(quote(" ".join(*words))).strip("/")
        words = quote(" ".join(*words)).strip("/")
        if base_url.endswith("="):
            base_url += words
        else:
            base_url = "/".join([base_url, words])
        base_url = shlex.quote(base_url)

    if suffix:
        # suffix = shlex.quote(quote(suffix)).strip("/")
        base_url = "/".join([base_url, suffix])

    # print(base_url)
    # os.system(f"echo {base_url}")
    # raise Exception

    if not simulate:
        os.system(f"xdg-open {base_url}")

    return base_url


def is_ascii(artist: str) -> bool:
    """Like str.isascii(), but less strict (returns True if at least one ascii
    char is present)"""
    # https://stackoverflow.com/a/266162
    artist = artist.translate(str.maketrans("", "", string.punctuation))
    artist = artist.replace(" ", "")  # space is not considered punctuation

    # print(artist)
    # for c in artist:
    #     print(c, c.isascii(), c.isalnum(), c.isspace())
    # # م     False True  False
    # # ø     False True  False
    # # 원    False True  False
    # # space True  False True

    if not any(c.isascii() for c in artist):
        return False

    # print("not ascii")
    return True


# }}}

# user input {{{


def select_from_list(
    # https://docs.python.org/3/library/collections.abc.html#collections-abstract-base-classes
    items: Sequence | pd.DataFrame,
    msg: str,
    sep: str = "\n",
    allow_null: bool = True,
) -> Any:
    """Select a single item from a sequence by index. dfs have special
    behaviour that must be accounted for.

    Args:

    Returns:
    """

    if len(items) == 1:
        print("Selecting the only option:", items)
        return items if isinstance(items, pd.DataFrame) else items[0]

    if isinstance(items, pd.DataFrame):
        # empty df should not be handled here, but by caller
        assert not items.empty
        print(items)

    else:
        # print(type(items))
        if len(items) == 0:
            # if items.empty:
            eprint(msg + ":")
            return input(msg)

        eprint("=====")
        eprint(sep.join((f"{i}: {t}" for i, t in enumerate(items))))
        eprint("=====")

    # for i, t in enumerate(items):
    #     print(f"{i}: {t}")

    # readchar should never be used
    # write prompt to stderr to ensure it is always seen (and never used)
    eprint(msg.strip(": ") + ":")
    sel = input()

    if not allow_null and sel == "":
        return ""

    # allow arbitrary string to be returned
    if sel and not sel.isnumeric():
        return sel

    if sel == "":
        sel = 0
    sel = int(sel)

    return items.iloc[sel] if isinstance(items, pd.DataFrame) else items[sel]


def input_with_prefill(prompt: str, text: str) -> str:
    """Mimic GNU read -e -i <prefill>"""

    # https://stackoverflow.com/a/8505387
    def hook():
        readline.insert_text(text)
        readline.redisplay()

    readline.set_pre_input_hook(hook)
    while result := input(prompt):
        break
    readline.set_pre_input_hook()  # clear on function end
    return result


def get_clipboard() -> str:
    """Retrieve clipboard contents, attempts to detect if a Discogs id was
    copied (from the 'Copy Release Code' button). Note: if you use tridactyl,
    yank doesn't actually send the selection to xclip.
    """
    import subprocess as sp

    # this is very hacky
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
            from dita.discogs.release import get_primary_url
            from dita.discogs.core import d_get

            return get_primary_url(d_get(f"/masters/{clip_str[2:-1]}")).split("/")[-1]

        return ""


# }}}

# printing {{{


LOGGER = logging.getLogger("foo")
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


# https://docs.python.org/3/howto/logging.html
def lprint(*args) -> None:
    """Pretty print everything except strings"""
    # LOGGER.info(args)
    # LOGGER.info(" ".join(args))
    # https://stackoverflow.com/a/11093247

    for arg in args:
        if isinstance(arg, str):
            LOGGER.info(arg)
        else:
            LOGGER.info(pformat(arg))


def eprint(*args):
    """Print to stderr; typically for msgs that should be ignored by shell"""
    print(*args, file=sys.stderr)


def cprint(
    rating: float,
    color: str = "",
    _print: bool = True,
) -> str:
    """Print with color"""
    if not rating or isnan(rating):  # or not 1 <= rating <= 5:
        return str(rating)

    if color:
        c_rating = colored(str(rating), color)

    else:
        # note: these colors may be overridden by your tty colorscheme
        colors = {
            1: "red",
            2: "green",
            3: "magenta",  # blue
            4: "cyan",
            5: "yellow",
        }
        if int(rating) not in colors:
            return str(rating)
        c_rating = colored(str(rating), colors[int(rating)])

    if _print:
        eprint(c_rating)

    return c_rating


def tabulate_dict(
    dict_or_df: Iterator[dict] | pd.DataFrame,
    columns: list[str] | None = None,
    max_rows: int = 50,
    truncate: bool = False,
    # showindex: bool = False,
) -> str:
    """Addresses some shortcomings of pandas' default df reprs (namely,
    ensuring strings are left-aligned).

    """
    # i used to use `tabulate` because right-aligned strings from pandas'
    # default printer are annoying to read, but at some point i removed it
    # without explanation

    # left-aligning columns is not worth the effort lol
    # https://pandas.pydata.org/pandas-docs/stable/user_guide/options.html#available-options
    # print(df)

    # truncated = False

    if isinstance(dict_or_df, pd.DataFrame):
        if columns:
            df = dict_or_df.loc[:, columns]
        else:
            df = dict_or_df
    elif columns:
        df = pd.DataFrame(dict_or_df, columns=columns)
    else:
        df = pd.DataFrame(dict_or_df)

    if df.empty:
        return ""

    # df_len = len(df)
    if truncate and len(df) > max_rows:
        df = df[:max_rows]
        # truncated = True

    # df.r = df.r.apply(lambda x: cprint(x, _print=False))

    return df.to_string()


# }}}
