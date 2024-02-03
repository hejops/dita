#!/usr/bin/env python3
"""Module for rating Discogs releases on the command-line.

"""
# from random import choice
# import shlex
import html
import json
import os
import sys
import time

import pandas as pd
import requests

import dita.discogs.core as dc
from dita.config import TARGET_DIR
from dita.discogs import artist
from dita.discogs.core import search_with_relpath
from dita.tagfuncs import lprint
from dita.tagfuncs import shuote

# from artist import Artist
# from tagfuncs import PATH
# from tagfuncs import eprint


# can be config
IGNORED_FORMATS = {
    "Advance",
    "Blu-ray",
    "Box Set",
    "CD-ROM",
    "Comp",  # partial
    "Compilation",  # ignoring may not always be desired -- https://www.discogs.com/release/13421485
    "DVD",  # https://www.discogs.com/artist/299089-Ami-Suzuki
    "DVD-Video",
    "Flexi",
    "MPEG-4 Video",
    "Maxi-Single",
    "Mini",
    "Minimax",
    "Mixed",
    "NTSC",
    "Promo",
    "Repress",
    "Sampler",
    "Shellac",  # usually so old that nobody really has them anyway
    "Single",
    "Transcription",
    "VCD",
    "VHS",
    # "AAC",  # ambiguous
    # "Enhanced",
    # "File",
    # "Maxi",
    # "Reissue",	# generally better not to ignore
    # "Remastered",
    # "Single Sided",
    # "Unofficial Release",
    # '10"',
    # '12"',  # highly ambiguous
    # '7"',
    '5"',
}


def is_rateable(
    release: pd.Series,
    exclude: int = 0,
    require_date: bool = True,
) -> bool:
    """[TODO:summary]

    Filter by a Discogs release by format, primarily for rating. No GETs are
    ever done.

    Args:
        release: can be a "partial" release (from artist search), or a full
            release
        exclude: 1 excludes compilations, 2 excludes compilations and singles
        require_date: [TODO:description]

    Returns:
        bool: True
    """
    # print(release)
    # raise ValueError

    # print(exclude)
    # lprint(release)

    # title = release["title"]

    # allowed_formats = {"Album", "EP"}

    if exclude >= 1:
        IGNORED_FORMATS.add("Comp")
    if exclude >= 2:
        # os.system("notify-send fasjdklas")
        IGNORED_FORMATS.add("Single")

    # print(release)
    # raise ValueError

    # print(
    #     release.get("format"),
    #     release.get("formats"),
    # )

    # from artist search
    if "format" in release and release.format:
        format_names = release["format"].split(", ")

        if set(format_names).intersection(IGNORED_FORMATS):
            # lprint(release["title"], "formats (partial)", format_names)
            # if format_names[0] in IGNORED_FORMATS:
            return False

    # full release
    elif "formats" in release:
        # print(release["formats"])
        # raise ValueError
        format_names = {f["name"] for f in release["formats"]}
        # lprint(format_names)

        if len(format_names) == 1 and format_names.intersection(IGNORED_FORMATS):
            # lprint(release["title"], "formats (full)", format_names)
            return False

        # desc = {f.get("descriptions")[0] for f in release["formats"]}
        # if desc.intersection(IGNORED_FORMATS):

        if (
            "descriptions" in release["formats"][0]
            and release["formats"][0]["descriptions"][0] in IGNORED_FORMATS
        ) or (
            release["formats"][0]["name"] == "File"
            and release["formats"][0]["qty"] == "1"
        ):
            return False

        # lprint("formats (desc)", desc)
        for fmt in release.get("formats"):
            if fmt.get("descriptions") and set(fmt.get("descriptions")).intersection(
                IGNORED_FORMATS
            ):
                # lprint(f)
                # if set(f.get("descriptions")).intersection(IGNORED_FORMATS):
                return False

    if require_date and "year" not in release:
        return False

    return True


def rate_release(
    release: dict,
    rating: str = "",
    rerate: bool = False,
) -> int:
    """Rate discogs release on a scale of 0-5

    3 API calls are required:
        1. GET current rating of album, if any
        2. PUT rating
        3. POST add album to collection

    Local database (csv) reading is an expensive operation and should never be
    done here, as this function is usually looped.

    See also: is_rateable(), which applies only to 'partial' releases (as
    listed under an artist)

    Args:
        release: Discogs release object, obtained from API

        rating: 1-5. By default, rating is supplied via user input. A rating
        can also be specified when migrating from an existing database (with
        ratings).

        rerate: if release already rated, rate it again

    Returns:
        int: 0 if release is not rated. Otherwise, the user-supplied rating.
        Can be used to trigger subsequent actions, e.g. return value of 0 may
        be used to delete a directory.
    """

    def checklib(*args):
        """Require exact match for artist, front match for album. Casefold may
        be implemented."""
        if not TARGET_DIR:
            return
        root = TARGET_DIR
        for i, arg in enumerate(args):
            for chi in os.scandir(root):
                if (i > 0 and chi.name.startswith(arg + " (")) or chi.name == arg:
                    root = chi.path
        if root != TARGET_DIR:
            print(root)

    # lprint(
    #     release,
    #     list(release),
    # )
    # raise ValueError

    if "id" not in release:
        return 0

    release_id: int = release["id"]

    # usually release["data_quality"] == "Needs Major Changes"
    if "title" not in release:
        return 0

    print(
        release["year"],
        art := ", ".join([dc.clean_artist(x["name"]) for x in release["artists"]]),
        album := release["title"],
        sep=" :: ",
    )

    os.system("echo " + shuote(art, album) + " | xclip")

    # os.system(f"< $HOME/.config/mpv/library grep -i '{artist}/{album}'")
    # os.system(f"checklib '{art}' '{album}'")
    checklib(art, album)

    print(release["uri"].split("-")[0])

    # ...the current rating is still checked, because the csv is not
    # immediately updated
    # https://www.discogs.com/developers#page:database,header:database-release-rating-by-user-put
    url = dc.API_PREFIX + f"/releases/{release_id}/rating/{dc.USERNAME}"
    current_rating: str = json.loads(
        requests.get(
            url=url,
            headers=dc.HEADERS,
            timeout=3,
        ).content
    )["rating"]
    if current_rating:
        print(f"Already rated ({current_rating})")
        if not rerate:
            return int(current_rating)

    if not rating:
        rating = input("Rating: ")

        if rating == "x":
            # sys.exit(0)
            return -1
        if not rating or rating not in "12345":
            # print("Not rating")
            return 0

    data = json.dumps(  # dict -> json str
        {
            "username": dc.USERNAME,
            "release_id": release_id,
            "rating": int(rating),
        }
    )
    dc.HEADERS["Content-Type"] = "application/json"
    # put is idempotent
    response = requests.put(
        url=url,
        data=data,
        headers=dc.HEADERS,
        timeout=3,
    )
    lprint(json.loads(response.content))
    print()

    if current_rating:
        return int(current_rating)

    # add to collection -- must be done last to prevent duplicate additions
    # (post is not idempotent)
    # https://www.discogs.com/developers#page:user-collection,header:user-collection-add-to-collection-folder
    response = json.loads(
        requests.post(
            url=dc.API_PREFIX
            + f"/users/{dc.USERNAME}/collection/folders/1/releases/{release_id}",
            headers=dc.HEADERS,
            timeout=3,
        ).content
    )

    return int(rating)


def import_rym_ratings(rym_csv: str) -> None:
    """'Import' rateyourmusic.com ratings into Discogs.

    The csv file can be obtained with the 'Export your data' button, or at:

    https://rateyourmusic.com/user_albums_export?album_list_id=[ID]&noreview

    (where ID is displayed on a user's profile beside the username)
    """
    df = pd.read_csv(
        rym_csv,
        usecols=["Last Name", " Last Name localized", "Title", "Rating"],
    )
    print(df)

    for _, row in df.iterrows():
        if row["_1"]:
            art: str = row["_1"]
        else:
            art = row["_2"]

        # cells starting with a numeric are floats for some reason
        art = html.unescape(str(art)).strip()
        if not art:
            continue

        album = html.unescape(str(row.Title)).strip()

        release = dc.search_release(
            artist=art,
            album=album,
            # expected_tracks=0,
            primary=True,
            interactive=False,
        )[0]
        time.sleep(2)

        if not release:
            continue

        if row.Rating < 6:
            rating = "1"
        elif row.Rating < 8:
            rating = "2"
        else:
            rating = str(row.Rating - 5)
        rate_release(release, rating=rating)
        time.sleep(2)


# def get_release_rating(release: dict) -> int:
#     if "main_release" in release:
#         release_id = release["main_release"]
#     else:
#         release_id = release["id"]
#     url = dc.API_PREFIX + f"/releases/{release_id}/rating/{dc.USERNAME}"
#     # curr = requests.get(url=url, headers=dc.HEADERS)
#     curr = dc.d_get(url)
#     eprint(json.loads(curr.content))
#     return json.loads(curr.content)["rating"]


def rate_from_str(_str: str):
    _artist, album, rating = _str.split(",")
    rating = rating.strip("-+")
    rel = search_with_relpath(f"{_artist}/{album}")
    rate_release(rel, rating=rating, rerate=True)


if __name__ == "__main__":
    # discogs.artist.Label(195387).rate_all()
    # raise ValueError

    # all main commands must involve rating in some way

    # discogs.artist.Artist(1158911).rate_all()
    # raise Exception

    if "/release/" in sys.argv[1]:
        rate_release(dc.d_get(dc.get_id_from_url(sys.argv[1])))
    elif "/artist/" in sys.argv[1]:
        artist.Artist(dc.get_id_from_url(sys.argv[1])).rate_all()
    elif "/label/" in sys.argv[1]:
        artist.Label(dc.get_id_from_url(sys.argv[1])).rate_all()
    elif "," in sys.argv[1]:
        rate_from_str(sys.argv[1])
    elif os.path.isfile(sys.argv[1]):
        with open(sys.argv[1], "r") as f:
            for l in f.readlines():
                if "," not in l:
                    break
                rate_from_str(l.strip())

    else:
        if sys.argv[1].isnumeric():
            A_ID = int(sys.argv[1])
        else:  # artist name
            A_ID = artist.get_artist_id(
                " ".join(sys.argv[1:]),
                check_coll=False,
            )

        # rate_releases_of_artist(filter_by_role(discogs.artist.Artist(aid).releases))
        artist.Artist(A_ID).rate_all()
