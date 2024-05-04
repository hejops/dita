"""

Module for extracting and downloading files from:
    - YouTube (uses the `newsboat` `urls` file; feed URLs that contain `yt_dl`
      anywhere in the line will selected)
    - Bandcamp (uses API)

Previously, newsboat was relied on for fetching RSS feeds, but this requirement
has been removed.

"""

import io
import os
import re
import xml
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pandas as pd
import requests

from dita.config import CONFIG
from dita.scrape import bandcamp

HOME = os.path.expanduser("~")
NB_DIR = f"{HOME}/.config/newsboat"


def extract_yt() -> set[str]:
    with open(f"{NB_DIR}/urls", encoding="utf8") as f:
        uploaders = [l.split()[0] for l in f.readlines() if "yt_dl" in l]

    def get_xml(url) -> pd.DataFrame:
        try:
            return (
                pd.read_xml(
                    io.BytesIO(requests.get(url, timeout=3).content),
                    parser="etree",
                )
                .dropna(subset=["published", "id"])
                .dropna(axis="columns", how="all")
            )
        except xml.etree.ElementTree.ParseError:
            return pd.DataFrame()

    df = pd.concat(get_xml(url) for url in uploaders)

    df.published = pd.to_datetime(df.published)
    now = datetime.now().replace(tzinfo=timezone(offset=timedelta()))
    df = df[now - df.published < pd.to_timedelta("7 days")]

    df["url"] = df.id.apply(
        lambda x: "https://www.youtube.com/watch?v=" + x.split(":")[-1]
    )

    return set(df.url)


# def extract_bc(nb_df: pd.DataFrame) -> set[str]:
#     def get_urls(text):
#         matches = re.findall(r"https://[^.]+\.bandcamp\.com/album/[-\w]+", text)
#         if matches:
#             return matches
#         return None
#
#     urls = set(nb_df.content.apply(get_urls).dropna().apply(set).explode())
#     print("bc", f"{len(urls)}/{len(nb_df)}")
#     return urls


def extract_discogs() -> set[str]:
    with open(f"{HOME}/dita/dita/scrape/discogs.txt") as f:
        return set(f"ytsearch1:'{l.strip()}'" for l in f.readlines())


def main():
    if os.path.isfile(f"{NB_DIR}/cache.txt"):
        return

    urls = (
        extract_yt()
        # | extract_discogs() # TODO: ytsearch goutubedl
        # | extract_bc(nb_df)
        | bandcamp.get_albums_of_week(CONFIG["bandcamp"]["username"])
    )

    # print(urls)

    with open(f"{NB_DIR}/cache.txt", "w", encoding="utf8") as f:
        f.writelines(a + "\n" for a in urls)


if __name__ == "__main__":
    main()
