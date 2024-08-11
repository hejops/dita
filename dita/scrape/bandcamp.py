#!/usr/bin/env python3
"""Module for scraping Bandcamp (since RSS feeds have been discontinued)"""
import json
import math
import os
from datetime import datetime
from time import sleep
from typing import Iterator

import requests
import tqdm
from bs4 import BeautifulSoup
from cloudscraper import CloudScraper

BC_SUBS_FILE = f"{os.path.expanduser('~')}/.config/newsboat/bandcamp"
SCRAPER = CloudScraper()


def get_album_age(album_url: str) -> int:
    """Parses a Bandcamp date string, returns number of days elapsed since
    then. Can be a negative integer, if release is not yet published.

    Bandcamp date strings are always in the following format:
    'release[ds] July 29, 2022'
    """
    sleep(1)
    try:
        page = SCRAPER.get(album_url, timeout=3)
    except requests.exceptions.ReadTimeout:
        return -1
    except requests.exceptions.ConnectionError:
        print("?", album_url)
        return -1
    soup = BeautifulSoup(page.content, "html.parser")
    album_credits = soup.find(
        "div",
        attrs={"class": "tralbum-credits"},
    )

    if not album_credits:
        print("error", album_url)
        return -1
        # raise NotImplementedError

    release_date = [
        line
        for line in album_credits.text.split("\n")
        if line.strip().startswith("release")
    ][0]

    days = (
        datetime.now()
        - datetime.strptime(
            release_date.split(maxsplit=1)[1],
            "%B %d, %Y",
        )
    ).days

    # print(album_url, release_date, days)
    return days


def get_label_albums(
    label_name: str,
    n: int = 7,
    verbose: bool = False,
) -> Iterator[str] | None:
    """Retrieve albums on the first page of a Bandcamp label's releases
    published within the last `n` days.
    """
    label_url = f"https://{label_name}.bandcamp.com/music"

    try:
        page = SCRAPER.get(label_url, timeout=3)
    except (
        requests.exceptions.ReadTimeout,
        requests.exceptions.ConnectionError,
    ):
        print("timeout:", label_url)
        return

    soup = BeautifulSoup(page.content, "html.parser")
    albums = soup.find_all(
        "li",
        attrs={
            # different layouts must be accounted for
            # assume albums are sorted newest first
            "class": [
                "music-grid-item square",
                "music-grid-item square first-four",
                "music-grid-item square first-four featured",
            ],
        },
    )
    # print(label_url, len(albums), "albums")
    for i, album in enumerate(albums):
        if album.a["href"].startswith("https"):
            url = album.a["href"]  # external urls
        else:
            url = label_url.removesuffix("/music") + album.a["href"]

        if "/album/" not in url:
            continue
        if not url.startswith("http"):
            print("?", url, label_url)
            continue
        age = get_album_age(url)
        if age < 0:
            continue

        if age > n:
            # TODO: albums may not be displayed in chronological order!
            # (sentientruin)
            if i == 0:
                continue
            if age > 1000:
                print("!" * (int(math.log2(age))), label_url)
            break
        yield url.partition("?")[0]


def get_user_subscriptions(username: str) -> list[str]:
    """Retrieve a list of Bandcamp labels followed by `username`. Uses a single
    `POST` request, which is required because only the first 45 labels are
    returned in the HTML response. Fairly quick.

    https://michaelherger.github.io/Bandcamp-API
    https://bandcamp.com/developer/account
    """
    # https://github.com/bembidiona/bandcamp-fan-feed/blob/master/bandcamp-fan-feed.py

    url = f"https://bandcamp.com/{username}"
    # print(url)
    soup = BeautifulSoup(
        SCRAPER.get(url, timeout=3).content,
        "html.parser",
    )
    assert "403 Forbidden" not in soup.text, soup
    # TODO: figure out what causes this
    assert (
        len(soup.find_all("a", {"class": "fan-username"})) == 45
    ), "cloudscraper failed to scrape correctly"

    # API calls require fan_id, which is not exposed by the API
    user_id = soup.find(type="button")["id"].split("_")[1]

    # # https://stackoverflow.com/a/64419449
    # user_id = json.loads(soup.find(id="pagedata")["data-blob"])["fan_data"]["fan_id"]

    # with requests.Session() as sess:
    # # click the see more button
    # sess.get(f"https://bandcamp.com/{username}/following/artists_and_labels")

    # curl 'https://bandcamp.com/api/fancollection/1/following_bands' --data-raw '{"fan_id":123,"older_than_token":"99999999999:0","count":99999999999}'

    following = SCRAPER.post(
        "https://bandcamp.com/api/fancollection/1/following_bands",
        json={
            "fan_id": user_id,
            # HACK: set older_than_token and count to absurdly large integers
            # (we just need current epoch)
            "older_than_token": "9999999999:9999999999",
            "count": 9999,
        },
    )

    # yes, 'followeers' is not a typo...
    following = json.loads(following.text)["followeers"]

    foo = sorted(x["url_hints"]["subdomain"].strip() for x in following)
    # print(foo, len(foo))
    return foo


def get_albums_of_week(username: str) -> set[str]:
    """Get list of URLs of Bandcamp releases published in the past week"""
    # 8 min / 286

    labels = get_user_subscriptions(username)

    albums = []
    for label in labels:  # tqdm.tqdm(labels):
        albums += list(get_label_albums(label, verbose=True))
    return set(albums)
