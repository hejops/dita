#!/usr/bin/env python3
"""Module for manipulating Discogs collection. All functions should either
fetch a csv/df, or read one.

"""
# from pprint import pprint
import os
import sys
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Callable
from typing import Iterator

import flatdict
import numpy as np
import pandas as pd
from pyfzf.pyfzf import FzfPrompt
from tqdm import tqdm
from unidecode import unidecode

from dita.discogs.artist import Artist
from dita.discogs.artist import get_artist_id
from dita.discogs.core import clean_artist
from dita.discogs.core import d_get
from dita.discogs.core import DISCOGS_CSV
from dita.discogs.core import USERNAME
from dita.tag.core import cprint
from dita.tag.core import eprint
from dita.tag.core import lprint
from dita.tag.core import open_url
from dita.tag.core import select_from_list


VAL_DELIM = ":"  # prop:val
FILT_DELIM = ","  # prop1:val,prop2:val


class Collection:  # {{{
    """Initialised with dataframe. Apply filters with .filter(str). Only used
    externally by discogs.compare. The original df is never modified. Aside
    from __len__, all attributes and methods should refered to the filtered df.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        drop_imgs: bool = True,
    ):
        self.df = df  # will never be modified

        if drop_imgs and "img" in df:
            self.df.drop("img", axis=1, inplace=True)

        assert not df.date_added.isna().any()

        # reduce memory usage, not strictly necessary
        cols = ["year", "r", "id", "iid"]
        df[cols] = df[cols].apply(pd.to_numeric, errors="coerce", axis=1)

        # a dict[str, str] would have been the ideal type, but this is not
        # feasible since 'genre' can be specified more than once. i am not
        # willing to allow 'genre:a+b' or whatever.
        # TODO: dict[str, set[str]]
        self.filter_list = ()  # tuple[tuple[str,str]]

        self.filtered = self.df.copy()

    def __len__(self) -> int:
        return len(self.df)

    def __str__(self):
        return self.filtered.to_string()

    def __repr__(self):
        # not meant for testing
        return "\n".join(
            [
                f"Collection: {len(self)} total",
                f"Filtered: {len(self.filtered)}",
                f"Filters applied: {self.filter_list}",
            ]
        )

    def to_dict(self):
        """Returns result of filtering as a dict"""
        return self.filtered.set_index("id").title.to_dict()

    def reset_filters(self):
        """Removes all user-defined filters, reverts df to its initial
        state."""
        self.filter_list = ()
        self.filtered = self.df.copy()

    def apply_filter(
        self,
        # self.filtered: pd.DataFrame,
        prop: str,
        val: str,
        # group_artist: bool = False,
    ) -> pd.DataFrame:
        """Apply a single filter to the df."""

        def filter_year(df):
            """Allows single year, and hyphen-delimited year range"""
            if val.isnumeric():
                df = df[df.year == int(val)]
            else:
                start, end = val.split("-")
                # df = df[(int(start) <= df.year) & (df.year <= int(end))]
                df = df[int(start) <= df.year <= int(end)]
            return df

        def filter_text(df):
            """Standard string-based filters"""
            # .copy() avoids "hidden" chaining
            # https://www.dataquest.io/blog/settingwithcopywarning/
            df = df.dropna(subset=prop).copy()

            # # better to use lower() once than to pass case flag multiple times
            # df[prop] = df[prop].str.lower()  # .copy()

            if val.isascii():
                df[prop] = df[prop].apply(unidecode)

            # first use contains/startswith to allow disambiguation
            # note: contains() requires regex=False
            # https://pandas.pydata.org/docs/reference/api/pandas.Series.str.contains.html
            matches = df[prop].str.contains(
                val,
                regex=prop == "title",
                case=False,
            )

            # print(df[matches])

            if not matches.any():
                print(val, "not found in field", prop)
                raise ValueError

            df = df[matches]

            if prop == "artist":
                # https://pandas.pydata.org/docs/reference/api/pandas.Series.unique.html
                if len(disamb := df.artist.unique()) > 1 and len(self.filter_list) == 1:
                    name = select_from_list(disamb, "Disambiguation required")
                    # print(df[df.artist == name])
                    df = df[df.artist == name]

            elif prop == "title":
                # df = df[matches]
                # for albums with >1 artist/performer, concat artists and take mean of r
                df = df.groupby("id", as_index=False).aggregate(
                    {
                        "artist": ", ".join,
                        "r": np.mean,  # a neat hack
                        # "title": lambda x: x[0],
                        "title": lambda x: list(x)[0],
                        # "r": lambda x: x[0],  # a neat hack
                    }
                )
                print(df)

            # else:
            #     df = df[matches]

            return df

        # if prop == "genre" and val[-1] == "!":
        #     group_artist = True
        #     val = val.removesuffix("!")

        if prop == "r":
            if int(val) > 2:
                self.filtered = self.filtered[self.filtered[prop] >= int(val)]
            else:
                self.filtered = self.filtered[self.filtered[prop] <= int(val)]

        elif prop == "date_added":
            if val.isnumeric():
                # https://stackoverflow.com/a/68371636
                now = datetime.now().replace(tzinfo=timezone(offset=timedelta()))
                self.filtered = self.filtered[
                    now - self.filtered[prop] < pd.to_timedelta(f"{val} days")
                ]
            else:
                # yes, this redundancy is required
                self.filtered = self.filtered[
                    self.filtered[prop].astype(str).str.startswith(val)
                ]

        elif prop == "year":
            self.filtered = filter_year(self.filtered)

        else:
            self.filtered = filter_text(self.filtered)

        # print(df)

        return self.filtered

    def filter(
        self,
        filters: str = "",
        # group_artist: bool = False,
        # unique_albums: bool = False,
        sort: bool = True,
    ):
        """Parses filters to be sequentially applied to an offline collection.
        Filters are to be passed as strings in the form '<key>:<value>', which
        will be stored. If <value> is left blank, user input will be required.
        Special prefixes are allowed.

        The actual applying of filters is done by apply_filter().

        If a filter clears the selection, the initial state can be restored
        with reset_filters().

        Sorting is done by default.

        Examples:
            artist:[blank]
            genre:black metal (spaces allowed)
            genre:black metal,r:3 (r => 3)
            genre:black metal,r:2 (r =< 2)
            genre:thrash@ (sorts by newest last)
            genre:thrash! (groups by artist and calculates mean rating --
                            warning: discards release information!)
            title:Goldberg Variations (groups releases by id)

        Args:
            filters: [TODO:description]
            group_artist: [TODO:description]

        Returns:
            [TODO:description]

        Raises:
            [TODO:name]: [TODO:description]
            [TODO:name]: [TODO:description]
        """

        for filt in filters.split(FILT_DELIM):
            if VAL_DELIM not in filt:
                eprint(f"Skipping invalid filter '{filt}'")
                continue

            # empty values must be caught before the tuple is prepared
            if filt.endswith(VAL_DELIM):
                key = filt.rstrip(VAL_DELIM)
                eprint("No value specified")
                val: str = FzfPrompt().prompt(
                    sorted(self.filtered[key].unique()),
                    "--reverse",
                )[0]
                filt += val

            spl = tuple(filt.split(VAL_DELIM, maxsplit=1))
            if spl not in self.filter_list:
                self.filter_list += (spl,)

            # defaultdict
            # k, v = filt.split(VAL_DELIM, maxsplit=1)
            # self.filter_list[k].add(v)

        # if unique_albums or "date_added" in filters:
        if "date_added" in filters:
            self.filtered.drop_duplicates(inplace=True, subset=["date_added"])

        # print(self.filter_list)

        for key, val in self.filter_list:
            if key not in self.filtered.columns:
                eprint("Property must be one of:", "/".join(self.filtered.columns))
                raise ValueError

            print(key + VAL_DELIM + val)

            self.filtered = self.apply_filter(
                # self.filtered,
                key,
                val.rstrip("!@"),
                # group_artist,
            )

        if {"artist", "title"}.issubset({f[0] for f in self.filter_list}):
            # this would drop too many columns
            return

        # drop columns that were used for filtering, but never drop r/title
        # always drop label column
        for key in [f[0] for f in self.filter_list] + ["label"]:
            if (
                not self.filtered.empty
                and key in self.filtered
                and key not in ["r", "title"]
            ):
                # if not df.empty and prop in df and prop not in ["r", "genre"]:
                self.filtered = self.filtered.drop(key, axis=1)

        # print(self.filtered.to_dict())

        if sort or any(filt[1] == "@" for filt in self.filter_list):
            self.sort()

        if any(filt[1] == "!" for filt in self.filter_list):
            self.filtered = group_collection_by_artist(self.filtered)

        # return self.df

    def sort(self):
        """Standard sort method is: rating, artist, year. If date_added is True
        (e.g. 'r:3@'), it will take precedence over everything else.
        """
        sortkey = {
            # "date_added": True,
            "r": False,
            "artist": True,
            "year": True,
        }

        sortkey = {k: v for k, v in sortkey.items() if k in self.filtered.columns}

        # if any(filt[1][-1] == "@" for filt in self.filter_list):
        #     sortkey = {"date_added": True} | sortkey

        if not any(filt[1][-1] == "@" for filt in self.filter_list):
            sortkey.pop("date_added", None)

        # print(self.filter_list, sortkey)
        # raise ValueError

        # eprint(sortkey)

        # # sort artist, case-insensitive
        # if "artist" in sortkey:
        #     self.filtered = self.filtered.sort_values(
        #         by="artist",
        #         key=lambda col: col.str.lower(),
        #     )

        self.filtered = self.filtered.sort_values(
            by=list(sortkey.keys()),
            ascending=list(sortkey.values()),
        )


# }}}


# dumping {{{


def dump_collection_to_csv():
    """Fetch all pages of a user's collection and write to csv"""

    df = (
        # note: tqdm is kinda goofy with generators
        pd.DataFrame(tqdm(get_collection_releases()))
        # pd.DataFrame(get_collection_releases())
        # .drop_duplicates(["id"])	# allow composers and performers to be listed
        .sort_values("date_added")
    )
    df.to_csv(DISCOGS_CSV)


def get_wantlist_releases() -> pd.DataFrame:
    """Note: wantlist doesn't actually contain any info on marketplace availability..."""
    # pprint(discogs_get(f"/users/{USERNAME}/wants?per_page=500&page=1"))
    # raise Exception

    return (
        pd.DataFrame(get_collection_releases(wantlist=True))
        .drop_duplicates(subset=["id"])
        .sort_values("artist")
    )


def get_collection_releases_verbose():
    """For debugging of collection fields only"""
    i = 1
    # status == Draft -> warn
    while (
        url := f"/users/{USERNAME}/collection/folders/0/releases?per_page=500&page={i}"
    ) and ("releases" in (page := d_get(url))):
        for rel in page["releases"]:
            # pprint(flatdict.FlatDict(rel["basic_information"], delimiter="."))
            # pprint(rel["basic_information"])
            # raise ValueError
            yield flatdict.FlatDict(rel, delimiter=".")
        i += 1


def get_collection_releases(
    all_fields: bool = False,
    wantlist: bool = False,
) -> Iterator[dict[str, Any]]:
    """

    Scrape all pages of a user's Discogs collection (the API does not support
    full collection export). Note: while sort order used is unknown,
    chronological order can be achieved via the 'instance_id' or 'date_added'
    fields.

    There is no way to retrieve a subset of a collection; filtering must be
    done manually.

    Args:
        all_fields: flatten nested fields (delimiter = '.')
        wantlist: get wantlist instead
    """

    if wantlist:
        query_type = "wants"
    else:
        # 0 (all) or 1 (uncategorised) folders are ok, no auth required
        # https://www.discogs.com/developers/#page:user-collection,header:user-collection-collection-items-by-folder
        query_type = "collection/folders/0/releases"

    # nice hack lol
    field = query_type.rsplit("/", maxsplit=1)[-1]

    # pprint(d_get(f"/users/{USERNAME}/{query_type}?per_page=250&page=1"))
    # raise ValueError

    i = 1
    # per_page=500 may cause problems with json.loads
    while (url := f"/users/{USERNAME}/{query_type}?per_page=250&page={i}") and (
        page := d_get(url).get(field)
    ):
        for rel in page:
            if all_fields:
                # yield pd.json_normalize(r).to_dict(orient="records")
                flatd = flatdict.FlatDict(rel, delimiter=".")
                lprint(flatd)
                raise NotImplementedError

            # yield for each artist (don't group here)
            for art in rel["basic_information"]["artists"]:
                item = {
                    # artist names are not cleaned, in order to allow disambiguation when offline
                    # not possible to distinguish composer/performer without an extra get, too bad
                    "artist": art["name"],
                    "title": rel["basic_information"]["title"],
                    "year": rel["basic_information"]["year"],
                    "r": rel["rating"],
                    "genre": ", ".join(sorted(rel["basic_information"]["styles"])),
                    "label": (
                        label[0]["name"]
                        if (label := rel["basic_information"].get("labels"))
                        else ""
                    ),
                    "id": rel["id"],
                    "date_added": rel["date_added"],  # 2022-10-23T15:16:36-07:00
                    "img": (
                        img
                        if (img := rel["basic_information"].get("cover_image"))
                        else ""
                    ),
                }
                # wantlist has no instance_id
                if not wantlist:
                    item["iid"] = rel["instance_id"]
                yield item

        eprint("Scraped page", i)
        i += 1

    eprint("Done")
    # time.sleep(2)


# }}}


def top_n_sum(
    artist_ratings: pd.Series,
    num: float = 3,
    strict: bool = True,
) -> int:
    """
    Custom metric to retrieve 'overall rating' of an artist. An artist must
    have at least one 5 rating, otherwise it is removed automatically.

    The sum of the top <n> ratings is then returned (maximum = n * 5).

    Taking the mean over all ratings tends to produce an undesirable (and
    extremely strong) bias towards artists with few rated releases, even more
    so for median.
    """
    if artist_ratings.to_list().count(5) >= num:
        return int(5 * num)

    # removes about 600 artists
    if strict and ~artist_ratings.isin([5]).any():
        return 0

    # # extra strict mode, tends to favour small but strong discogs (e.g. TCP)
    # if artist_ratings.isin([5]).sum() < 2:
    #     return 0

    # if (
    #     len(artist_ratings) < 50
    #     # stricter for medium-sized discogs (removes Cloudkicker, Perfume, Red Velvet)
    #     and artist_ratings.isin([1]).sum() / len(artist_ratings) > 0.1
    # ):
    #     # no flops allowed, e.g. toe
    #     # not applied to very large discogs
    #     return 0

    _sum = sum(artist_ratings.sort_values(ascending=False)[: int(num)])
    return _sum  # - 10

    # # allows artists with 2 releases rated
    # return int(np.mean(artist_ratings.sort_values(ascending=False)[:3]) * 3)


def group_collection_by_artist(
    df: pd.DataFrame,
    groupby: str = "artist",
    min_releases: int = 2,
    metric: Callable = top_n_sum,
) -> pd.DataFrame:
    """Wrapper for df.groupby, with metric to be applied

    Args:
        df: df to be grouped
        groupby: primarily supports 'artist', may add support for 'label' in future
        metric: default is top_three_sum. Alternative metrics include:
            mean:   lambda x: np.mean(x) * 3
            count:  len
            median: lambda x: np.median(x) * 3

    Returns:
        df, with columns [<groupby>, 'r']
    """
    # clean first, otherwise groupby will be performed incorrectly
    df["artist"] = df.artist.apply(clean_artist)

    if groupby == "label":
        df = df[df.label != "Not On Label"]

    # Series[bool], unique items in column that fulfill the condition
    cond: pd.Series = df[groupby].value_counts() >= min_releases

    # whether the df row fulfills the (above) column condition (no extra column needed)
    met: pd.Series = df[groupby].map(cond)

    return (
        df[met]  # get the subset
        .groupby(groupby, as_index=False)["r"]
        .apply(metric)
        # keep cols (better to keep than drop)
        [[groupby, "r"]]
        .sort_values(
            ["r", groupby],
            ascending=[False, True],
        )
    )


def cprint_df(df):
    """Print df with color-coded (ANSI-escaped) 'r' column. ANSI escapes lead
    to column misalignment with the default printer, possibly because the
    length of the escaped string exceeds 'r', but .to_markdown() can be used to
    circumvent this.
    """

    mean_rating = round(df.r.mean(), 2)

    df.r = df.r.apply(cprint, _print=False)

    df_str = df.reset_index(drop=True).to_markdown()

    # truncate title
    excess = len(df_str.split("\n")[0]) - os.get_terminal_size().columns
    if excess > 0:
        df.title = df.title.apply(lambda x: x[: max(df.title.str.len()) - excess])
        df_str = df.reset_index(drop=True).to_markdown()

    print(df_str)
    cprint(mean_rating)


def get_percentiles(
    df: pd.DataFrame,
    col: str = "r",
) -> pd.DataFrame:
    """Adds 'perc' column to df."""
    master = df[col].to_list()
    percentiles = {}
    perc = 1

    while True:
        mslice = set(master[: len(master) // 100 * perc])
        percentiles |= {x: perc for x in mslice - set(percentiles)}
        if mslice == set(master):
            break
        perc += 1

    percentiles[0] = 100

    df["perc"] = df.r.apply(lambda x: percentiles[x])
    return df


if __name__ == "__main__":
    if ":" in sys.argv[1]:  # and "http" not in sys.argv[1]:
        DISCOGS_DF: pd.DataFrame = pd.read_csv(
            DISCOGS_CSV,
            index_col=0,
            parse_dates=["date_added"],  # allow calculation of date differences
        )
        coll = Collection(DISCOGS_DF)
        coll.filter(" ".join(sys.argv[1:]))
        # print(col)
        cprint_df(coll.filtered.drop_duplicates("id"))
        sel = input()
        if sel:
            print(d_get(coll.filtered.iloc[int(sel)].id)["uri"])
        else:
            # assumes filter was artist:XXX
            if coll.filter_list[0][0] == "artist":
                Artist(get_artist_id(coll.filter_list[0][1])).rate_all()

    elif len(sys.argv) == 2:
        if sys.argv[1] == "--dump":
            get_collection_releases_verbose()
            dump_collection_to_csv()

        elif sys.argv[1] == "--want":
            print(get_wantlist_releases())

        elif sys.argv[1] == "--top":
            PERC = 2  # float also allowed, e.g. 2.5
            top_df = group_collection_by_artist(
                pd.read_csv(DISCOGS_CSV),
                metric=lambda x: top_n_sum(x, 10 // PERC),
                # groupby="label",
                # metric=lambda x: np.mean(x) * 3,
                # metric=len,
                # metric=lambda x: np.median(x) * 3,
            )
            # print(top_df, len(top_df))
            # raise ValueError
            top_df = get_percentiles(top_df)
            # print(top_df[top_df.perc == PERC + 1])
            top_df = top_df[top_df.perc <= PERC]
            print(top_df, len(top_df))

            # from etc.rym_artists import print_rym_artists

            # print_rym_artists(top_df)

            # print_rym_artists(top_df[top_df.r > 22])
            # print()
            # print_rym_artists(top_df[top_df.r <= 22])

        elif sys.argv[1] == "--random":
            coll = Collection(pd.read_csv(DISCOGS_CSV))
            coll.filter("r:4")
            ran = coll.filtered.sample(n=1)
            open_url(
                "https://open.spotify.com/search/",
                " ".join(
                    [ran.artist.iloc[0], ran.title.iloc[0]],
                ).split(),
                suffix="albums",
            )
