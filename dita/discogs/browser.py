#!/usr/bin/env python3
"""
Proof-of-concept curses UI for browsing any list of dicts obtained from
Discogs. Never left the proof-of-concept stage because I'm not particularly
interested in it.

- artist releases
- collection releases
- label releases
- master versions
- search results releases

Support for non-Discogs-based data is not planned at the moment.

https://docs.python.org/3/library/curses.html

"""
# from pprint import pprint  # , pformat
import curses
import sys

import pandas as pd

from dita.discogs.collection import d_get
from dita.discogs.release import get_release_tracklist

# from tag.core import CONFIG


MAX_ROWS = 50
LR_GAP = 3


class Browser:
    """Browser object for displaying two sets of data in left/right layout. The
    df must contain the data necessary to determine the contents of the right
    panel. Calls a curses wrapper.

    Not suitable for short terminals; in such cases, a top/bottom layout might
    be preferred.

    Not responsible for knowing screen/panel dimensions; these must be passed
    externally.
    """

    def __init__(
        self,
        df,
        left_indexer: str,  # column to use for indexing the df
    ):
        self.df = df
        # extra df column for caching the right panel's df.to_string
        self.df["summary"] = ""

        self.l_idx = left_indexer

        main_scr = curses.initscr()

        curses.curs_set(0)

        self.height, self.width = main_scr.getmaxyx()

        self.pos = 0

        # left panel is mostly static, so width can be calculated on init
        # this needs to be recalc'd on page change
        self.l_width = self.get_l_width() + LR_GAP
        self.r_width = self.width - self.l_width

        # self.l_panel = self.left_panel()
        # self.r_panel = self.right_panel()

    def __len__(self):
        return len(self.df)

    # def __str__(self):
    #     return pformat(self.results)

    def get_l_width(self) -> int:
        """Calculate width of left panel, via width of the longest line in the
        df subset.
        """
        return max(
            len(line)
            for line in self.df[[self.l_idx, "title"]][: self.height]
            .to_string()
            .split("\n")
        )

    # left/right_panel may need to be passed into Browser as functions
    def left_panel(self) -> str:
        """Get str repr of columns of specific columns in primary df. Mostly
        static (aside from cursor); only needs to be recalc'd when scrolling
        beyond the current row subset (not implemented yet)."""

        # retrieve the subset of rows before truncating the actual string
        df = self.df[[self.l_idx, "title"]][: self.height]
        # return df.to_string()
        return self.truncate(
            df.to_string(),
            panel_width=self.l_width,
            cursor_pos=self.pos,
        )

    def right_panel(self) -> str:
        """Secondary df is dynamically generated (i.e. GET) with every user
        interaction. Resulting str repr (non-scrollable) is cached to df to
        avoid repeated GETs.

        Note that the secondary df is actually discarded; this may change in
        future, if we want to return the secondary df (instead of just selected
        row)."""

        if self.df.iloc[self.pos].summary:
            return self.df.iloc[self.pos].summary

        curr_id = self.df.iloc[self.pos].id
        body = get_release_tracklist(d_get(curr_id)).to_string(index=False)

        # append id to top of string, other data can be added here
        body = "\n".join([str(curr_id), body])

        summary = self.truncate(
            text=body,
            panel_width=self.r_width,
            pad=-3,
        )

        self.df.at[self.pos, "summary"] = summary
        return summary

    def accept(self) -> pd.DataFrame:
        """Returns the df of the selected row. Can return a Series for easier
        indexing, but df is potentially more useful if another Browser instance
        is to be spawned."""
        # return self.df.iloc[self.pos]
        return self.df.iloc[[self.pos]]  # extra [] to keep as df

    def navigate(self, mod: int):
        """Move between items in the left panel."""
        new_pos = self.pos + mod
        if 0 <= new_pos <= len(self):
            self.pos = new_pos

    def truncate(
        self,
        text: str,  # any multiline string
        panel_width: int,  # because panel widths will differ
        cursor_pos: int = -1,
        pad: int = 0,
    ) -> str:
        """Truncate multiline string to fit in the confines of a curses panel"""
        lines = text.split("\n")
        truncated = []

        for i, line in enumerate(lines):
            if i == self.height:
                break

            trunc = line[:panel_width]

            if cursor_pos >= 0:
                # left pad by 2 chars
                if i - 1 == cursor_pos:
                    trunc = "> " + trunc
                else:
                    trunc = "  " + trunc

            # right pad
            # TODO: truncation should be performed at the df column level, not
            # string level
            if pad < 0:
                trunc = trunc[:pad]

            truncated.append(trunc)

        return "\n".join(truncated)  # [:panel_height]

    def browse(
        self,
        scr,  # this arg implicitly refers to the wrapper/screen that called the func
        *args,  # the entire Browser object
    ) -> pd.DataFrame:
        """Input loop.

        Browser items (rows of primary df) are displayed/navigated in the left
        panel (e.g. a list of releases). For each row, a secondary df is
        prepared is used to get its str repr, which is then displayed in the
        right panel (note: the secondary df is currently discarded).

        "Selecting" an item ends the loop, returning the selected df row as the
        same type (i.e. df, not Series).

        In future, the secondary df (that was used to generate the right panel)
        may be returned. In any case, the return value must be of the same
        type."""

        # https://docs.python.org/3/library/curses.html#curses.wrapper
        # https://stackoverflow.com/a/74203737
        curb: Browser = args[0]

        # # Clear and refresh the screen for a blank canvas
        # scr.clear()

        scr.refresh()

        # l_width: int = curb.l_width + LR_GAP
        # r_width: int = width - l_width

        # while char := ord(scr.getch()):
        # while char := scr.getkey():
        while True:
            l_panel = curses.newpad(self.height, self.l_width)  # nlines, ncols
            r_panel = curses.newpad(self.height, self.r_width)

            # note: addstr raises error if str exceeds size, so all strs must be truncated 1st
            l_panel.addstr(0, 0, curb.left_panel())

            r_panel.addstr(0, 0, curb.right_panel())

            # _, _, starty, startx, endy, endx
            l_panel.refresh(
                0,
                0,
                0,
                0,
                self.height,
                self.l_width,
            )
            r_panel.refresh(
                0,
                0,
                0,
                self.l_width + 1,
                self.height,
                self.width - 1,
            )

            match scr.getkey():
                case "x":
                    sys.exit()
                case "j":
                    curb.navigate(1)
                case "k":
                    curb.navigate(-1)
                case "l":
                    return curb.accept()


def browse(
    df,
    l_idx,
):
    """A 'meta-function' that constructs a Browser, which 'contains' a screen
    (curses.initscr()). The Browser's dynamic state is drawn on this screen
    with Browser.browse().

    All navigation is performed entirely within a curses.wrapper(), which, by
    the nature of its arguments,

        wrapper(func, *args, **kwds)

    takes the Browser.browse() as func, and the entire Browser as a (kw)arg.

    If this sounds like a lot of abstraction, that's because it probably is.
    """

    browser = Browser(df, l_idx)
    # .wrapper performs all init and exit procedures implicitly, and should
    # always be used
    # https://github.com/enthought/Python-2.7.3/blob/master/Lib/curses/wrapper.py

    # the return value is of func (browser.browse()), not curses.wrapper()!
    return curses.wrapper(browser.browse, browser)


if __name__ == "__main__":
    # # https://github.com/jquast/blessed
    # from blessed import Terminal
    # import sys
    # term = Terminal()
    # with term.location(0, term.height - 1):
    #     print("This is " + term.underline("underlined") + "!", end="")
    # sys.exit()

    # results = d_get(
    #     # f"/users/{CONFIG.get('discogs','username')}/collection/folders/0/releases"
    #     f"/users/{CONFIG.discogs.username}/collection/folders/0/releases",
    # )
    # df = pd.DataFrame([r["basic_information"] for r in results])
    # df.artists = df.artists.apply(lambda x: x[0]["name"])
    # browser = Browser(df, "artists")

    results = d_get("/labels/33085/releases?sort=year&per_page=100&page=1")["releases"]
    print(
        browse(
            pd.DataFrame(results),
            "artist",
        ).resource_url.iloc[0]
    )
