#!/usr/bin/env python3
"""Streamlit app for track ratings. Especially useful for comparing different
recordings of a classical work.

"""

# from urllib.parse import quote_plus
# import re
import math
import os
import sys
from glob import glob

import pandas as pd
import streamlit as st
import yt_dlp

import dita.discogs.core as dc
from dita.config import PATH
from dita.config import TARGET_DIR
from dita.discogs import release
from dita.discogs.collection import Collection

# import requests

ESCAPES = "".join([chr(char) for char in range(1, 32)])
INTS = set(range(999))
MEAN_COL = "_mean"

YTPL_REGEX = r"OLAK5uy[^\\]+"

YT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/112.0"
    )
}


# def ytmusic(query: str) -> list[str]:
#     # st.write(query)
#     st.write(query)
#     url = f"https://music.youtube.com/search?q={quote_plus(query)}&cbrd=1&ucbcb=1"
#     src = requests.get(
#         url,
#         headers=YT_HEADERS,
#         timeout=3,
#     ).text.translate(ESCAPES)
#     # escape chars (\\x) are annoying
#     return [
#         "https://music.youtube.com/playlist?list=" + pl_id
#         for pl_id in set(re.findall(YTPL_REGEX, src))
#     ]


def ytsearch(
    query: str,
    num_results: int = 1,
) -> None:
    """Search YouTube, embed in page. Not very useful since playlists cannot be
    embedded."""
    # https://github.com/whatdaybob/sonarr_youtubedl/blob/3d3b1d55c6799cc3f317d08639b9a60cd41c64d9/app/sonarr_youtubedl.py#L325

    # with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    query = query.replace("'", "")
    query = "".join(c for c in query if c.isascii())
    query = st.text_input("", value=query)
    query = f"ytsearch{num_results}:'{query}'"

    with yt_dlp.YoutubeDL() as ydl:
        result = ydl.extract_info(query, download=False)

    st.write(result)

    if "entries" in result and (entries := result.get("entries")):
        # for i in range(num_results):
        # st.write(len(entries))

        for entry in entries:
            video_url = entry.get("webpage_url")
            st.video(video_url)

    else:
        st.text("Not found")
        return
        # video_url = result.get("webpage_url")
        # video_url = ""

    # return video_url


# movement databases
# https://github.com/albertms10/bachproject/tree/b801d727bc0e371ac30c20b76d06a22329d6db35/web/assets/database/csv
# https://raw.githubusercontent.com/albertms10/bachproject/b801d727bc0e371ac30c20b76d06a22329d6db35/web/assets/database/csv/25-preludis-fugues.csv
# https://raw.githubusercontent.com/aqhali/bach/0a2d903bf9ebb1758424d14019df84f6d17495ac/compositions.json


def new_work():
    """Add work from collection, typically by using a 'title:XXX' filter, which
    returns a df with columns ['artist', 'r'].

    Movements must be input manually (multiline) at the moment.
    """
    filters = st.text_input(
        "filters",
        help=Collection.filter.__doc__,
        placeholder="title:requiem",
    )

    if not filters:
        sel_perfs = st.text_area("performers").split("\n")
        movts = st.text_area("movements").split("\n")

    else:
        coll = Collection(pd.read_csv(dc.DISCOGS_CSV))
        coll.filter(filters)
        # TODO: editable df with checkbox col
        # st.write(coll.filtered)
        df = coll.filtered.copy()
        df["sel"] = True
        st.data_editor(df)
        st.stop()
        results: list[str] = (
            coll.filtered.drop_duplicates("artist")
            .artist.apply(lambda x: x.split(", ")[0].split()[-1])
            .to_list()
        )
        sel_perfs = st.multiselect(
            "performers",
            results,
            default=results,
        )
        # sel_perfs = [dc.clean_artist(p).split()[-1] for p in sel_perfs]
        # assume copy-pasted discogs tracklist ["1 ...", ...]

        # fetch from a release
        # _id = st.text_input(
        _id = st.selectbox(
            "Get movements from Discogs id",
            options=coll.filtered.id.to_list(),
        )
        if not _id:
            return
        movts = release.get_release_tracklist(dc.d_get(_id)).title.to_list()

    st.write(
        new_df := pd.DataFrame(
            [[None] * len(sel_perfs)] * len(movts),
            index=movts,
            columns=sel_perfs,
        )
    )

    with st.form("new work"):
        fname = st.text_input("filename").replace(" ", "-")

        submitted = st.form_submit_button("Submit")
        if submitted:
            new_df.to_csv(f"{PATH}/clas/{fname}.csv")
            st.info("wrote")
            st.experimental_rerun()


# def add_movts(df) -> pd.DataFrame:
#     works = st.text_area("movements").split("\n")
#     # df = pd.DataFrame([None] * len(works), index=works)
#     for work in works:
#         # df = pd.concat([df, pd.Series(name=w)])
#         df.append(pd.Series(name=work))
#     # df.to_csv(TRACK_CSV)
#     return df


# def add_performers():
#     filters = st.text_input("filters")
#     results: list[str] = (
#         Collection(pd.read_csv(dc.DISCOGS_CSV))
#         .filter(filters)
#         .drop_duplicates("artist")
#         .artist.to_list()
#     )
#     # st.write(results)
#     names = st.multiselect(
#         "performers",
#         results,
#         default=results,
#         help=Collection.filter.__doc__,
#     )
#     st.write(names)
#     for name in names:
#         DF[name.split()[-1]] = 0
#     st.write(DF)
#     if st.button("save"):
#         DF.to_csv(TRACK_CSV)
#         st.stop()


def show_df_with_mean(df):
    """Show big df on left, means (single-column) on right"""
    # st.write(df.dtypes)

    # convert categories back to ints, for mean calculation
    df = df.astype({perf: int for perf in PERFS})

    col1, col2 = st.columns((5, 3))
    with col1:
        st.write(df)
    with col2:
        mean = df.mean()
        means = pd.DataFrame(round(mean, 2), columns=[MEAN_COL])
        means["pts"] = means[MEAN_COL].apply(mean_to_points)
        st.write(means)


def mean_to_points(
    mean: float,
    peak: float = 2.7,
    step: int = 6,
) -> int | None:
    """Scale float with range [0, 3] to an int [1, 5]

    2.70 / 2.25 / 1.80 / 1.35 / 0.90

    Some (entirely personal) benchmarks:
        - 5 = Asperen WTC1
        - 2 = Barenboim Goldberg

    For pop (or generally non-classical) albums with number of tracks n > 6,
    the mean may be multiplied by 1.02^(n-6).

    Args:
        mean: [TODO:description]
        peak: minimum float value required to yield 5
        step: at intervals of (peak/step) away from peak, 1 will be subtracted
        from result

    Returns:
        int between 1 and 5 (inclusive), or None if invalid mean provided
    """
    # st.write(mean)
    if (mean is None) or math.isnan(mean):
        return None

    peak = 2.7
    # step = peak / step  # .45
    grad = step / peak
    y_int = 5 - (grad * peak)

    pts = (grad * mean) + y_int
    if pts < 1:
        return 1
    return int(pts)


def sidebar_actions(df):
    """
    - Add performer
    - Sort performers
    - Add movements
    - New work
    """

    with st.form("new perf"):
        new_perf = st.text_input("Add performer")
        if st.form_submit_button():
            print(df)
            df[new_perf] = None
            df.to_csv(TRACK_CSV)
            st.experimental_rerun()

    if st.button("Sort performers"):
        # https://stackoverflow.com/a/17712440
        df = df.reindex(
            df.mean().sort_values(ascending=False).index,
            axis=1,
        )
        df.to_csv(TRACK_CSV)

    if st.button("Add movements"):
        movts = st.text_area("movements").split("\n")
        if all(movts):
            for movt in movts:
                # st.write(w)
                # df = pd.concat([df, pd.Series(name=w)])
                df = df.append(pd.Series(name=movt))
            df.to_csv(TRACK_CSV)

    # with st.expander("New work"):
    #     new_work()

    # embed = st.checkbox("Embed (slow)", value=False)
    # if embed:
    #     search = st.number_input(
    #         "Search",
    #         min_value=0,
    #         max_value=3,
    #         value=2,
    #     )


def checkbox_grid(
    items: pd.Series,
    ncols: int = 3,
    # func = lambda item: DF[item].isna().values.all(),
) -> list[str]:
    """Create grid of checkboxes, to conserve vertical space"""
    ncols = min(ncols, 4)

    sel = []
    cols = st.columns((1) * ncols)

    for item in items:
        with cols[ncols * items.get_loc(item) // len(items)]:
            if not DF[item].isna().values.all():
                if st.checkbox(f"~{item}~", key=item):
                    sel.append(item)
            elif st.checkbox(item, key=item):
                sel.append(item)

    return sel


def from_str(
    perf: str,
    df,
):
    ratings = st.text_input(perf)
    if not ratings:
        return df

    vals = [int(x) for x in ratings if x in "0123"]
    # st.write(len(selected))

    if len(vals) == len(df):
        df[perf] = vals
    else:
        df.loc[:, perf] = vals + [None] * (len(df) - len(vals))

    return df


if __name__ == "__main__":
    SINGLE = bool(len(sys.argv) > 1)

    st.set_page_config(layout="wide")

    # st.write(sys.argv)
    # st.stop()

    # if SINGLE:	# messes with AppTest
    if 0:
        RELPATH = " ".join(sys.argv[1:])  # idk why streamlit cli discards quotes
        abspath = os.path.join(TARGET_DIR, RELPATH)
        # st.write(abspath, os.path.isdir(abspath))
        audio_files = sorted(
            x
            # for x in os.listdir(relpath)
            for x in os.listdir(abspath)
            if x.endswith("mp3")
        )
        PERFS = [RELPATH]
        DF = pd.DataFrame(
            [[None] * len([RELPATH])] * len(audio_files),
            index=audio_files,
            columns=PERFS,
        )
        TRACK_CSV = None

    else:
        with st.expander("New work"):
            new_work()

        # selectbox behaviour is subpar (doesn't work properly in Firefox)
        TRACK_CSV = st.sidebar.radio(
            "Select file",
            sorted(glob(f"{PATH}/clas/*csv")),
            # os.listdir(f"{PATH}/clas"),
            format_func=os.path.basename,
            # key="foo",
        )
        DF: pd.DataFrame = pd.read_csv(
            TRACK_CSV,
            index_col=0,
            comment="#",
            # note: adding comments is useless since they get wiped on to_csv
        )

        with st.sidebar:
            st.write("---")
            sidebar_actions(DF)

        with st.expander("Performers"):
            PERFS: list[str] = checkbox_grid(DF.columns)

        if not PERFS:
            show_df_with_mean(DF)
            st.stop()

        # probably should not be triggered anymore
        if len(DF.columns) == 0:
            st.warning("No performers listed!")
            # add_performers()

    selected: pd.DataFrame = DF[PERFS]

    # https://docs.streamlit.io/library/advanced-features/dataframes?ref=blog.streamlit.io#categorical-columns-selectboxes
    for perf in PERFS:
        selected.loc[:, perf] = (
            selected[perf].astype("category").cat.set_categories([0, 1, 2, 3])
        )
        selected = from_str(perf, selected)

    # .cat is Series-only
    # selected[PERFS].astype("category").cat.set_categories([0, 1, 2, 3])

    edited_df = st.data_editor(
        selected,
        num_rows="dynamic",  # disables column sorting
    )

    # replace columns
    # https://stackoverflow.com/a/58766782
    df2 = DF.assign(**edited_df.to_dict(orient="series"))

    if all(x >= 0 for perf in PERFS for x in df2[perf].to_list()):
        show_df_with_mean(df2)

    # if not single:
    #     df2.to_csv(TRACK_CSV)

    # assert df.all() == df2.all()

    # st.write(df2)

    if TRACK_CSV:
        with st.sidebar:
            st.markdown("---")
            if st.button("Save"):
                df2.to_csv(TRACK_CSV)
