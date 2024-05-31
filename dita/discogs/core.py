#!/usr/bin/env python3
"""
Module for fetching and manipulating data from the Discogs API. A personal
access token is required for some actions:

    https://www.discogs.com/settings/developers

API: https://www.discogs.com/developers

"""
# from pprint import pprint
import json
import re
import sys
import time
from typing import Any

import pandas as pd
import requests
from titlecase import titlecase

from dita.config import CONFIG
from dita.config import PATH
from dita.discogs import release
from dita.tag.core import eprint
from dita.tag.core import input_with_prefill
from dita.tag.core import tabulate_dict

DISCOGS_CSV = PATH + "/" + CONFIG["discogs"]["database"]

DISCOGS_TOKEN = CONFIG["discogs"]["token"]
USERNAME = CONFIG["discogs"]["username"]

# seems to avoid 502 -- https://stackoverflow.com/a/45964313
HEADERS = {"Cache-Control": "no-cache"}

if DISCOGS_TOKEN:
    HEADERS["Authorization"] = f"Discogs token={DISCOGS_TOKEN}"

API_PREFIX = "https://api.discogs.com"
PREFIX = "https://www.discogs.com"

ALBUM_SUFFIXES = set(CONFIG["tag"]["album_suffixes_to_remove"].split(","))

# # https://samgeo.codes/blog/python-types/
# async def print_with_timer(fmt: str, interval: float = 0.1) -> None:
#     start = time.time()
#     while True:
#         elapsed = time.time() - start
#         print(fmt.format(elapsed=elapsed), end="")
#         await asyncio.sleep(interval)


# core functions {{{


def get_id_from_url(url: str) -> int:
    """Obtain discogs id as string. Format is almost always:
    https://www.discogs.com/[type]/[id]-[text]
    """
    return int(
        url
        # some programs (e.g. tridactyl) quote their output
        .strip("'\"")
        .split("/")[-1]
        .split("-")[0]
    )


def d_get(
    query: str | int,
    all_pages: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Send a GET request to discogs.

    API_PREFIX should not be provided. If it is provided, it will be removed.

    Args:
        query: to be specified in the form "/<searchtype>s/{query}" -- note the
        plural. Must start with '/'. If searchtype is not specified (i.e. only
        a numeric string is passed), defaults to 'release'.

    Returns:
        dict: [TODO:description]
    """
    # TODO: if master id provided, warn, or redirect to discogs.release. however, this
    # false positive is difficult to catch because both dicts are structurally
    # valid.

    if isinstance(query, str):
        if "www.discogs.com" in query:
            query = web_url_to_api(query)

        if query.startswith(API_PREFIX):
            query = query.removeprefix(API_PREFIX)

        # if only a plain "int" is provided; assume releases (the most common use case)
        if query.isnumeric():
            query = f"{API_PREFIX}/releases/{query}"
        elif query.startswith("/"):
            # for explicitness, full queries should be prefixed with /
            query = f"{API_PREFIX}{query}"

    else:
        query = f"{API_PREFIX}/releases/{query}"

    if verbose:
        eprint(query)
        eprint(HEADERS)

    # requests.exceptions.ChunkedEncodingError
    # requests.exceptions.ReadTimeout
    # requests.exceptions.MissingSchema (int.0)
    response = requests.get(
        query,
        headers=HEADERS,
        timeout=30,  # 10 too short for large artists
    )

    # pprint(dict(response.headers))

    # print(response)
    # print(response.text)
    if verbose and response.status_code != 200:
        eprint(response.status_code)

    if response.status_code == 429:
        eprint("Hit rate limit, retrying in 60 seconds...")
        time.sleep(60)
        return d_get(query)

    json_d: dict = json.loads(response.text)

    # lprint(query, d)

    if all_pages:
        raise NotImplementedError

    # malformed search, usually due to empty query (e.g. all ascii stripped).
    # might be better to use 'items' rather than 'pages', but it doesn't matter
    # much.
    if "pagination" in json_d and json_d["pagination"]["pages"] == 200:
        return {}

    return json_d


def clean_artist(artist: str) -> str:
    """Cleanup formatting of a Discogs artist.

    Titlecase is always applied. Titlecase exceptions are not to be handled
    here.
    """

    for patt, sub in {
        # order is important
        " = .+": "",  # remove translation
        r" \(\d{1,3}\)$": "",  # remove (\d) suffix
        r" \(\d{1,2}\)(,| &)": ",",  # remove internal (\d)
        " [-•+]": ",",
        # r"\s+": " ",	# just strip
    }.items():
        artist = re.sub(patt, sub, artist)

    # not sure whether to titlecase before or after merging "The"s
    artist = titlecase(artist)
    # artist = artist.title()
    # artist = tcase_with_exc(artist)

    words = artist.split(", ")
    print(words)
    while "The" in words:
        i = words.index("The")
        if "the" not in words[i - 1]:  # may not be desirable
            words[i - 1] = " ".join([words[i], words[i - 1]])
        del words[i]

    artist = ", ".join(words)
    artist = artist.replace(", the", ", The")
    artist = artist.strip()
    artist = artist.rstrip("*")

    return artist


def web_url_to_api(url: str) -> str:
    """
       https://www.discogs.com/release/<id>-...
    -> https://api.discogs.com/releases/<id>
    """
    assert ".com" in url
    if "api." in url:
        return url
    return (
        url.split("-")[0]
        # hack: replace last / with s/
        .replace("release", "releases")
        .replace("master", "masters")
        .replace("www.", "api.")
    )


def replace_last(string: str, patt: str, replace: str) -> str:
    """Because str.replace(count=-1) does not do what you think it should do"""
    # https://bytenota.com/python-replace-last-occurrence-of-a-string/
    rev = string[::-1]
    replaced = rev.replace(patt[::-1], replace[::-1], 1)
    return replaced[::-1]


def api_url_to_web(url: str) -> str:
    """
       https://api.discogs.com/releases/<id>
    -> https://www.discogs.com/release/<id>
    """
    if "www." in url:
        return url
    return (
        replace_last(url, "s/", "/")  # hacky
        # url.replace("s/", "/")
        # .replace("releases", "release")
        # .replace("masters", "master")
        .replace("api.", "www.")
    )


def parse_string_num_range(
    str_range: str,
    top_delim: str = ",",
) -> list[int]:
    """[TODO:summary]

    Convert a numerical range represented as string (e.g. "3 to 7, 9 to 10") to
    an actual numerical list.

    Args:
        str_range: [TODO:description]
        top_delim: almost always ', ', may be ','

    Returns:
        [TODO:description]
    """

    # i don't think there is any sane way to parse this monster:
    # https://www.discogs.com/release/12464502
    # 1-1~1-17,2-1,2-4,2-8~2-9,2-12~2-13,2-21

    # https://www.discogs.com/release/1874970
    # 1-1 to 2-1

    def longest_common_prefix(strs: list[str]) -> str:
        """
        Identify longest common prefix; e.g. in a 2 disc situation, the dashes
        in '1-1 to 1-21' do not refer to ranges and should be removed.
        """
        prefix = ""
        strs = sorted(strs, key=len)
        shortest = strs[0]
        for i, char in enumerate(shortest):
            if all(_str[i] == char for _str in strs):
                prefix += char
            else:
                break
        if prefix in strs:
            # '1-1 to 1-12' should return '1-', not '1-1'
            prefix = prefix[:-1]
        return prefix

    # expanded from https://stackoverflow.com/a/6405228
    def bisect(
        part: str,
        delim: str,
    ) -> list[int]:
        # print(part, delim)
        start, end = part.split(delim)

        # prefix = longest_common_prefix([start, end])
        # if prefix and prefix.isnumeric():
        #     start = start.removeprefix(prefix)
        #     end = end.removeprefix(prefix)
        #     for x in inner_delims:
        #         prefix = prefix.strip(x)
        #     # assert start, f"{part} - {prefix}"
        #     # assert end, part
        #     foo = [int(prefix) + (x / 100) for x in range(int(start), int(end) + 1)]
        #     print(foo)
        #     raise ValueError

        if not start.isnumeric():
            prefix = longest_common_prefix([start, end])
            start = start.removeprefix(prefix)
            end = end.removeprefix(prefix)
            assert start, f"{part} - {prefix}"
            assert end, part

        return list(range(int(start), int(end) + 1))

    result = []

    parts = str_range.split(top_delim)
    inner_delims = [" to ", "~", "-"]
    print(str_range)
    for i, part in enumerate(parts):
        # print(part)
        part = part.strip()
        for delim in inner_delims:
            if delim in part:
                # print(delim, part)
                result += bisect(part, delim)
                if i + 1 == len(parts):  # last part
                    print(result)
                    return result
                break  # otherwise other delims will be tried

        # part = parse_string_num_range(part)

    # print(result)
    # raise ValueError

    return result


# }}}

# track functions {{{


def duration_as_int(dur: str) -> int:
    """Convert duration strings (as on Discogs) to integers"""
    # print(dur)

    # if not (dur := track.get("duration")):
    if not dur:
        return 0
    # https://stackoverflow.com/a/10663851
    if ":" not in dur:
        return 0
    if dur.count(":") == 2:
        # x = time.strptime(dur, "%H:%M:%S")
        hour, minute, sec = (int(x) for x in dur.split(":"))
        return 3600 * hour + 60 * minute + sec

    # x = time.strptime(dur, "%M:%S")
    minute, sec = (int(x) for x in dur.split(":"))
    return 60 * minute + sec


def extract_track_artists(
    release: dict,
    require_composer_role: bool = False,
) -> list[str]:
    """Extracts composer of a track

    The extraartists field of the track metadata is checked

    Args:
        track: [TODO:description]

    Returns:
        str: [TODO:description]
    """
    # subtrack could inherit composer from track; but this is not trivial, and
    # i don't think i've ever needed it

    artists = []
    for track in release["tracklist"]:
        if require_composer_role and "extraartists" in track:
            # should only be 1
            composer = ", ".join(
                art["name"]
                for art in track["extraartists"]
                if art["role"].startswith("Compos")
            )
            if composer:
                if "sub_tracks" in track:
                    # https://www.discogs.com/release/10016898
                    artists += [composer] * len(track["sub_tracks"])
                else:
                    artists.append(composer)

        # for non-classical splits
        elif "artists" in track:
            artists.append(track["artists"][0]["name"])

    return artists


# }}}

# genre functions {{{


def gather_genre_releases(genre: str) -> pd.DataFrame:
    """Get random releases in genre. Sorting of search results is not supported
    and must be done manually. Note: genres/styles are only associated with
    release/masters, not artists.

    Possibly useful if you're really bored and willing to check out anything
    from a genre. I haven't found a compelling use case for this."""

    i = 1
    max_pgs = 3
    rows = []

    # https://www.discogs.com/search/?sort=date_added%2Cdesc&style_exact={style}&year={year}&type=release
    # https://www.discogs.com/search/?sort=have%2Cdesc&style_exact={style}&year={year}&type=release
    # https://www.discogs.com/search/?sort=hot%2Cdesc&style_exact={style}&year={year}&type=release
    # https://www.discogs.com/search/?sort=want%2Cdesc&style_exact={style}&year={year}&type=release

    # sort_methods = ["date_added", "have", "hot", "want", "year"]
    # for s in sort_methods:
    #     search_url = (	# artist/album
    #         "https://api.discogs.com/database/search?release_title="
    #         f"{album}&artist={artist}&type=release"
    #     )
    #     url = (	# releases in style
    #         "https://api.discogs.com/database/search/?sort="
    #         f"{s}%2Cdesc&style_exact={style}&year={year}&type=release"
    #     )

    while page := d_get(
        f"/database/search?type=release&style={genre}&per_page=100&page={i}"
        # f"/database/search?type=release&style={style}&year={year}&per_page=100"
    ):
        for result in page["results"]:
            artist, album = result["title"].split(" - ", maxsplit=1)
            dic = {
                "artist": artist,
                "album": album,
                "year": result["year"] if "year" in result else "0",
            }
            rows.append(dic)
        eprint("page", i, "OK")
        i += 1
        if i == max_pgs:
            break
        time.sleep(1)

    # print("Stopped at page", i)
    df = pd.DataFrame(rows)  # .sort_values(["artist", "year"]).drop_duplicates()
    print(df)
    return df


# }}}

# list functions {{{


def get_list_releases(
    list_id: int,
    label: bool = False,
):
    """currently only used for piping to external programs"""

    if label:
        url = f"/labels/{list_id}/releases"
        dic = {clean_artist(i["artist"]): i["title"] for i in d_get(url)["releases"]}

    else:
        url = f"/lists/{list_id}"
        df = pd.read_csv(DISCOGS_CSV, index_col=0)
        items = [
            # item can be any (release/artist/label)
            i["display_title"]
            for i in d_get(url)["items"]
            if i and i["type"] == "release" and i["id"] not in df.id
        ]

        dic = {}
        for item in items:
            artist, album = item.split(" - ", maxsplit=1)
            artist = clean_artist(artist)
            dic[artist] = album

    for artist, album in dic.items():
        # lprint(item)
        print(f"{artist}#{album}")


# no API for creating lists...
# def create_list():
#     ...


# }}}


# album/year conflict often leads to false negative (non-primary title + primary year)
# put year on hold for now, until i think of a better way to use it
# used in release/pmp
def search_with_relpath(relpath: str) -> dict:
    """Expected path structure is: Artist/Album (YYYY). Returns primary release
    of first search result. Draft releases are ignored."""
    artist, album = relpath.split("/")
    if album.endswith(")"):
        album = album[:-7]
    results = search_release(
        artist=artist.split("(")[-1],  # .replace("!", ""),  # transliteration
        album=album,  # assumes fixed 'album (date)' format
        # album = re.sub(r" \(\d{4}\)$", "", album),
        interactive=False,
        primary=True,
    )
    if not results:
        return {}
    for res in results:
        rel = d_get(res["id"])
        if rel["status"] != "Draft":
            return rel
    return {}


def cli_search(
    artist: str,
    album: str,
) -> list[str]:
    """

    Wrapper for search_discogs_release, that allows user to edit search
    queries.

    Returns list of release ids.
    """

    delim = " ::: "
    prefill = delim.join([artist, album])
    query = input_with_prefill("Search:\n", text=prefill)

    if delim in query:
        artist, album = query.split(delim)
        results = search_release(
            artist=artist,
            album=album,
            primary=True,
        )
    else:
        results = search_release(
            album=prefill,
            primary=True,
        )

    if results.empty:
        eprint("no results")
        return []

    return results.id.to_list()


def remove_words(
    _str: str,
    ignore_order: bool = True,
) -> str:
    """Remove commonly encountered words that interfere with automated
    searching. Word order can be discarded for extra conciseness."""
    if ignore_order:
        return " ".join(set(_str.lower().split()) - ALBUM_SUFFIXES)

    result = []
    for word in reversed(_str.split()):
        if word.lower() not in ALBUM_SUFFIXES:
            result.append(word)
    return " ".join(reversed(result))


def search_release(
    artist: str = "",
    album: str = "",
    # interactive: bool = True,
    primary: bool = False,
) -> pd.DataFrame:
    """Return Discogs search results of an `artist`/`album` query.

    If either param is omitted, a general search is performed.

    Only the first 10 are retrieved; I've never found a larger number to be
    necessary.

    If a match is not found in non-interactive mode, an empty dict is returned
    immediately. In interactive mode, search queries are allowed to be edited
    until a match is found (or until the search is aborted).

    Not url encoding is apparently ok.

    Note: search results are NOT the same as releases. Most importantly, search
    results do not contain tracklist/artists, while releases do.

    """

    def sanitize(artist: str, album: str):
        # these actions may also be useful for input_with_prefill
        if album.endswith("]"):
            # move performers from album field to artist
            # https://stackoverflow.com/a/4894156
            performers = album[album.find("[") + 1 : album.find("]")]
            artist = f"{artist} {performers}"
            # album = re.sub(r" \[.+\]$", "", album)
            album = album.rsplit("[", maxsplit=1)[0]

        # remove parentheses
        artist = re.sub(r"\([^)]+\)", "", artist)
        album = re.sub(r"\([^)]+\)", "", album)

        # remove most punctuation for discogs search; ' must be preserved
        # (replacing with space is not allowed)
        artist = re.sub(r"[^\w -']", " ", artist)
        album = re.sub(r"[^\w -']", " ", album)

        # remove years at start (and end)
        artist = re.sub(r"^(19|20)\d{2}", "", artist)
        album = re.sub(r"^(19|20)\d{2}", "", album)
        album = re.sub(r"(19|20)\d{2}$", "", album)
        return artist.lower(), album.lower()  # avoid smartcase

    max_results = 10

    artist, album = sanitize(artist, album)

    # Notes:
    #
    # - Searches only return results that match artist's "main" name; aliases
    # may be rejected.
    #
    # - Releases are searched, rather than masters, as masters tend to produce
    # false positives.
    #
    # - Getting an exact replica of a web result is not trivial (if at all
    # possible), mainly because I don't know what sort method is used in the
    # API. e.g.
    # https://www.discogs.com/search/?q=this+is+it+1538&type=all&type=all

    # anv should not be used over artist

    if artist and album:
        search_url = (
            f"/database/search?release_title={album}&artist={artist}&type=release"
        )
        eprint(f"Searching discogs: {artist} - {album}")
    else:
        query = " ".join((artist, album))
        query = re.sub(r"[^\w -']", " ", query)
        search_url = f"/database/search?q={query}&type=release"

    data = d_get(search_url)

    # if not interactive and not data.get("results"):
    #     eprint(f"No results: {artist} - {album}")
    #     return pd.DataFrame()

    # # no real way to distinguish Draft releases without extra GET
    # pprint(results)
    # raise ValueError

    if primary:
        # results have information on master_url, but not primary release
        # (master). kind of convoluted, up to 2 GETs required:
        # master result (not release) -> primary url -> primary release

        # if any(master := (r for r in results if r.get("master_url"))):
        #     return [d_get(discogs.release.get_primary_url(next(master)))]

        for res in results:
            # print(list(res))
            if m_url := res.get("master_url"):
                return [d_get(release.get_primary_url(d_get(m_url)))]

    # TODO: -- advantages it would provide: easier col indexing, sort by year
    # ascending

    # print(results)
    # raise ValueError

    return results[:max_results]


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(__doc__)

    elif len(sys.argv) == 2:
        if any(x in sys.argv[1] for x in ["/lists/", "/label/"]):
            get_list_releases(
                get_id_from_url(sys.argv[1]),
                label="/label/" in sys.argv[1],
            )

    else:
        print(__doc__)

    sys.exit()
