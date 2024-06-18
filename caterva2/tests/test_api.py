###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import pathlib

import httpx

import blosc2
import pytest

import caterva2 as cat2
import numpy as np

from .services import TEST_CATERVA2_ROOT
from .. import api_utils


@pytest.fixture
def pub_host(services):
    return services.get_endpoint(f'publisher.{TEST_CATERVA2_ROOT}')


@pytest.fixture
def sub_url(services):
    return services.get_urlbase('subscriber')


def my_path(dspath, slice_):
    slice_ = api_utils.slice_to_string(slice_)
    if slice_:
        suffix = dspath.suffix
        dspath = dspath.with_suffix('')
        dspath = pathlib.Path(f'{dspath}[{slice_}]{suffix}')
    return dspath


def test_roots(services, pub_host, sub_url, sub_jwt_cookie):
    roots = cat2.get_roots(sub_url, auth_cookie=sub_jwt_cookie)
    assert roots[TEST_CATERVA2_ROOT]['name'] == TEST_CATERVA2_ROOT
    assert roots[TEST_CATERVA2_ROOT]['http'] == pub_host


def test_root(services, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    assert myroot.name == TEST_CATERVA2_ROOT
    assert myroot.sub_url == sub_url


def test_list(services, examples_dir, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    example = examples_dir
    nodes = set(str(f.relative_to(str(example))) for f in example.rglob("*") if f.is_file())
    assert set(myroot.node_list) == nodes


def test_file(services, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    file = myroot['README.md']
    assert file.name == 'README.md'
    assert file.sub_url == sub_url


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(10, 20, 1)])
def test_index_dataset_frame(slice_, services, examples_dir, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot['ds-hello.b2frame']
    assert ds.name == 'ds-hello.b2frame'
    assert ds.sub_url == sub_url

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    if isinstance(slice_, int):
        assert ord(ds[slice_]) == a[slice_]  # TODO: why do we need ord() here?
        assert ord(ds.fetch(slice_)) == a[slice_]
    else:
        assert ds[slice_] == a[slice_]
        assert ds.fetch(slice_) == a[slice_]


def test_dataset_step_diff_1(services, examples_dir, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot['ds-hello.b2frame']
    assert ds.name == 'ds-hello.b2frame'
    assert ds.sub_url == sub_url
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:
        _ = ds[::2]
        assert str(e_info.value) == 'Only step=1 is supported'


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(1, 5, 1)])
def test_index_dataset_1d(slice_, services, examples_dir, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot['ds-1d.b2nd']
    assert ds.name == 'ds-1d.b2nd'
    assert ds.sub_url == sub_url

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])
    np.testing.assert_array_equal(ds.fetch(slice_), a[slice_])


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(1, 5, 1), (slice(None, 10), slice(None, 20))])
@pytest.mark.parametrize("name", ['dir1/ds-2d.b2nd', 'dir2/ds-4d.b2nd'])
def test_index_dataset_nd(slice_, name, services, examples_dir, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot[name]
    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])
    np.testing.assert_array_equal(ds.fetch(slice_), a[slice_])


@pytest.mark.parametrize("name", ['ds-1d.b2nd', 'dir1/ds-2d.b2nd'])
def test_download_b2nd(name, services, examples_dir, sub_url,
                       sub_user, sub_jwt_cookie):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot[name]
    path = ds.download()
    assert path == ds.path

    # Data contents
    example = examples_dir / name
    a = blosc2.open(example)
    b = blosc2.open(path)
    np.testing.assert_array_equal(a[:], b[:])

    # Using 2-step download
    urlpath = ds.get_download_url()
    data = httpx.get(urlpath,
                     headers={'Cookie': sub_jwt_cookie} if sub_user else None)
    assert data.status_code == 200
    b = blosc2.ndarray_from_cframe(data.content)
    np.testing.assert_array_equal(a[:], b[:])


def test_download_b2frame(services, examples_dir, sub_url,
                          sub_user, sub_jwt_cookie):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url, user_auth=sub_user)
    ds = myroot['ds-hello.b2frame']
    path = ds.download()
    assert path == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = blosc2.open(example)
    b = blosc2.open(path)
    assert a[:] == b[:]

    # Using 2-step download
    urlpath = ds.get_download_url()
    assert urlpath == f"{sub_url}api/fetch/{ds.path}"
    data = httpx.get(urlpath,
                     headers={'Cookie': sub_jwt_cookie} if sub_user else None)
    assert data.status_code == 200
    b = blosc2.schunk_from_cframe(data.content)
    assert a[:] == b[:]


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(1, 5, 1)])
def test_index_regular_file(slice_, services, examples_dir, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot['README.md']

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read().encode()
    if isinstance(slice_, int):
        assert ord(ds[slice_]) == a[slice_]  # TODO: why do we need ord() here?
        assert ord(ds.fetch(slice_)) == a[slice_]
    else:
        assert ds[slice_] == a[slice_]
        assert ds.fetch(slice_) == a[slice_]


def test_download_regular_file(services, examples_dir, sub_url,
                               sub_user, sub_jwt_cookie):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot['README.md']
    path = ds.download()
    assert path == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read()
    b = open(path).read()
    assert a[:] == b[:]

    # Using 2-step download
    urlpath = ds.get_download_url()
    assert urlpath == f"{sub_url}api/fetch/{ds.path}"
    data = httpx.get(urlpath,
                     headers={'Cookie': sub_jwt_cookie} if sub_user else None)
    assert data.status_code == 200
    b = blosc2.schunk_from_cframe(data.content)
    # TODO: why do we need .decode() here?
    assert a[:] == b[:].decode()


@pytest.mark.parametrize("name", ['ds-1d.b2nd',
                                  'ds-hello.b2frame',
                                  'README.md'])
def test_vlmeta(name, services, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot[name]
    schunk_meta = ds.meta.get('schunk', ds.meta)
    assert ds.vlmeta is schunk_meta['vlmeta']


def test_vlmeta_data(services, sub_url, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_url=sub_url, user_auth=sub_user)
    ds = myroot['ds-sc-attr.b2nd']
    assert ds.vlmeta == dict(a=1, b="foo", c=123.456)
