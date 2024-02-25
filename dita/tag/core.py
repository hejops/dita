"""Core utilities for working with MP3 tags"""
# from glob import escape
# from pprint import pprint
# import configparser
# import shutil
# import unicodedata
import itertools
import logging
import os
import re
import readline
import shlex
import string
import sys
from datetime import datetime
from glob import glob
from math import isnan
from pprint import pformat
from typing import Any
from typing import Iterator
from typing import Sequence
from urllib.parse import quote

import filetype
import pandas as pd
import psutil
from mutagen import File
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from termcolor import colored
from titlecase import titlecase

from dita.config import load_titlecase_exceptions

# from tabulate import tabulate

# from mutagen.easyid3 import ID3

TITLECASE_EXCEPTIONS = load_titlecase_exceptions()


def align_lists(left: list, right: list):
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
    # return pd.DataFrame({"left": left, "right": right})


# file operations {{{


def file_in_use(fpath: str) -> bool:
    """Mimic tail --pid=<pid> -f /dev/null"""
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

    # children = [
    #     full_d for d in os.scandir(parent) if os.path.isdir(full_d := f"{parent}/{d}")
    # ]
    # print(children[0])

    # children = [
    #     full_d for d in os.listdir(parent) if os.path.isdir(full_d := f"{parent}/{d}")
    # ]

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
        root_dir: [TODO:description]
        recursive: [TODO:description]
        dirs_only: [TODO:description]
        first_match: [TODO:description]
        mindepth: [TODO:description]

    Returns:
        [TODO:description]
    """
    # basically just listdir
    if not recursive:
        return [os.path.join(root_dir, x) for x in os.listdir(root_dir)]

    # # don't think this does anything
    # if mindepth == 0:
    #     return [root_dir]

    items = glob(
        "**",
        root_dir=root_dir,
        recursive=True,
    )

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

    # x = "printanières"
    # print([c + unicodedata.category(c) for c in x])
    # raise Exception

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
    """Check that <file>:
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

    if ext == "ape":
        return True

    if os.path.isfile(file) and ext in extensions:
        # if the byte check fails, expect to see it caught by any media player.
        # it is not our responsibility to fix this
        if (
            filetype.guess_extension(file)
            and filetype.guess_extension(file) in extensions
        ):
            return True
        print("bad file header")
        return False

    if os.path.islink(file) and not os.path.isfile(file):
        eprint("Removing broken symlink:", file)
        os.unlink(file)
        return False

    return False


def get_audio_files(src_dir: str) -> list[str]:
    """Wrapper for glob_full + is_audio_file. Returns a sorted list of audio
    files in a directory (non-recursive by default, i.e. top-level). Hidden
    files (starting with '.') are omitted.



    """
    files = glob_full(
        src_dir,
        recursive=True,
        dirs_only=False,
    )
    return sorted(f for f in files if is_audio_file(f, ["mp3"]))


# }}}

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
    """set_tag.
    For SRP and explicitness, this function only operates on a single
    track/file. Tags are saved.

    Args:
        tags (EasyID3): tags for a single file
        field (str): field to be written, e.g. "artist"
        new_val (str): new value to be written

    Returns:
        EasyID3:
    """

    field: str = FIELD_ALIASES.get(field, field)

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
    """
    Parse ID3 tags of an mp3 file using EasyID3. The file must have valid tag
    headers; no error handling is performed here. EasyID3 is used for its
    convenient dict-like structure.
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

    # if ID3(file, v2_version=3).__dict__["_version"] == (2, 4, 0):
    #     return None

    try:
        # tags = ID3(file, v2_version=3)
        # if tags.__dict__["_version"] == (2, 4, 0):
        #     print("dfsaodas")
        #     # neither of these version-setting methods work -- according to `file`
        #     # tags.save(v2_version=3)
        #     tags.update_to_v23()
        #     assert tags.__dict__["_version"] != (2, 4, 0)

        tags = EasyID3(file)  # 5.5 ms
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
        if add_empty_fields:
            for field in ["genre", "artist", "album"]:
                tags[field] = ""
        if tags == {}:
            # print(tags)
            tags.add_tags()
        save_tags(tags)


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
    return x
    # return int(x)


def get_files_tags(
    audio_files: list[str],
    sort_tracknum: bool = True,
) -> list[EasyID3]:
    """Gathers all files in a directory non-recursively (for recursive calls,
    call get_audio_files(dir) directly), checks if files are sorted properly,
    and generates EasyID3 tags for each file.

    Side-effect: 'tracknumber' field is also set.

    Args:
        dir (str): dir

    Returns:
        list of EasyID3 tags
    """

    eprint(f"Processing {len(audio_files)} files...")

    # files passed are assumed to be sorted by fname; this is not necessarily
    # correct if >99 files

    # tags_list = [file_to_tags(f) for f in audio_files]

    tags_list = []
    for f in audio_files:
        # print(f)
        try:
            tags_list.append(file_to_tags(f))
        except ID3NoHeaderError:
            # pass
            # shutil.move(f, f + ".xxx")
            add_headers(audio_files)
            tags_list = [file_to_tags(f) for f in audio_files]

    # Determine how to sort the files
    # priority: discnumber -> file prefix -> tracknumber

    # by default, if tags have tracknumber field, they are assumed to be sorted
    # properly; this overrides fname sorting

    # if max(t.get("tracknumber") for t in tags_list) > 1:
    #     print(
    #         audio_files,
    #         tags_list,
    #     )
    #     raise Exception

    if (
        len({t.get("album")[0] for t in tags_list if t.get("album")})
        > 1
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
                if all(t.get("discnumber") for t in tags_list):
                    eprint("sort discnum")
                    return lambda x: 1000 * int(front_int(x["discnumber"][0])) + int(
                        front_int(x["tracknumber"][0])
                    )
                return lambda x: int(front_int(x["tracknumber"][0]))

            tags_list = sorted(tags_list, key=sortkey())

        except KeyError:
            pass

    # print(tags_list)
    # raise ValueError

    for i, tags in enumerate(tags_list):
        # print(i, tags)
        set_tag(tags, "tracknumber", fill_tracknum(i + 1))
        # print(i, tags)
        # raise ValueError

    return tags_list


def year_is_valid(year: int) -> bool:
    """
    The oldest album I have is from 1929:
    https://www.discogs.com/release/14916738
    """
    return 1925 <= year <= datetime.now().year


# }}}

# string operations {{{


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


def shuote(*words: str):
    """Wrapper for shlex.quote on a list (hacky). only used about twice"""
    words = [x if not x.startswith("http") else x for x in words]
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
        items: [TODO:description]
        msg: [TODO:description]
        sep: [TODO:description]
        allow_null: [TODO:description]

    Returns:
        [TODO:description]
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
    if not rating or isnan(rating) or not 1 <= rating <= 5:
        return str(rating)

    if color:
        c_rating = colored(str(rating), color)

    else:
        colors = {
            1: "red",
            2: "green",
            3: "blue",
            4: "cyan",
            5: "magenta",
        }
        c_rating = colored(str(rating), colors[int(rating)])

    # else:
    #     raise NotImplementedError

    if _print:
        eprint(c_rating)

    return c_rating


def tabulate_dict(
    dict_or_df: Iterator[dict] | pd.DataFrame,
    columns: list[str] | None = None,
    max_rows: int = 50,
    truncate: bool = False,
    showindex: bool = False,
) -> str:
    """Addresses some shortcomings of pandas' default df reprs (namely,
    ensuring strings are left-aligned).

    Args:
        dict_or_df: [TODO:description]
        columns: [TODO:description]
        max_rows: [TODO:description]
        truncate: [TODO:description]
        showindex: [TODO:description]

    Returns:
        [TODO:description]
    """
    # left-aligning columns is not worth the effort lol
    # https://pandas.pydata.org/pandas-docs/stable/user_guide/options.html#available-options
    # pd.set_option("display.colheader_justify", "left")
    # pd.set_option("text-align", "left")
    # print(df)

    # truncated = False

    if isinstance(dict_or_df, pd.DataFrame):
        if columns:
            df = dict_or_df.loc[:, columns]
        else:
            df = dict_or_df
    else:
        if columns:
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
