#!/usr/bin/env python3
"""Visualise Discogs collection in streamlit as a grid of images

"""
from random import randint
from urllib.parse import quote

import pandas as pd
import streamlit as st
from st_clickable_images import clickable_images

from dita.discogs.collection import Collection
from dita.discogs.core import d_get
from dita.discogs.core import DISCOGS_CSV
from dita.discogs.release import get_release_tracklist

# from discogs.compare import ytmusic
# from discogs.core import d_get

st.set_page_config(layout="wide")

# MIN_RATING = 3
# NUM_ALBUMS = 9
# PAGE = 1
# pg = 1
IMG_WIDTH = 300
MARGIN = IMG_WIDTH // 20

for key in ["page", "pages"]:
    if key not in st.session_state:
        st.session_state[key] = 1

if "per_page" not in st.session_state:
    st.session_state.per_page = 9


def change_page():
    """Callback to change page dynamically, done via a 'dummy' session state
    key. The 'intuitive' way of simply modifying session state using the
    widget's return value does not produce the intended behaviour."""
    # https://stackoverflow.com/a/72183935
    st.session_state.page = st.session_state.new_page


def show_grid(grid: pd.DataFrame):
    """Display a grid of images on left, metadata on (bottom) right.

    Grid is drawn with essentially just CSS, and does a reasonable job for most
    square images.
    """
    with col1:
        clicked = clickable_images(
            paths=grid.img.to_list(),
            # tooltip
            titles=grid.title.to_list(),
            div_style={
                "display": "flex",
                "justify-content": "right",
                "flex-wrap": "wrap",
            },
            img_style={
                "margin": f"{MARGIN}px",
                "height": f"{IMG_WIDTH}px",
                # setting both height and width will lead to unusual stretching
                # "width": f"{IMG_WIDTH}px",
            },
        )
        clicked = max(clicked, 0)  # defaults to -1

    with col2:
        sel: pd.Series = grid.iloc[clicked]

        with st.expander(sel.title, expanded=True):
            st.write(f"https://www.discogs.com/release/{sel.id}")

            # st.write("YouTube")
            # st.write(ytmusic(" ".join([sel.artist, sel.title]))[0])

            spot_url = (
                "https://open.spotify.com/search/"
                + quote(" ".join([sel.artist, sel.title]))
                + "/albums"
            )
            st.markdown(f"[Spotify]({spot_url})")

            col2a, col2b = st.columns((1, 1))
            with col2a:
                st.write(
                    get_release_tracklist(d_get(str(sel.id))).set_index("tracknumber")
                )
            with col2b:
                st.write(sel)
                # filt.iloc[clicked].genre.split(", "),


DF = pd.read_csv(DISCOGS_CSV, index_col=0).drop_duplicates("date_added")
COLL = Collection(DF, drop_imgs=False)

# i don't really like this var tbh
# at this point, it refers to the unfiltered coll
st.session_state["pages"] = len(COLL.filtered) // st.session_state.per_page

col1, col2 = st.columns((2, 1))

# this technically could be in the sidebar, but i don't want one
with col2:
    if st.button("Random page"):
        st.session_state.page = randint(1, st.session_state.pages)
    else:
        PAGE = st.session_state.page

    PAGE = st.number_input(
        "Page",
        value=st.session_state.page,
        step=1,
        min_value=1,
        max_value=st.session_state.pages,
        # help=f"{st.session_state.pages} pages",
        on_change=change_page,
        key="new_page",
    )

    st.session_state.per_page = st.number_input(
        "Albums per page",
        value=9,
        step=1,
    )

    MIN_RATING = "r:" + str(
        st.number_input(
            "Minimum rating",
            value=3,
            step=1,
            min_value=1,
            max_value=5,
        )
    )

    FILTERS = st.text_input(
        "Additional filters",
        help=Collection.filter.__doc__,
    )
    if FILTERS:
        FILTERS = ",".join([MIN_RATING, FILTERS])
    else:
        FILTERS = MIN_RATING

    if st.checkbox("Date"):
        date = st.date_input("Date")
        date_filt = str(date).rsplit("-", maxsplit=1)[0]
        FILTERS += ",date_added:" + date_filt

COLL.filter(FILTERS)

if not st.checkbox("grid", value=True):
    with col1:
        st.write(COLL.filtered.reset_index(drop=True))
    st.stop()

filtered_df = COLL.filtered

if "date_added" in filtered_df:
    filtered_df = filtered_df.sort_values(
        by="date_added",
        ascending=False,
    )

# but now the number of pages is reduced after filter
st.session_state["pages"] = len(COLL.filtered) // st.session_state.per_page

start = (st.session_state.page - 1) * st.session_state.per_page

show_grid(filtered_df[start : start + st.session_state.per_page])
