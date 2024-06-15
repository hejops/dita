#!/usr/bin/env python3
"""Module for parsing Discogs artist objects. To prevent namespace collision
when importing this module, prefer the variable name art instead of artist.

"""
import os
import sys
import time
from random import choice
from typing import Any

import pandas as pd
import readchar

import dita.discogs.core as dc
from dita.discogs import rate
from dita.tag.core import eprint
from dita.tag.core import is_ascii
from dita.tag.core import select_from_list

# from dita.tag.core import open_url
# from dita.tag.core import tcase_with_exc
# from tag.core import tabulate_dict
# import dita.discogs.release


class Artist:  # {{{
    """Retrieve Discogs releases by an artist.

    In most cases, the first 100 releases are always fetched. If the artist has
    >1000 items credited, a random page is fetched instead.

    Basic CLI navigation is supported; might be moved into Browser instead.

    Args:
        artist_id: numeric string
        per_page: releases per page (sent in GET request)
    """

    # Metallica 3.3k / Mozart 43k
    max_items = 5000

    # if an artist's first release is from before min_year, always start at a
    # random page instead of page 1
    # Miles Davis (8k, first release 1947)
    # Stevie Wonder (12k, first release 1962)
    min_year = 1945

    page: int = 1
    per_page: int = 100  # 100 is safest to avoid http 502
    position: int = 0  # position in the list

    def __init__(
        self,
        a_id: int,
    ):
        # static, will never change
        self.a_id = a_id

        results = self.get_releases()
        # print(results)

        if "pagination" not in results:
            raise ValueError(self.a_id)

        self.total = results["pagination"]["items"]
        # print(self.total)

        def random_page(_max: int) -> dict:
            self.page = choice(range(1, _max))
            results = self.get_releases()
            if "Main" in pd.DataFrame(results["releases"]).role.to_list():
                return results
            return random_page(self.page)

        if (
            len(self) > self.max_items
            and results["releases"][0]["year"] < self.min_year
        ):
            # print(
            #     len(self),
            #     "releases, first release:",
            #     results["releases"][0]["year"],
            # )
            results = random_page(results["pagination"]["pages"])

        # not calling fillna will lead to float NaNs all over the place
        self.releases = pd.DataFrame(results["releases"]).fillna(0)

        # print(self.releases)
        # raise ValueError

        # # since the following filters irreversibly mutate the original df, it
        # # might be preferable to store the filtered state in a separate attrib
        # self.filtered = self.releases.copy()

        # print(self.a_id)

        # self.current_rel = self.releases[self.position]

    def get_name(self) -> str:
        """Getting the artist name requires a dedicated get
        ('/artists/{artist_id}'). To avoid this, 'artist' field is simply
        parsed to get the most common artist name.
        """
        # https://stackoverflow.com/a/52039106
        return self.releases[self.releases.artist != "Various"].artist.mode().iloc[0]

    def __len__(self) -> int:
        # return len(self.releases)
        return self.total

    def __str__(self):
        return f"{self.get_name()} [{self.position+1}/{self.total}]"

    # def filter_data_quality(self):
    #     ...
    #     # # as a surrogate for data_quality (latter requires a get)
    #     # if (
    #     #     results["pagination"]["items"] > max_items
    #     #     and release["stats"]["community"]["in_collection"] < 5
    #     # ):
    #     #     # print("foo")
    #     #     continue

    def filter_from_df(
        self,
        local_df,
    ):
        """Removes rows whose ids are found in <local_df>."""
        rated = local_df.id  # .to_list()
        before = len(self.releases)

        if "main_release" in self.releases:
            self.releases = self.releases[~self.releases.main_release.isin(rated)]
        else:
            self.releases = self.releases[~self.releases.id.isin(rated)]

        if self.releases.empty:
            print(f"All {before} releases rated")
        elif before != len(self.releases):
            print(before - len(self.releases), "rated items were removed")

    def filter_by_format(
        self,
        exclude: int = 1,
    ):
        """Typically removes compilations. If exclude=2, removes singles as
        well.
        """

        if self.releases.empty:
            return

        # print(
        #     self.releases,
        #     self.releases.columns,
        # )

        if "rateable" not in self.releases:
            self.releases["rateable"] = self.releases.apply(
                lambda x: rate.is_rateable(x, exclude=exclude),
                axis=1,
            )
            self.releases = self.releases[self.releases.rateable.eq(True)]

            if self.releases.empty:
                print("No releases left! (format)")

    def filter_by_role(
        self,
        roles: list[str],
    ):
        """Typical roles are (in order of decreasing importance):

        "Main", "Producer", "Appearance", "TrackAppearance", "Co-producer",
        "Mixed by", "Remix", "UnofficialRelease",

        Almost always, 'Main'

        Roles like 'Visual' are only in full release (requires extra get).
        """

        if self.releases.empty:
            return

        if "role" in self.releases:
            # print(self.releases)
            self.releases = self.releases[self.releases.role.isin(roles)]

            if self.releases.empty:
                print("No releases left! (role)")

    # def jump_to_pos(self):
    #     ...

    # def rate(self):
    #     discogs.rate.rate_releases_of_artist(
    #         [dc.d_get(r["id"]) for r in self.releases]
    #     )
    #     lprint(self.releases)
    #     discogs.rate.rate_releases_of_artist(self.releases)

    def get_releases(self) -> dict[str, Any]:
        """Fetch artist releases, starting on page 1 by default."""
        results = dc.d_get(
            (
                f"/artists/{self.a_id}/releases?sort=year"
                f"&per_page={self.per_page}&page={self.page}"
            ),
            verbose=True,
        )
        return results

    def get_credits(self):
        """Very inefficient (need GET for every single release), not well
        tested"""
        self.filter_by_role(["Appearance", "TrackAppearance"])

        # for col in self.releases:
        #     print(self.releases[col])

        rids = self.releases[["type", "main_release", "id"]]
        print(rids)

        tracks = []
        for _, row in rids.iterrows():
            # note: this clunky field logic is also used in rate_all
            if row.type == "master":
                _id = int(row.main_release)
            else:
                _id = row.id

            rel = dc.d_get(_id)

            for track in rel["tracklist"]:
                if "extraartists" not in track:
                    continue
                # lprint(track)
                artist_ids = [x["id"] for x in track["extraartists"]]
                if int(self.a_id) in artist_ids:
                    tracks.append(
                        {
                            "artist": dc.clean_artist(rel["artists_sort"]),
                            "title": track["title"],
                        }
                    )
            time.sleep(2)

        return pd.DataFrame(tracks)

    def add_next_page(self):
        """Append next n releases to the current list of releases, where n =
        items per page. Position is unchanged. Does nothing if all releases
        have been fetched.
        """
        if len(self.releases) < self.total:
            self.page += 1
            new_pg = pd.DataFrame(self.get_releases()["releases"]).fillna(0)
            self.releases = pd.concat([self.releases, new_pg], sort=False)
            self.position = self.position // self.per_page + self.per_page

    # def add_prev_page(self):
    #     if self.page > 1:
    #         self.page -= 1
    #         self.releases = self.get_releases()["releases"] + self.releases
    #         # self.position -= self.per_page

    def navigate(self, mod: int):
        """Increment/decrement position in current list of releases by <mod>."""
        # TODO: 1st check position vs len(self), then vs len(self.releases)
        # print(self.position, mod, len(self))
        # raise ValueError
        new_position = self.position + mod
        if 0 <= new_position <= len(self):
            self.position = new_position

        # if self.position + mod > len(self) or self.position + mod < 0:
        #     print("end of list")
        # else:
        #     self.position += mod
        #     # print(self.position, mod)
        #     # raise ValueError

    def show_release(self):
        """Format artist, album, and tracklist (df), cache to df"""
        os.system("clear")
        print(self.releases)
        rel_str = dc.release_as_str(self.releases[self.position]["id"])
        # https://stackoverflow.com/a/45746617
        # iloc is faster than loc
        # self.releases.loc[self.releases[self.position], "rel_str"] = rel_str
        self.releases.iloc[
            self.position,
            self.releases.get_loc("rel_str"),
        ] = rel_str
        print(self.releases.rel_str)
        raise ValueError

    def browse(self):
        """Basic CLI interface"""
        self.show_release()
        while action := readchar.readchar():
            match action:
                case "j":
                    self.navigate(1)
                    os.system("clear")
                    self.show_release()
                case "k":
                    self.navigate(-1)
                case "r":
                    ...  # rate
                case "R":
                    ...  # random page
                case "x":
                    sys.exit()

    def rate_all(
        self,
        rerate: bool = False,
        skip_wanted: bool = False,
    ) -> None:
        """Rate Discogs releases of an artist, in chronological order.

        Filtering is done in two stages. The first stage does not require any
        GET requests, and uses all the information available in the releases
        listed under the artist -- these entries have less information than an
        actual release (for instance, the 'formats' field is not available).

        The second stage of filtering then uses a GET request to obtain data
        for the release.

        Args:
            releases: releases listed under artist (not full releases)
            rerate: [TODO:description]
            skip_wanted: [TODO:description]
        """

        self.filter_by_role(["Main"])
        # print(len(self))
        self.filter_by_format()
        # print(len(self))

        if self.releases.empty:
            print("df was emptied")
            return

        # generally for Label only?
        self.releases = self.releases[self.releases.year != 0]

        if not rerate:
            local_df = pd.read_csv(dc.DISCOGS_CSV)
            self.filter_from_df(local_df)

        if skip_wanted:
            # get is only feasible if wantlist small, otherwise write it to file
            wants = dc.d_get(f"/users/{dc.USERNAME}/wants")
            wants_df: list[dict] = [r["basic_information"] for r in wants["wants"]]
            self.filter_from_df(wants_df)

        # lprint(all_artist_release_ids, num_rated)

        # if len(release_ids) == num_rated:
        #     print("All releases rated")
        #     return

        # print(
        #     f"{len(self.releases)} releases found, {num_rated} rated locally "
        #     f"({ 100 * num_rated // len(self.releases) }%)"
        # )

        # usually desired for classical, because of large discographies, and
        # because many old releases (< 1960) are unfindable
        # but beware, as this may quickly lead to many skips and thus 429, e.g.
        # Morning Musume (101 releases)

        require_correct_data = len(self.releases) > 250
        if require_correct_data:
            eprint("Restricting to data quality == Correct")

        for _, row in self.releases.iterrows():
            if "type" in row and row.type == "master":
                _id = int(row.main_release)
            else:
                _id = row.id

            rel = dc.d_get(str(_id))

            if require_correct_data and rel["data_quality"] != "Correct":
                continue

            # must be done inside loop, because now full release data is parsed
            if not rate.is_rateable(pd.Series(rel), exclude=1):
                continue

            if rate.rate_release(rel) < 0:
                break


# }}}


class Label(Artist):
    """Inherits most attributes and methods from Artist, except for
    get_name()"""

    def __init__(self, l_id):
        # note: the attrib is still called a_id
        Artist.__init__(self, l_id)

    def get_releases(self) -> dict[str, Any]:
        results = dc.d_get(
            (
                f"/labels/{self.a_id}/releases?sort=year"
                f"&per_page={self.per_page}&page={self.page}"
            ),
            verbose=True,
        )
        return results

    def get_name(self) -> str:
        """the hack for Artist.get_name() doesn't work here"""
        return dc.d_get(str(self.releases.id[0]))["labels"][0]["name"]


def get_transliterations(rel: dict) -> dict[str, list[str]]:
    """Append transliteration (in parentheses) to artist name for ease of
    reading/indexing.

    Usually followed by `apply_transliterations`.

    Discogs-approved transliterations are tried first:
        `{ 'artist1': 'artist1 (abc)', ... }`

    If 'artistX' has no transliterations, it is excluded from the dict.

    Warning: dict keys are lowercase
    """

    artists: list[dict] = rel["artists"]
    # print(artists_dict)

    try:
        # corner case: non-ascii in artist track credits only
        # https://www.discogs.com/release/892711
        artists += [t["artists"][0] for t in rel["tracklist"]]
    except KeyError:
        pass

    transliterations: dict[str, list[str]] = {}

    for art in artists:
        # print(artist_dict)
        native = dc.clean_artist(art["name"])
        if is_ascii(native):
            transliterations[native.lower()] = [native]
            continue

        art_info = dc.d_get(f"/artists/{str(art['id'])}")

        if "namevariations" not in art_info:
            transliterations[native.lower()] = []
            continue

        # print(pd.Series(artist))
        # raise ValueError
        transliterations[native.lower()] = [
            dc.clean_artist(name)
            for name in art_info["namevariations"]
            if name.isascii()
            # number of words must match (Nechaev); difficult since this
            # only applies to 'latin' scripts
            # and len(x.split()) == len(a["name"].split())
        ]

        # TODO: non-ascii composer remains in performers
        # TODO: titlecase exception for transliteration (e.g. WJSN)

    return transliterations


def get_artist_id(
    artist_name: str,
    check_coll: bool = True,
) -> int:
    """Attempt to get an artist's Discogs id from only its name.

    The default strategy is to match names first (partially), then check the
    user's collection (this approach is, in itself, somewhat unreliable).

    Even if there is exactly one name match, it is never automatically returned
    until the collection is checked. Otherwise, there is a high risk of
    returning a false positive.

    Situations requiring manual resolution typically involve:
        - multiple name matches, but >1 in collection
        - multiple name matches, none in collection

    Additionally, some artist names are invalid, and will always be rejected by
    search, e.g. 'classical', 'gospel', 'rock', 'flo', and many 2-letter names.
    A CLI workaround for this might not be possible.

    Args:
        artist: artist name (not id)

    Returns:
        artist id
    """

    def get_id_and_title(data):
        # description field may not always be present
        # [data.columns.intersection(["id", "title", "description"])]
        return data[["id", "title"]].reset_index(drop=True)

    search_url = f"/database/search?q={artist_name}&type=artist"

    try:
        data = pd.DataFrame(dc.d_get(search_url, verbose=True)["results"])
        print(data)
    except KeyError:
        eprint("Invalid search term:", artist_name)
        return 0

    # unidecode(art["title"].lower())

    # print(data)
    # raise ValueError

    # assert "user_data" in data, data
    # usually 2 artists credited together e.g. 'N + Ehnahre'
    if data.empty:
        if ", " in artist_name:
            if "id" not in data.columns or artist_name not in data.title.iloc[0]:
                artist = artist_name.split(", ")[0]
                return get_artist_id(artist)

        else:
            return 0

    # exact_matches = data[data.title.str.lower() == artist_name.lower()]
    # print(exact_matches)
    if not check_coll:
        return select_from_list(get_id_and_title(data), "Artist id").id

    # note: collection check is not unit-testable
    # for testing, maybe allow an optional arg for list[int] of ids?
    # note: this can fail? (all False) -- possibly an issue on discogs' side
    data["in_col"] = data.user_data.apply(lambda x: x["in_collection"])

    partial_matches = data[
        data.title.str.contains(artist_name, regex=False, case=False)
    ]

    if partial_matches.empty:
        eprint("No name matches", artist_name)

    # else:
    #     print("Name matches")
    #     print(partial_matches[["id", "title"]])

    # now intersection 'all artists' and 'artists in collection'

    # print("Collection matches")
    # print(data[["id", "title", "in_col"]])
    # # raise ValueError

    if not data.in_col.any():
        return select_from_list(get_id_and_title(data), "Artist id").id

    in_coll = data[data.in_col.eq(True)]

    matches_in_coll = pd.merge(
        left=partial_matches[["id", "title"]],
        right=in_coll[["id", "title", "in_col"]],
        how="inner",
        # on="id",  # all columns not specified will be duplicated
    )

    # print("Name matches in collection")
    # print(matches_in_col)

    if len(matches_in_coll) == 1:
        return matches_in_coll.id.iloc[0]

    if not matches_in_coll.empty:
        return select_from_list(
            get_id_and_title(data[data.id.isin(matches_in_coll.id)]),
            "Artist id",
        ).id

    return select_from_list(
        get_id_and_title(data[data.id.isin(in_coll.id)]),
        "Artist id",
    ).id.values[0]


if __name__ == "__main__":
    print(get_artist_id(sys.argv[1]))
