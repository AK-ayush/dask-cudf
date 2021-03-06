import dask
import dask.dataframe as dd
import numpy as np
import pandas as pd
import pandas.util.testing as tm
import pytest
from pandas.util.testing import assert_frame_equal

import cudf
import dask_cudf as dgd


def test_from_cudf():
    np.random.seed(0)

    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=10000), "y": np.random.normal(size=10000)}
    )

    gdf = cudf.DataFrame.from_pandas(df)

    # Test simple around to/from dask
    ingested = dd.from_pandas(gdf, npartitions=2)
    assert_frame_equal(ingested.compute().to_pandas(), df)

    # Test conversion to dask.dataframe
    ddf = ingested.to_dask_dataframe()
    assert_frame_equal(ddf.compute(), df)


def test_from_cudf_with_generic_idx():

    cdf = cudf.DataFrame(
        [
            ("a", list(range(20))),
            ("b", list(reversed(range(20)))),
            ("c", list(range(20))),
        ]
    )

    ddf = dgd.from_cudf(cdf, npartitions=2)

    assert isinstance(ddf.index.compute(), cudf.dataframe.index.GenericIndex)
    dd.assert_eq(ddf.loc[1:2, ["a"]], cdf.loc[1:2, ["a"]])


def _fragmented_gdf(df, nsplit):
    n = len(df)

    # Split dataframe in *nsplit*
    subdivsize = n // nsplit
    starts = [i * subdivsize for i in range(nsplit)]
    ends = starts[1:] + [None]
    frags = [df[s:e] for s, e in zip(starts, ends)]
    return frags


@pytest.mark.xfail(reason="don't agree with this functionality")
def test_concat():
    np.random.seed(0)

    n = 1000
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=n), "y": np.random.normal(size=n)}
    )

    gdf = cudf.DataFrame.from_pandas(df)
    frags = _fragmented_gdf(gdf, nsplit=13)

    # Combine with concat
    concated = dgd.concat(frags)
    assert_frame_equal(df, concated)


@pytest.mark.xfail(reason="don't agree with this functionality")
def test_append():
    np.random.seed(0)

    n = 1000
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=n), "y": np.random.normal(size=n)}
    )

    gdf = cudf.DataFrame.from_pandas(df)
    frags = _fragmented_gdf(gdf, nsplit=13)

    # Combine with .append
    head = frags[0]
    tail = frags[1:]

    appended = dd.from_pandas(head, npartitions=1)
    for each in tail:
        appended = appended.append(each)

    dd.assert_eq(df, appended)


def test_series_concat():
    np.random.seed(0)

    n = 1000
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=n), "y": np.random.normal(size=n)}
    )

    gdf = cudf.DataFrame.from_pandas(df)
    frags = _fragmented_gdf(gdf, nsplit=13)

    frags = [df.x for df in frags]

    concated = dgd.concat(frags).compute().to_pandas()
    assert isinstance(concated, pd.Series)
    np.testing.assert_array_equal(concated, df.x)


def test_series_append():
    np.random.seed(0)

    n = 1000
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=n), "y": np.random.normal(size=n)}
    )

    gdf = cudf.DataFrame.from_pandas(df)
    frags = _fragmented_gdf(gdf, nsplit=13)

    frags = [df.x for df in frags]

    appending = dd.from_pandas(frags[0], npartitions=1)
    for frag in frags[1:]:
        appending = appending.append(frag)

    appended = appending.compute().to_pandas()
    assert isinstance(appended, pd.Series)
    np.testing.assert_array_equal(appended, df.x)


def test_query():
    np.random.seed(0)

    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=10), "y": np.random.normal(size=10)}
    )
    gdf = cudf.DataFrame.from_pandas(df)
    expr = "x > 2"

    assert_frame_equal(gdf.query(expr).to_pandas(), df.query(expr))

    queried = dd.from_pandas(gdf, npartitions=2).query(expr)

    got = queried.compute().to_pandas()
    expect = gdf.query(expr).to_pandas()

    assert_frame_equal(got, expect)


def test_head():
    np.random.seed(0)
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=100), "y": np.random.normal(size=100)}
    )
    gdf = cudf.DataFrame.from_pandas(df)
    dgf = dd.from_pandas(gdf, npartitions=2)

    assert_frame_equal(dgf.head().to_pandas(), df.head())


def test_from_dask_dataframe():
    np.random.seed(0)
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=20), "y": np.random.normal(size=20)}
    )
    ddf = dd.from_pandas(df, npartitions=2)
    dgdf = ddf.map_partitions(cudf.from_pandas)
    got = dgdf.compute().to_pandas()
    expect = df

    dd.assert_eq(got, expect)


@pytest.mark.parametrize("nelem", [10, 200, 1333])
def test_set_index(nelem):
    with dask.config.set(scheduler="single-threaded"):
        np.random.seed(0)
        # Use unique index range as the sort may not be stable-ordering
        x = np.arange(nelem)
        np.random.shuffle(x)
        df = pd.DataFrame({"x": x, "y": np.random.randint(0, nelem, size=nelem)})
        ddf = dd.from_pandas(df, npartitions=2)
        dgdf = ddf.map_partitions(cudf.from_pandas)

        expect = ddf.set_index("x")
        got = dgdf.set_index("x")

        dd.assert_eq(expect, got, check_index=False, check_divisions=False)


def assert_frame_equal_by_index_group(expect, got):
    assert sorted(expect.columns) == sorted(got.columns)
    assert sorted(set(got.index)) == sorted(set(expect.index))
    # Note the set_index sort is not stable,
    unique_values = sorted(set(got.index))
    for iv in unique_values:
        sr_expect = expect.loc[[iv]]
        sr_got = got.loc[[iv]]

        for k in expect.columns:
            # Sort each column before we compare them
            sorted_expect = sr_expect.sort_values(k)[k]
            sorted_got = sr_got.sort_values(k)[k]
            np.testing.assert_array_equal(sorted_expect, sorted_got)


@pytest.mark.parametrize("nelem", [10, 200, 1333])
def test_set_index_2(nelem):
    with dask.config.set(scheduler="single-threaded"):
        np.random.seed(0)
        df = pd.DataFrame(
            {
                "x": 100 + np.random.randint(0, nelem // 2, size=nelem),
                "y": np.random.normal(size=nelem),
            }
        )
        expect = df.set_index("x").sort_index()

        dgf = dd.from_pandas(cudf.DataFrame.from_pandas(df), npartitions=4)
        res = dgf.set_index("x")  # sort by default
        got = res.compute().to_pandas()

        assert_frame_equal_by_index_group(expect, got)


def test_set_index_w_series():
    with dask.config.set(scheduler="single-threaded"):
        nelem = 20
        np.random.seed(0)
        df = pd.DataFrame(
            {
                "x": 100 + np.random.randint(0, nelem // 2, size=nelem),
                "y": np.random.normal(size=nelem),
            }
        )
        expect = df.set_index(df.x).sort_index()

        dgf = dd.from_pandas(cudf.DataFrame.from_pandas(df), npartitions=4)
        res = dgf.set_index(dgf.x)  # sort by default
        got = res.compute().to_pandas()

        expect.index.name = None
        dd.assert_eq(expect, got)


def test_assign():
    np.random.seed(0)
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=20), "y": np.random.normal(size=20)}
    )

    dgf = dd.from_pandas(cudf.DataFrame.from_pandas(df), npartitions=2)
    pdcol = pd.Series(np.arange(20) + 1000)
    newcol = dd.from_pandas(cudf.Series(pdcol), npartitions=dgf.npartitions)
    out = dgf.assign(z=newcol)

    got = out.compute().to_pandas()
    assert_frame_equal(got.loc[:, ["x", "y"]], df)
    np.testing.assert_array_equal(got["z"], pdcol)


@pytest.mark.parametrize("data_type", ["int8", "int16", "int32", "int64"])
def test_setitem_scalar_integer(data_type):
    np.random.seed(0)
    scalar = np.random.randint(0, 100, dtype=data_type)
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=20), "y": np.random.normal(size=20)}
    )
    dgf = dd.from_pandas(cudf.DataFrame.from_pandas(df), npartitions=2)

    df["z"] = scalar
    dgf["z"] = scalar

    got = dgf.compute().to_pandas()
    np.testing.assert_array_equal(got["z"], df["z"])


@pytest.mark.parametrize("data_type", ["float32", "float64"])
def test_setitem_scalar_float(data_type):
    np.random.seed(0)
    scalar = np.random.randn(1).astype(data_type)[0]
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=20), "y": np.random.normal(size=20)}
    )
    dgf = dd.from_pandas(cudf.DataFrame.from_pandas(df), npartitions=2)

    df["z"] = scalar
    dgf["z"] = scalar

    got = dgf.compute().to_pandas()
    np.testing.assert_array_equal(got["z"], df["z"])


def test_setitem_scalar_datetime():
    np.random.seed(0)
    scalar = np.int64(np.random.randint(0, 100)).astype("datetime64[ms]")
    df = pd.DataFrame(
        {"x": np.random.randint(0, 5, size=20), "y": np.random.normal(size=20)}
    )
    dgf = dd.from_pandas(cudf.DataFrame.from_pandas(df), npartitions=2)

    df["z"] = scalar
    dgf["z"] = scalar

    got = dgf.compute().to_pandas()
    np.testing.assert_array_equal(got["z"], df["z"])


@pytest.mark.parametrize(
    "func",
    [
        lambda: tm.makeDataFrame().reset_index(),
        tm.makeDataFrame,
        tm.makeMixedDataFrame,
        tm.makeObjectSeries,
        tm.makeTimeSeries,
    ],
)
def test_repr(func):
    pdf = func()
    try:
        gdf = cudf.from_pandas(pdf)
    except Exception:
        raise pytest.xfail()
    # gddf = dd.from_pandas(gdf, npartitions=3, sort=False)  # TODO
    gddf = dd.from_pandas(gdf, npartitions=3, sort=False)

    assert repr(gddf)
    if hasattr(pdf, "_repr_html_"):
        assert gddf._repr_html_()


@pytest.fixture
def pdf():
    return pd.DataFrame(
        {"x": [1, 2, 3, 4, 5, 6], "y": [11.0, 12.0, 13.0, 14.0, 15.0, 16.0]}
    )


@pytest.fixture
def gdf(pdf):
    return cudf.from_pandas(pdf)


@pytest.fixture
def ddf(pdf):
    return dd.from_pandas(pdf, npartitions=3)


@pytest.fixture
def gddf(gdf):
    return dd.from_pandas(gdf, npartitions=3)


@pytest.mark.parametrize(
    "func",
    [
        lambda df: df + 1,
        lambda df: df.index,
        lambda df: df.x.sum(),
        lambda df: df.x.astype(float),
        lambda df: df.assign(z=df.x.astype("int")),
    ],
)
def test_unary_ops(func, gdf, gddf):
    p = func(gdf)
    g = func(gddf)
    dd.assert_eq(p, g, check_names=False)
