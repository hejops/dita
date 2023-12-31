#!/usr/bin/env python3
"""Module for scraping Bandcamp (since RSS feeds have been discontinued)

"""
# from pprint import pprint
# from typing import Generator
import json
import os
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BC_SUBS_FILE = f"{os.path.expanduser('~')}/.config/newsboat/bandcamp"


def get_album_age(album_url: str) -> int:
    """Parses a Bandcamp date string, returns number of days elapsed since
    then. Can be a negative integer, if release is not yet published.

    Bandcamp date strings are always in the following format:
    'release[ds] July 29, 2022'
    """
    try:
        page = requests.get(album_url, timeout=3)
    except requests.exceptions.ReadTimeout:
        return -1
    soup = BeautifulSoup(page.content, "html.parser")
    album_credits = soup.find(
        "div",
        attrs={"class": "tralbum-credits"},
    )

    if not album_credits:
        raise NotImplementedError

    release_date = [
        line
        for line in album_credits.text.split("\n")
        if line.strip().startswith("release")
    ][0]

    # print(release_date)

    return (
        datetime.now()
        - datetime.strptime(
            release_date.split(maxsplit=1)[1],
            "%B %d, %Y",
        )
    ).days


def get_label_albums(
    label_name: str,
    max_days: int = 7,
) -> list[str]:
    """Retrieve albums on the first page of a Bandcamp label's releases
    published within the last <n> days.
    """
    label_url = f"https://{label_name}.bandcamp.com/music"

    try:
        page = requests.get(label_url, timeout=3)
    except requests.exceptions.ReadTimeout:
        print("timeout:", label_url)
        return []

    soup = BeautifulSoup(page.content, "html.parser")
    albums = []
    for album in soup.find_all(
        "li",
        attrs={
            # different layouts must be accounted for
            "class": [
                "music-grid-item square",
                "music-grid-item square first-four",
                "music-grid-item square first-four featured",
            ],
        },
    ):
        if album.a["href"].startswith("https"):
            # external urls
            url = album.a["href"]
        else:
            url = label_url.removesuffix("/music") + album.a["href"]

        # print(url)

        if 0 < get_album_age(url) <= max_days:
            albums.append(url)
        else:
            break

    return albums


def get_user_subscriptions(username: str) -> list[str]:
    """Retrieve a list of Bandcamp labels followed by a user, with a single
    POST request. Fairly quick.
    """
    # Based on:
    # https://github.com/bembidiona/bandcamp-fan-feed/blob/master/bandcamp-fan-feed.py
    soup = BeautifulSoup(
        requests.get(f"https://bandcamp.com/{username}", timeout=3).content,
        "html.parser",
    )

    user_id = soup.find(type="button")["id"].split("_")[1]

    with requests.Session() as sess:
        sess.get(f"https://bandcamp.com/{username}/following/artists_and_labels")
        # clicks the see more button

    following = sess.post(
        "https://bandcamp.com/api/fancollection/1/following_bands",
        json={
            "fan_id": user_id,
            # HACK: set older_than_token and count to absurdly large integers
            "older_than_token": "9999999999:9999999999",
            "count": 9999,
        },
    )
    followed = json.loads(following.text)

    # yes, 'followeers' is not a typo...
    return [x["url_hints"]["subdomain"].strip() for x in followed["followeers"]]


def get_albums_of_week(username: str) -> list[str]:
    """Get list of URLs of Bandcamp releases published in the past week.

    While we could return some kind of dict - to match columns of rss.py,
    namely: ["title", "author", "feedurl", "url"] - rss needs to extract info
    of url-only bc urls anyway, so... never mind."""

    # with open(BC_SUBS_FILE, "w", encoding="utf-8") as f:
    subs = get_user_subscriptions(username)

    # with open(BC_SUBS_FILE, "r", encoding="utf-8") as f:
    #     labels = f.readlines()

    return [alb for label in subs for alb in get_label_albums(label)]
