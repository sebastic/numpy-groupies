import timeit
import numpy as np
import pytest

from .. import (aggregate_py, aggregate_ufunc, aggregate_np as aggregate_numpy,
                aggregate_weave, aggregate_pd as aggregate_pandas)


_implementations = "py ufunc numpy weave pandas".split()
_implementations = ['aggregate_' + impl for impl in _implementations]
aggregate_implementations = dict((impl, globals()[impl]) for impl in _implementations)


class AttrDict(dict):
    __getattr__ = dict.__getitem__


@pytest.fixture(params=_implementations, ids=lambda x: x[0])
def aggregate_all(request):
    impl = aggregate_implementations[request.param]
    if impl is None:
        pytest.xfail("Implementation not available")
    return impl


def test_preserve_missing(aggregate_all):
    res = aggregate_all(np.array([0, 1, 3, 1, 3]), np.arange(101, 106, dtype=int))
    np.testing.assert_array_equal(res, np.array([101, 206, 0, 208]))
    if aggregate_all != aggregate_py:
        assert 'int' in res.dtype.name


def test_start_with_offset(aggregate_all):
    group_idx = np.array([1, 1, 2, 2, 2, 2, 4, 4])
    res = aggregate_all(group_idx, np.ones(group_idx.size), dtype=int)
    np.testing.assert_array_equal(res, np.array([0, 2, 4, 0, 2]))
    if aggregate_all != aggregate_py:
        assert 'int' in res.dtype.name


def test_start_with_offset_prod(aggregate_all):
    group_idx = np.array([2, 2, 4, 4, 4, 7, 7, 7])
    res = aggregate_all(group_idx, group_idx, func=np.prod, dtype=int)
    np.testing.assert_array_equal(res, np.array([0, 0, 4, 0, 64, 0, 0, 343]))


def test_no_negative_indices(aggregate_all):
    pytest.raises(ValueError, aggregate_all, np.arange(-10, 10), np.arange(20))


def test_parameter_missing(aggregate_all):
    pytest.raises(TypeError, aggregate_all, np.arange(5))


def test_shape_mismatch(aggregate_all):
    pytest.raises(ValueError, aggregate_all, np.array((1, 2, 3)), np.array((1, 2)))


def test_create_lists(aggregate_all):
    res = aggregate_all(np.array([0, 1, 3, 1, 3]), np.arange(101, 106, dtype=int), func=list)
    np.testing.assert_array_equal(np.array(res[0]), np.array([101]))
    assert res[2] == 0
    np.testing.assert_array_equal(np.array(res[3]), np.array([103, 105]))


def test_stable_sort(aggregate_all):
    group_idx = np.repeat(np.arange(5), 4)
    a = np.arange(group_idx.size)
    res = aggregate_all(group_idx, a, func=list)
    np.testing.assert_array_equal(np.array(res[0]), np.array([0, 1, 2, 3]))
    a = np.arange(group_idx.size)[::-1]
    res = aggregate_all(group_idx, a, func=list)
    np.testing.assert_array_equal(np.array(res[0]), np.array([19, 18, 17, 16]))


def test_item_counting(aggregate_all):
    group_idx = np.array([0, 1, 2, 3, 3, 3, 3, 4, 5, 5, 5, 6, 5, 4, 3, 8, 8])
    a = np.arange(group_idx.size)
    res = aggregate_all(group_idx, a, func=lambda x: len(x) > 1)
    np.testing.assert_array_equal(res, np.array([0, 0, 0, 1, 1, 1, 0, 0, 1]))


@pytest.mark.parametrize(["func", "fill_value"], [(np.array, None), (np.sum, -1)])
def test_fill_value(aggregate_all, func, fill_value):
    group_idx = np.array([0, 2, 2], dtype=int)
    res = aggregate_all(group_idx, np.arange(len(group_idx), dtype=int), func=func, fill_value=fill_value)
    assert res[1] == fill_value


def test_fortran_arrays(aggregate_all):
    """ Numpy handles C and Fortran style indices. Optimized aggregate has to
        convert the Fortran matrices to C style, before doing it's job.
    """
    t = 10
    for order_style in ('C', 'F'):
        mat = np.zeros((t, t), order=order_style, dtype=float)
        mat.flat[:] = np.arange(t * t)
        assert aggregate_all(np.zeros(t, dtype=int), mat[0, :])[0] == sum(range(t))


@pytest.fixture(params=['np/py', 'c/np', 'ufunc/np', 'pandas/np'], scope='module')
def aggregate_compare(request):
    if request.param == 'np/py':
        func = aggregate_numpy
        func_ref = aggregate_py
        group_cnt = 100
    else:
        group_cnt = 3000
        func_ref = aggregate_numpy
        if 'ufunc' in request.param:
            func = aggregate_ufunc
        elif 'pandas' in request.param:
            func = aggregate_pandas
        else:
            func = aggregate_weave

    if not func:
        pytest.xfail("Implementation not available")

    # Gives 100000 duplicates of size 10 each
    group_idx = np.repeat(np.arange(group_cnt), 2)
    np.random.shuffle(group_idx)
    group_idx = np.repeat(group_idx, 10)

    a = np.random.randn(group_idx.size)
    nana = a.copy()
    nana[::3] = np.nan
    somea = a.copy()
    somea[somea < 0.3] = 0
    somea[::31] = np.nan
    return AttrDict(locals())


def func_arbitrary(iterator):
    tmp = 0
    for x in iterator:
        tmp += x * x
    return tmp

def func_preserve_order(iterator):
    tmp = 0
    for i, x in enumerate(iterator, 1):
        tmp += x ** i
    return tmp


def allnan(x):
    return np.all(np.isnan(x))

def anynan(x):
    return np.any(np.isnan(x))

func_list = (np.sum, np.min, np.max, np.prod, np.all, np.any, np.mean, np.std,
             np.nansum, np.nanmin, np.nanmax, np.nanmean, np.nanstd,
             anynan, allnan, func_arbitrary, func_preserve_order)


@pytest.mark.parametrize("func", func_list, ids=lambda x: getattr(x, '__name__', x))
def test_compare(aggregate_compare, func, decimal=14):
    a = aggregate_compare.nana if 'nan' in getattr(func, '__name__', func) else aggregate_compare.a
    ref = aggregate_compare.func_ref(aggregate_compare.group_idx, a, func=func)
    try:
        res = aggregate_compare.func(aggregate_compare.group_idx, a, func=func)
    except NotImplementedError:
        pytest.xfail("Function not yet implemented")
    else:
        np.testing.assert_array_almost_equal(res, ref, decimal=decimal)


def test_timing_sum(aggregate_compare):
    try:
        t1 = timeit.Timer(lambda: aggregate_compare.func(aggregate_compare.group_idx, aggregate_compare.a)).timeit(number=3)
    except NotImplementedError:
        pytest.xfail("Function not yet implemented")
    t0 = timeit.Timer(lambda: aggregate_compare.func_ref(aggregate_compare.group_idx, aggregate_compare.a)).timeit(number=3)
    assert t0 > t1
    print "%s/%s speedup: %.3f" % (aggregate_compare.func.func_name, aggregate_compare.func_ref.func_name, t0 / t1)


def test_timing_std(aggregate_compare):
    try:
        t1 = timeit.Timer(lambda: aggregate_compare.func(aggregate_compare.group_idx, aggregate_compare.a, func=np.std)).timeit(number=3)
    except NotImplementedError:
        pytest.xfail("Function not yet implemented")
    t0 = timeit.Timer(lambda: aggregate_compare.func_ref(aggregate_compare.group_idx, aggregate_compare.a, func=np.std)).timeit(number=3)
    assert t0 > t1
    print "%s/%s speedup: %.3f" % (aggregate_compare.func.func_name, aggregate_compare.func_ref.func_name, t0 / t1)


def benchmark(group_cnt=10000):
    group_idx = np.repeat(np.arange(group_cnt), 2)
    np.random.shuffle(group_idx)
    group_idx = np.repeat(group_idx, 10)
    a = np.random.randn(group_idx.size)

    accfuncs = aggregate_implementations[1:]
    print "function" + ''.join(f.__name__.rjust(15) for f in accfuncs)
    print "-" * 53
    for func in func_list[:-2]:
        print func.__name__.ljust(8),
        results = []
        for aggregatefunc in accfuncs:
            try:
                res = aggregatefunc(group_idx, a, func=func)
            except NotImplementedError:
                continue
            else:
                results.append(res)
            t0 = timeit.Timer(lambda: aggregatefunc(group_idx, a, func=func)).timeit(number=10)
            print ("%.3f" % (t0 * 1000)).rjust(14),
        print
        for res in results[1:]:
            np.testing.assert_array_almost_equal(res, results[0])

if __name__ == '__main__':
    benchmark()