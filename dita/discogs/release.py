#!/usr/bin/env python3
"""Module for parsing Discogs release objects.

As far as possible, all functions must take a release (NOT release id) as the
main arg. To prevent namespace collision when importing this module, prefer the
variable name rel instead of release.
"""

import os
import shlex
import sys
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
from titlecase import titlecase

import dita.discogs.core as dc
from dita.tag.core import eprint
from dita.tag.core import extract_year
from dita.tag.core import fill_tracknum
from dita.tag.core import is_ascii
from dita.tag.core import lprint
from dita.tag.core import open_url
from dita.tag.core import select_from_list
from dita.tag.core import tcase_with_exc


# i wanted to unit test this, but it turns out i simply can't get left align to
# work
def release_as_str(rel: dict) -> str:
    """Display essential information of a Discogs release."""
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_rows", None)
    # pd.set_option("display.colheader_justify", "left")

    df = get_release_tracklist(rel).drop(columns=["sub_tracks"])
    # print(df[["title", "duration"]])
    return df.to_string()


def display_release_results(
    ids: list[str],
    num_tracks: int,
) -> dict:
    """Only used in `tag.fix`; may be replaced with the simpler `display_tracks`.

    Similar to `try_auto`, might merge

    Returns
    -------
        Discogs release, empty if nothing matches

    """
    for i in ids:
        rel = dc.d_get(i)

        if not {"artists", "tracklist", "uri"}.issubset(rel):
            print({"artists", "tracklist", "uri"} - set(rel))
            continue

        tracklist = get_release_tracklist(rel)

        if len(tracklist) != num_tracks:
            # print("tracks diff", rel["uri"])
            continue

        print(rel["uri"].partition("-")[0])
        print(rel["title"])
        # print(release_as_str(rel))

        # lprint(rel)
        print(" / ".join(a["name"] for a in rel["artists"]))
        # print(rel["artists_sort"])
        print(rel["title"])
        print(rel["year"])
        print(rel["uri"])
        print(tracklist[["tracknumber", "title", "dur"]])
        print()

        if input("Accept? [y/N]: ") == "y":
            return rel

    print("Not found")
    return {}


def get_performers(
    release: dict,
    composers: set[str],
) -> list[str]:
    """Extract from `artists` and `extraartists` fields of release.

    Composers (determined in advance by `get_artists`) are subtracted.
    """
    # print(discogs_release)
    if "main_release_url" in release:
        release = dc.d_get(release["main_release_url"])

    # the only place to find composer credit is extraartists
    # only release has extraartists, not master!
    # composer extraction is done in get_discogs_tracklist, not here

    # primary artists are always fine; just remove composer

    performers = [x["name"] for x in release["artists"] if x["name"] not in composers]

    if not performers and "extraartists" in release:
        # i have never needed more roles than this
        roles = ["conductor", "orchestra", "directed"]
        performers = [
            x["name"]
            for x in release["extraartists"]
            if x and x["role"].lower() in roles
        ]

    for comp in composers:
        # eprint(comp)
        if comp in performers:
            performers.remove(comp)

    performers = [dc.clean_artist(x) for x in performers]
    return list(dict.fromkeys(performers))


def get_artists_from_album_credits(release: dict) -> list[str]:
    """Get artist(s) of each track, by parsing the 'extraartists' field of a
    release.

    Not robust, especially if track range strings are checked.
    """
    # https://www.discogs.com/release/2235562 -- standard

    # release -> extraartists -> role
    # should only have length 1

    # corner case: role = Written-By
    # https://www.discogs.com/release/4703012

    if "extraartists" in release:
        artists = [a for a in release["extraartists"] if a["role"].startswith("Compos")]
    else:
        artists = []

    if len(artists) == 1:
        return [artists[0]["name"]]

    # { 1 : 'artist' , ... }

    try:
        artist_ranges = {
            i: art["name"]
            for art in artists
            for i in dc.parse_string_num_range(art["tracks"])
        }
    except ValueError:
        return []

    # sort by key, then return values
    artist_ranges = dict(sorted(artist_ranges.items()))
    return list(artist_ranges.values())


def is_classical(release: dict) -> bool:
    genres = set(release.get("genres", ""))
    return (
        not genres.intersection(
            {
                "Jazz",
                "Pop",
                "Folk, World, & Country",
                "Electronic",
                "Rock",
            },
        )
        # edge case: classical and electronic -- https://www.discogs.com/release/2417085
        or "Classical" in genres
    )


def apply_transliterations(
    transliterations: dict[str, list[str]],
    discogs_tags: pd.DataFrame,
) -> pd.DataFrame:
    """Append transliteration to artist column.

    This only modifies the df if it is possible to apply from
    `transliterations` in an unambiguous manner, or if tty input is possible.
    Otherwise, the df is returned unchanged.
    """
    if (
        # 1 transliteration per artist
        all(len(x) == 1 for x in transliterations.values())
        # all artists have 1 translit
        and len(transliterations) == len(set(discogs_tags.artist))
    ):

        def auto_transliterate(name: str) -> str:
            if is_ascii(name):
                return name
            if match := transliterations.get(name.lower()):
                return f"{name} ({match[0]})"
            return name

        discogs_tags.artist = discogs_tags.artist.apply(auto_transliterate)
        assert all(is_ascii(x) for x in discogs_tags.artist)
        return discogs_tags

    if not (sys.__stdin__ and sys.__stdin__.isatty()):
        return discogs_tags

    def transliterate(name: str) -> str:
        if is_ascii(name):
            return name
        return name.lower().replace(native, f"{native} ({trans})")

    for native, trans_l in transliterations.items():
        if len(trans_l) == 1:
            trans = trans_l[0]
        elif not trans_l:
            # print("No transliterations found:")
            open_url("https://duckduckgo.com/?t=ffab&q=", native)
            trans = input(f"Provide transliteration for {native}: ")
        else:
            trans: str = select_from_list(trans_l, "Select transliteration")

        discogs_tags.artist = discogs_tags.artist.apply(transliterate)

    return discogs_tags


def get_discogs_tags(release: dict) -> pd.DataFrame:  # {{{
    """Transform the contents of a Discogs release.

    The df will contain the following columns:

        `"tracknumber", "title", "artist", "album", "date"`

    While "tracknumber" is the index in principle, it is left as a column for
    ease of mapping to the corresponding tag field. GET may be required (for
    date of master/primary release). No user input is required at any point.
    """
    discogs_tags = get_release_tracklist(release)

    discogs_tags["title"] = discogs_tags.title.apply(titlecase)

    # 1. artist(s)

    # # masters don't have 'artists_sort'
    # assert "artists_sort" in release

    artists = get_artists(release, len(discogs_tags))
    artists = [dc.clean_artist(a) for a in artists]
    artists = [tcase_with_exc(a) for a in artists]

    if len(artists) == 1:
        discogs_tags["artist"] = artists[0]
    else:
        discogs_tags["artist"] = artists

    # """1a. determine if album is VA (compare tracklist vs artists)"""
    # corner case: split with doubled tracklist
    # https://www.discogs.com/release/14700702
    if len(artists) == 2 * len(discogs_tags):
        artists = artists[: int(len(artists) / 2)]

    assert artists

    # 2. album
    album: str = titlecase(release["title"])

    # 2a. append performers if classical

    # generally safer if Classical is the only item because of edge cases like
    # https://www.discogs.com/release/2624119
    # https://www.discogs.com/release/3973813
    # https://www.discogs.com/release/1695184
    # corner case https://www.discogs.com/release/26267087

    if "Classical" in release["genres"] and is_classical(release):
        performers = get_performers(
            release=release,
            composers=set(artists),
        )

        # for SRP, transliteration check should -not- be done at this level

        # remove all composers
        performers = [p for p in performers if p not in artists]

        if performers:
            album += f" [{', '.join(performers)}]"

    discogs_tags["album"] = album

    # 3. date
    if "master_url" in release:
        master_release = dc.d_get(release["master_url"])
        if not (date := master_release.get("year")):
            # https://www.discogs.com/release/5045281
            eprint("master no date")
            if not (date := release.get("year")):
                date = 0
    elif not (date := release.get("year")):
        if "notes" in release:
            print(release["notes"])
        date = 0

    if (
        not date
        and release.get("notes")
        and (dates := extract_year(release["notes"]))
        and len(dates) == 1
    ):
        date = dates[0]

    discogs_tags["date"] = str(date)

    # print(discogs_tags.artist)
    # raise ValueError

    return discogs_tags


# }}}


def get_release_tracklist(release: dict) -> pd.DataFrame:
    """Process tracklist within a complete Discogs release (not one from under
    artist releases!). No extra GET required. Uses a two-level loop behind the
    scenes in order to merge tracks and subtracks, returning a flattened dict.
    Should always be called by `get_discogs_tags()`.

    Returns:
        DataFrame -- example:
    ```
          tracknumber                    title  dur
       0           01    Problematic Courtship    0
       1           02              My Recovery    0
       2           03   Farewell Ne'er Do Well    0
    ```

    """

    def expand_subtracks(row: pd.Series) -> pd.Series:
        if "sub_tracks" in row and isinstance(row.sub_tracks, dict):
            row.title += f" - {row.sub_tracks['title']}"
            row.duration = row.sub_tracks["duration"]
        return row

    # put all dicts (track and subtrack) into df directly, then on operate df
    # empty tracks https://www.discogs.com/release/6442550
    df = (
        pd.DataFrame(release["tracklist"])
        .replace({"title": {"", np.nan}})
        .dropna(subset="title")
    )

    if "sub_tracks" in df.columns:
        df = (
            df.explode("sub_tracks")
            .reset_index(drop=True)
            .apply(expand_subtracks, axis=1)
        )

    # https://stackoverflow.com/a/54276300

    ignore_words = ("dvd", "video")
    df: pd.DataFrame = df[
        (df.type_ != "heading")
        # & (df.position.str.split()[0] not in ignore_words)
        & (
            df.position.apply(
                lambda t: not any(w in t.lower() for w in ignore_words if t),
            )
        )
    ]

    df.reset_index(inplace=True)
    df.index += 1
    df["tracknumber"] = df.index.map(fill_tracknum)  # index requires map, not apply
    df["dur"] = df.duration.apply(dc.duration_as_int)

    # detect doubled tracklists (almost always cassette)
    # TODO: if durations are provided, should check them first
    # untitled tracks, all different:
    # https://www.discogs.com/release/16326633
    # https://www.discogs.com/release/1945094

    titles = df.title.apply(lambda x: x.strip()).to_list()
    n_titles = len(titles)
    if (
        # n_titles > 2
        # and
        n_titles % 2 == 0
        and titles[: n_titles // 2] == titles[n_titles // 2 :]
        # and all(titles.count(x) % 2 == 0 for x in titles)
        and all(titles.count(x) == 2 for x in titles)
    ):
        lprint("Doubled tracklist detected")
        df = df[: n_titles // 2]

    # print(df)
    return df


def get_format(release: dict) -> str | None:
    rel = pd.Series(release)
    if "formats" not in rel:
        return None
    return pd.DataFrame(rel.formats).name.iloc[0]


def get_artists(
    release: dict,
    num_files: int,
) -> list[str]:
    """Extract artist(s) from a Discogs release. Primary releases should always
    be used, as the information tends to be most correct/tractable.

    Broadly speaking, there are three main types of releases:
        - Classical -> composer(s) only -> 1/2/3/(4)
        - Non-classical split -> per-track artist -> 1/(4)
        - Non-classical standard -> artist_sort -> 4
    """

    def artists_ok(
        msg: str,
    ) -> bool:
        # early return (filter and check length at each step)
        # remove null items
        if not (filtered := list(filter(None, artists))):
            return False

        # if track credits, must == num_files
        if "composer" in msg:
            if len(filtered) == num_files:
                eprint(msg)
                return True

        elif len(filtered) in [1, num_files]:
            eprint(msg)
            return True

        return False

    artists = []

    # 1. release -> tracklist -> extraartists -> role = composer
    artists = dc.extract_track_artists(
        release,
        require_composer_role=is_classical(release),
    )
    # print(artists)
    # raise ValueError
    if artists_ok("track credits (composer)"):
        return artists

    # 2a. release -> extraartists -> role = composer
    # 2b. release -> extraartists -> tracks
    if is_classical(release):
        artists = get_artists_from_album_credits(release)
        # print(artists)
        # assert False
        if artists_ok("album credits (guess)"):
            return artists

    # 3. release -> artists
    # https://www.discogs.com/release/3745745
    artists = [x["name"] for x in release["artists"]]
    # print(release["artists"][0])
    # raise ValueError
    if artists_ok("track credits"):
        return artists

    # 4. artists_sort -- this will -always- succeed
    # note: releases by 'Various' don't have artist_sort field; this should be
    # caught in one of the above steps

    artist: str = release["artists_sort"]

    if len(release["artists"]) > 1:
        artist = artist.replace(" & ", ", ")
        artist = artist.replace(" / ", ", ")

        if len(artist) > 100:
            # catch long artist here -- probably rare
            # https://www.discogs.com/release/2183466
            artist = artist.partition(",")[0]

    eprint("artists_sort (fallback):", [artist])
    # print(release["artists_sort"], artist)
    return [artist]


def get_versions_of_master(
    release: dict,
    **filters,
) -> pd.DataFrame:
    """[TODO:summary]

                        title released      format major_formats  resource_url
    0  Inventions & Sinfonias     2015                      [CD]  ...
    1  Inventions & Sinfonias     2016  MP3, Album        [File]  ...

        Note: format can be empty
    """
    master_url = release.get("master_url")
    if not master_url:
        raise ValueError("Release has no master")

    versions_url = master_url + "/versions"

    # filters_available = dc.d_get(versions_url)["filters"]["available"]

    if filters:
        versions_url += "?" + "&".join(f"{k}={v}" for k, v in filters.items())

    df = pd.DataFrame(dc.d_get(versions_url)["versions"])

    # def stringify_dict(dic: dict) -> dict:
    #     dic.pop("stats")
    #     dic.pop("thumb")
    #     for k, val in dic.items():
    #         if isinstance(val, list):
    #             dic[k] = ", ".join(val)
    #     return dic

    # # drop columns with all values same; this requires all cols to be strings
    # nunique: pd.Series = df.nunique()
    # cols_to_drop: IndexLabel = nunique[nunique == 1].index  # type:ignore
    # df = df.drop(cols_to_drop, axis=1)

    return df


def get_primary_url(release: pd.Series) -> str:
    """Get URL for the primary version of a release.

    This uses the `main_release_url` field, which is **only** visible through the
    API. In most cases, the main release is often the earliest one, but not
    always. No attempt is made to infer what the 'correct' master release
    should be.
    """
    # master
    if "main_release_url" in release:
        return release.main_release_url

    # release with master -- recurse
    # if master_url := release.master_url:
    if "master_url" in release:
        return get_primary_url(pd.Series(dc.d_get(release.master_url)))

    assert "resource_url" in release, release

    # release with no master
    return release.resource_url


# def show_listing(lid: int):
#     # # next to useless
#     # x = dc.d_get(f"/marketplace/stats/{rid}")  # {curr_abbr}
#     # 2534003883
#     x = dc.d_get(f"/marketplace/listings/{lid}")  # {curr_abbr}
#     lprint(x)


def get_primary_url_from_web(web_url: str):
    rel = dc.d_get(dc.web_url_to_api(web_url))
    url = dc.api_url_to_web(get_primary_url(pd.Series(rel)))

    # does not actually distinguish between "release is primary" and
    # "release has no master"
    return url


def main():
    # arg inference should be replaced with explicit flags
    if sys.argv[1].startswith(dc.PREFIX):  # web url -> primary
        url = get_primary_url_from_web(sys.argv[1])

        if url in sys.argv[1]:
            os.system("notify-send 'Current release is primary'")
        print(url)

    elif sys.argv[1] == "--versions" and sys.argv[2].startswith(dc.PREFIX):
        # web url -> select version(s)
        # almost always called from browser
        # will be integrated into tag.fix at some point

        versions = get_versions_of_master(
            dc.d_get(sys.argv[2]),
            interactive=True,
        )
        cols = ["title", "released", "format", "major_formats", "resource_url"]
        print(versions[cols])

    elif sys.argv[1].isnumeric():
        print(dc.release_as_str(dc.d_get(sys.argv[1])))

    elif sys.argv[1].endswith(")"):  # relpath
        # artist, album = sys.argv[1].split("/")
        rel = dc.search_with_relpath(sys.argv[1])
        # pprint(r)

        if url := rel.get("uri"):
            ids = pd.read_csv(dc.DISCOGS_CSV).id
            if ids.isin([rel.get("id")]).any():
                eprint("Already rated", url)
            else:
                print(url)
        else:
            query = quote_plus(sys.argv[1].replace("/", "+"))
            print(f"https://www.discogs.com/search/?q={query}&type=all")
            os.system(f"notify-send 'Not on discogs' {shlex.quote(sys.argv[1])}")


if __name__ == "__main__":
    main()
