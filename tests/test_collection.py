import pandas as pd

from dita.discogs.collection import Collection

test_dict = {
    "title": {
        1731: "Master Of Puppets",
        2931: "Kill 'Em All",
        2933: "Ride The Lightning",
        1732: "...And Justice For All",
        546: "Hardwired...To Self-Destruct",
        3991: "No Life 'Til Leather",
        1728: "St. Anger",
    },
    "year": {
        1731: 1986,
        2931: 1983,
        2933: 1984,
        1732: 1988,
        546: 2016,
        3991: 1982,
        1728: 2003,
    },
    "r": {1731: 3, 2931: 3, 2933: 4, 1732: 2, 546: 1, 3991: 2, 1728: 1},
    "genre": {
        1731: "Speed Metal, Thrash",
        2931: "Speed Metal, Thrash",
        2933: "Speed Metal, Thrash",
        1732: "Heavy Metal, Thrash",
        546: "Heavy Metal, Speed Metal, Thrash",
        3991: "Heavy Metal, Thrash",
        1728: "Heavy Metal",
    },
    "id": {
        1731: 1549636,
        2931: 1259481,
        2933: 377464,
        1732: 521407,
        546: 9359830,
        3991: 1993970,
        1728: 588888,
    },
    "date_added": {
        1731: "2022-10-12T13:38:50-07:00",
        2931: "2022-10-12T13:39:03-07:00",
        2933: "2022-10-12T13:39:17-07:00",
        1732: "2022-10-13T03:07:11-07:00",
        546: "2022-10-23T15:20:18-07:00",
        3991: "2022-10-23T16:02:37-07:00",
        1728: "2022-10-24T01:06:28-07:00",
    },
    "iid": {
        1731: 1153075592,
        2931: 1153075745,
        2933: 1153075904,
        1732: 1153395887,
        546: 1162727660,
        3991: 1162755158,
        1728: 1162958300,
    },
}

test_df = pd.DataFrame(test_dict)


def test_filter():
    coll = Collection(test_df)

    assert coll.to_dict() == {
        377464: "Ride The Lightning",
        521407: "...And Justice For All",
        588888: "St. Anger",
        1259481: "Kill 'Em All",
        1549636: "Master Of Puppets",
        1993970: "No Life 'Til Leather",
        9359830: "Hardwired...To Self-Destruct",
    }

    coll.filter("r:3", sort=False)
    assert coll.filter_list == (("r", "3"),)
    assert coll.to_dict() == {
        377464: "Ride The Lightning",
        1259481: "Kill 'Em All",
        1549636: "Master Of Puppets",
    }

    coll.sort()
    # TODO: find a better way to test sort

    #                              title  year  ...                 date_added         iid
    # 1731             Master Of Puppets  1986  ...  2022-10-12T13:38:50-07:00  1153075592
    # 2931                  Kill 'Em All  1983  ...  2022-10-12T13:39:03-07:00  1153075745
    # 2933            Ride The Lightning  1984  ...  2022-10-12T13:39:17-07:00  1153075904
    # 1732        ...And Justice For All  1988  ...  2022-10-13T03:07:11-07:00  1153395887
    # 546   Hardwired...To Self-Destruct  2016  ...  2022-10-23T15:20:18-07:00  1162727660
    # 3991          No Life 'Til Leather  1982  ...  2022-10-23T16:02:37-07:00  1162755158
    # 1728                     St. Anger  2003  ...  2022-10-24T01:06:28-07:00  1162958300

    assert coll.filter_list == (("r", "3"),)
    assert coll.df.year.to_list() == [
        1986,
        1983,
        1984,
        1988,
        2016,
        1982,
        2003,
    ]

    coll.filter("r:4")
    assert coll.filter_list == (("r", "3"), ("r", "4"))
    assert coll.to_dict() == {
        377464: "Ride The Lightning",
    }

    # TODO: r:2 -- would lead to empty self.filtered

    coll.reset_filters()
    assert coll.filter_list == ()
    assert coll.to_dict() == {
        377464: "Ride The Lightning",
        521407: "...And Justice For All",
        588888: "St. Anger",
        1259481: "Kill 'Em All",
        1549636: "Master Of Puppets",
        1993970: "No Life 'Til Leather",
        9359830: "Hardwired...To Self-Destruct",
    }

    # force sort by date_added
    coll.filter("r:3@")
    assert coll.filter_list == (("r", "3@"),)
    assert coll.df.title.to_list() == [
        "Master Of Puppets",
        "Kill 'Em All",
        "Ride The Lightning",
        "...And Justice For All",
        "Hardwired...To Self-Destruct",
        "No Life 'Til Leather",
        "St. Anger",
    ]
