import re
import warnings

import numpy as np
import pytest
import scipy as sp
from numpy.testing import assert_array_equal

from sklearn import config_context, datasets
from sklearn.base import clone
from sklearn.datasets import load_iris
from sklearn.decomposition import PCA
from sklearn.decomposition._pca import _assess_dimension, _infer_dimension
from sklearn.utils._array_api import (
    _atol_for_type,
    _convert_to_numpy,
    yield_namespace_device_dtype_combinations,
)
from sklearn.utils._testing import _array_api_for_tests, assert_allclose
from sklearn.utils.estimator_checks import (
    _get_check_estimator_ids,
    check_array_api_input_and_values,
)
from sklearn.utils.fixes import CSC_CONTAINERS, CSR_CONTAINERS

iris = datasets.load_iris()
PCA_SOLVERS = ["full", "arpack", "randomized", "auto"]

# `SPARSE_M` and `SPARSE_N` could be larger, but be aware:
# * SciPy's generation of random sparse matrix can be costly
# * A (SPARSE_M, SPARSE_N) dense array is allocated to compare against
SPARSE_M, SPARSE_N = 1000, 300  # arbitrary
SPARSE_MAX_COMPONENTS = min(SPARSE_M, SPARSE_N)


def _check_fitted_pca_close(pca1, pca2, rtol):
    assert_allclose(pca1.components_, pca2.components_, rtol=rtol)
    assert_allclose(pca1.explained_variance_, pca2.explained_variance_, rtol=rtol)
    assert_allclose(pca1.singular_values_, pca2.singular_values_, rtol=rtol)
    assert_allclose(pca1.mean_, pca2.mean_, rtol=rtol)
    assert_allclose(pca1.n_components_, pca2.n_components_, rtol=rtol)
    assert_allclose(pca1.n_samples_, pca2.n_samples_, rtol=rtol)
    assert_allclose(pca1.noise_variance_, pca2.noise_variance_, rtol=rtol)
    assert_allclose(pca1.n_features_in_, pca2.n_features_in_, rtol=rtol)


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
@pytest.mark.parametrize("n_components", range(1, iris.data.shape[1]))
def test_pca(svd_solver, n_components):
    X = iris.data
    pca = PCA(n_components=n_components, svd_solver=svd_solver)

    # check the shape of fit.transform
    X_r = pca.fit(X).transform(X)
    assert X_r.shape[1] == n_components

    # check the equivalence of fit.transform and fit_transform
    X_r2 = pca.fit_transform(X)
    assert_allclose(X_r, X_r2)
    X_r = pca.transform(X)
    assert_allclose(X_r, X_r2)

    # Test get_covariance and get_precision
    cov = pca.get_covariance()
    precision = pca.get_precision()
    assert_allclose(np.dot(cov, precision), np.eye(X.shape[1]), atol=1e-12)


@pytest.mark.parametrize("density", [0.01, 0.1, 0.30])
@pytest.mark.parametrize("n_components", [1, 2, 10])
@pytest.mark.parametrize("sparse_container", CSR_CONTAINERS + CSC_CONTAINERS)
@pytest.mark.parametrize("svd_solver", ["arpack"])
@pytest.mark.parametrize("scale", [1, 10, 100])
def test_pca_sparse(
    global_random_seed, svd_solver, sparse_container, n_components, density, scale
):
    # Make sure any tolerance changes pass with SKLEARN_TESTS_GLOBAL_RANDOM_SEED="all"
    rtol = 5e-07
    transform_rtol = 3e-05

    random_state = np.random.default_rng(global_random_seed)
    X = sparse_container(
        sp.sparse.random(
            SPARSE_M,
            SPARSE_N,
            random_state=random_state,
            density=density,
        )
    )
    # Scale the data + vary the column means
    scale_vector = random_state.random(X.shape[1]) * scale
    X = X.multiply(scale_vector)

    pca = PCA(
        n_components=n_components,
        svd_solver=svd_solver,
        random_state=global_random_seed,
    )
    pca.fit(X)

    Xd = X.toarray()
    pcad = PCA(
        n_components=n_components,
        svd_solver=svd_solver,
        random_state=global_random_seed,
    )
    pcad.fit(Xd)

    # Fitted attributes equality
    _check_fitted_pca_close(pca, pcad, rtol=rtol)

    # Test transform
    X2 = sparse_container(
        sp.sparse.random(
            SPARSE_M,
            SPARSE_N,
            random_state=random_state,
            density=density,
        )
    )
    X2d = X2.toarray()

    assert_allclose(pca.transform(X2), pca.transform(X2d), rtol=transform_rtol)
    assert_allclose(pca.transform(X2), pcad.transform(X2d), rtol=transform_rtol)


@pytest.mark.parametrize("sparse_container", CSR_CONTAINERS + CSC_CONTAINERS)
def test_pca_sparse_fit_transform(global_random_seed, sparse_container):
    random_state = np.random.default_rng(global_random_seed)
    X = sparse_container(
        sp.sparse.random(
            SPARSE_M,
            SPARSE_N,
            random_state=random_state,
            density=0.01,
        )
    )
    X2 = sparse_container(
        sp.sparse.random(
            SPARSE_M,
            SPARSE_N,
            random_state=random_state,
            density=0.01,
        )
    )

    pca_fit = PCA(n_components=10, svd_solver="arpack", random_state=global_random_seed)
    pca_fit_transform = PCA(
        n_components=10, svd_solver="arpack", random_state=global_random_seed
    )

    pca_fit.fit(X)
    transformed_X = pca_fit_transform.fit_transform(X)

    _check_fitted_pca_close(pca_fit, pca_fit_transform, rtol=1e-10)
    assert_allclose(transformed_X, pca_fit_transform.transform(X), rtol=2e-9)
    assert_allclose(transformed_X, pca_fit.transform(X), rtol=2e-9)
    assert_allclose(pca_fit.transform(X2), pca_fit_transform.transform(X2), rtol=2e-9)


@pytest.mark.parametrize("svd_solver", ["randomized", "full", "auto"])
@pytest.mark.parametrize("sparse_container", CSR_CONTAINERS + CSC_CONTAINERS)
def test_sparse_pca_solver_error(global_random_seed, svd_solver, sparse_container):
    random_state = np.random.RandomState(global_random_seed)
    X = sparse_container(
        sp.sparse.random(
            SPARSE_M,
            SPARSE_N,
            random_state=random_state,
        )
    )
    pca = PCA(n_components=30, svd_solver=svd_solver)
    error_msg_pattern = (
        f'PCA only support sparse inputs with the "arpack" solver, while "{svd_solver}"'
        " was passed"
    )
    with pytest.raises(TypeError, match=error_msg_pattern):
        pca.fit(X)


def test_no_empty_slice_warning():
    # test if we avoid numpy warnings for computing over empty arrays
    n_components = 10
    n_features = n_components + 2  # anything > n_comps triggered it in 0.16
    X = np.random.uniform(-1, 1, size=(n_components, n_features))
    pca = PCA(n_components=n_components)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        pca.fit(X)


@pytest.mark.parametrize("copy", [True, False])
@pytest.mark.parametrize("solver", PCA_SOLVERS)
def test_whitening(solver, copy):
    # Check that PCA output has unit-variance
    rng = np.random.RandomState(0)
    n_samples = 100
    n_features = 80
    n_components = 30
    rank = 50

    # some low rank data with correlated features
    X = np.dot(
        rng.randn(n_samples, rank),
        np.dot(np.diag(np.linspace(10.0, 1.0, rank)), rng.randn(rank, n_features)),
    )
    # the component-wise variance of the first 50 features is 3 times the
    # mean component-wise variance of the remaining 30 features
    X[:, :50] *= 3

    assert X.shape == (n_samples, n_features)

    # the component-wise variance is thus highly varying:
    assert X.std(axis=0).std() > 43.8

    # whiten the data while projecting to the lower dim subspace
    X_ = X.copy()  # make sure we keep an original across iterations.
    pca = PCA(
        n_components=n_components,
        whiten=True,
        copy=copy,
        svd_solver=solver,
        random_state=0,
        iterated_power=7,
    )
    # test fit_transform
    X_whitened = pca.fit_transform(X_.copy())
    assert X_whitened.shape == (n_samples, n_components)
    X_whitened2 = pca.transform(X_)
    assert_allclose(X_whitened, X_whitened2, rtol=5e-4)

    assert_allclose(X_whitened.std(ddof=1, axis=0), np.ones(n_components))
    assert_allclose(X_whitened.mean(axis=0), np.zeros(n_components), atol=1e-12)

    X_ = X.copy()
    pca = PCA(
        n_components=n_components, whiten=False, copy=copy, svd_solver=solver
    ).fit(X_.copy())
    X_unwhitened = pca.transform(X_)
    assert X_unwhitened.shape == (n_samples, n_components)

    # in that case the output components still have varying variances
    assert X_unwhitened.std(axis=0).std() == pytest.approx(74.1, rel=1e-1)
    # we always center, so no test for non-centering.


@pytest.mark.parametrize("svd_solver", ["arpack", "randomized"])
def test_pca_explained_variance_equivalence_solver(svd_solver):
    rng = np.random.RandomState(0)
    n_samples, n_features = 100, 80
    X = rng.randn(n_samples, n_features)

    pca_full = PCA(n_components=2, svd_solver="full")
    pca_other = PCA(n_components=2, svd_solver=svd_solver, random_state=0)

    pca_full.fit(X)
    pca_other.fit(X)

    assert_allclose(
        pca_full.explained_variance_, pca_other.explained_variance_, rtol=5e-2
    )
    assert_allclose(
        pca_full.explained_variance_ratio_,
        pca_other.explained_variance_ratio_,
        rtol=5e-2,
    )


@pytest.mark.parametrize(
    "X",
    [
        np.random.RandomState(0).randn(100, 80),
        datasets.make_classification(100, 80, n_informative=78, random_state=0)[0],
    ],
    ids=["random-data", "correlated-data"],
)
@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_explained_variance_empirical(X, svd_solver):
    pca = PCA(n_components=2, svd_solver=svd_solver, random_state=0)
    X_pca = pca.fit_transform(X)
    assert_allclose(pca.explained_variance_, np.var(X_pca, ddof=1, axis=0))

    expected_result = np.linalg.eig(np.cov(X, rowvar=False))[0]
    expected_result = sorted(expected_result, reverse=True)[:2]
    assert_allclose(pca.explained_variance_, expected_result, rtol=5e-3)


@pytest.mark.parametrize("svd_solver", ["arpack", "randomized"])
def test_pca_singular_values_consistency(svd_solver):
    rng = np.random.RandomState(0)
    n_samples, n_features = 100, 80
    X = rng.randn(n_samples, n_features)

    pca_full = PCA(n_components=2, svd_solver="full", random_state=rng)
    pca_other = PCA(n_components=2, svd_solver=svd_solver, random_state=rng)

    pca_full.fit(X)
    pca_other.fit(X)

    assert_allclose(pca_full.singular_values_, pca_other.singular_values_, rtol=5e-3)


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_singular_values(svd_solver):
    rng = np.random.RandomState(0)
    n_samples, n_features = 100, 80
    X = rng.randn(n_samples, n_features)

    pca = PCA(n_components=2, svd_solver=svd_solver, random_state=rng)
    X_trans = pca.fit_transform(X)

    # compare to the Frobenius norm
    assert_allclose(
        np.sum(pca.singular_values_**2), np.linalg.norm(X_trans, "fro") ** 2
    )
    # Compare to the 2-norms of the score vectors
    assert_allclose(pca.singular_values_, np.sqrt(np.sum(X_trans**2, axis=0)))

    # set the singular values and see what er get back
    n_samples, n_features = 100, 110
    X = rng.randn(n_samples, n_features)

    pca = PCA(n_components=3, svd_solver=svd_solver, random_state=rng)
    X_trans = pca.fit_transform(X)
    X_trans /= np.sqrt(np.sum(X_trans**2, axis=0))
    X_trans[:, 0] *= 3.142
    X_trans[:, 1] *= 2.718
    X_hat = np.dot(X_trans, pca.components_)
    pca.fit(X_hat)
    assert_allclose(pca.singular_values_, [3.142, 2.718, 1.0])


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_check_projection(svd_solver):
    # Test that the projection of data is correct
    rng = np.random.RandomState(0)
    n, p = 100, 3
    X = rng.randn(n, p) * 0.1
    X[:10] += np.array([3, 4, 5])
    Xt = 0.1 * rng.randn(1, p) + np.array([3, 4, 5])

    Yt = PCA(n_components=2, svd_solver=svd_solver).fit(X).transform(Xt)
    Yt /= np.sqrt((Yt**2).sum())

    assert_allclose(np.abs(Yt[0][0]), 1.0, rtol=5e-3)


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_check_projection_list(svd_solver):
    # Test that the projection of data is correct
    X = [[1.0, 0.0], [0.0, 1.0]]
    pca = PCA(n_components=1, svd_solver=svd_solver, random_state=0)
    X_trans = pca.fit_transform(X)
    assert X_trans.shape, (2, 1)
    assert_allclose(X_trans.mean(), 0.00, atol=1e-12)
    assert_allclose(X_trans.std(), 0.71, rtol=5e-3)


@pytest.mark.parametrize("svd_solver", ["full", "arpack", "randomized"])
@pytest.mark.parametrize("whiten", [False, True])
def test_pca_inverse(svd_solver, whiten):
    # Test that the projection of data can be inverted
    rng = np.random.RandomState(0)
    n, p = 50, 3
    X = rng.randn(n, p)  # spherical data
    X[:, 1] *= 0.00001  # make middle component relatively small
    X += [5, 4, 3]  # make a large mean

    # same check that we can find the original data from the transformed
    # signal (since the data is almost of rank n_components)
    pca = PCA(n_components=2, svd_solver=svd_solver, whiten=whiten).fit(X)
    Y = pca.transform(X)
    Y_inverse = pca.inverse_transform(Y)
    assert_allclose(X, Y_inverse, rtol=5e-6)


@pytest.mark.parametrize(
    "data", [np.array([[0, 1, 0], [1, 0, 0]]), np.array([[0, 1, 0], [1, 0, 0]]).T]
)
@pytest.mark.parametrize(
    "svd_solver, n_components, err_msg",
    [
        ("arpack", 0, r"must be between 1 and min\(n_samples, n_features\)"),
        ("randomized", 0, r"must be between 1 and min\(n_samples, n_features\)"),
        ("arpack", 2, r"must be strictly less than min"),
        (
            "auto",
            3,
            (
                r"n_components=3 must be between 0 and min\(n_samples, "
                r"n_features\)=2 with svd_solver='full'"
            ),
        ),
    ],
)
def test_pca_validation(svd_solver, data, n_components, err_msg):
    # Ensures that solver-specific extreme inputs for the n_components
    # parameter raise errors
    smallest_d = 2  # The smallest dimension
    pca_fitted = PCA(n_components, svd_solver=svd_solver)

    with pytest.raises(ValueError, match=err_msg):
        pca_fitted.fit(data)

    # Additional case for arpack
    if svd_solver == "arpack":
        n_components = smallest_d

        err_msg = (
            "n_components={}L? must be strictly less than "
            r"min\(n_samples, n_features\)={}L? with "
            "svd_solver='arpack'".format(n_components, smallest_d)
        )
        with pytest.raises(ValueError, match=err_msg):
            PCA(n_components, svd_solver=svd_solver).fit(data)


@pytest.mark.parametrize(
    "solver, n_components_",
    [
        ("full", min(iris.data.shape)),
        ("arpack", min(iris.data.shape) - 1),
        ("randomized", min(iris.data.shape)),
    ],
)
@pytest.mark.parametrize("data", [iris.data, iris.data.T])
def test_n_components_none(data, solver, n_components_):
    pca = PCA(svd_solver=solver)
    pca.fit(data)
    assert pca.n_components_ == n_components_


@pytest.mark.parametrize("svd_solver", ["auto", "full"])
def test_n_components_mle(svd_solver):
    # Ensure that n_components == 'mle' doesn't raise error for auto/full
    rng = np.random.RandomState(0)
    n_samples, n_features = 600, 10
    X = rng.randn(n_samples, n_features)
    pca = PCA(n_components="mle", svd_solver=svd_solver)
    pca.fit(X)
    assert pca.n_components_ == 1


@pytest.mark.parametrize("svd_solver", ["arpack", "randomized"])
def test_n_components_mle_error(svd_solver):
    # Ensure that n_components == 'mle' will raise an error for unsupported
    # solvers
    rng = np.random.RandomState(0)
    n_samples, n_features = 600, 10
    X = rng.randn(n_samples, n_features)
    pca = PCA(n_components="mle", svd_solver=svd_solver)
    err_msg = "n_components='mle' cannot be a string with svd_solver='{}'".format(
        svd_solver
    )
    with pytest.raises(ValueError, match=err_msg):
        pca.fit(X)


def test_pca_dim():
    # Check automated dimensionality setting
    rng = np.random.RandomState(0)
    n, p = 100, 5
    X = rng.randn(n, p) * 0.1
    X[:10] += np.array([3, 4, 5, 1, 2])
    pca = PCA(n_components="mle", svd_solver="full").fit(X)
    assert pca.n_components == "mle"
    assert pca.n_components_ == 1


def test_infer_dim_1():
    # TODO: explain what this is testing
    # Or at least use explicit variable names...
    n, p = 1000, 5
    rng = np.random.RandomState(0)
    X = (
        rng.randn(n, p) * 0.1
        + rng.randn(n, 1) * np.array([3, 4, 5, 1, 2])
        + np.array([1, 0, 7, 4, 6])
    )
    pca = PCA(n_components=p, svd_solver="full")
    pca.fit(X)
    spect = pca.explained_variance_
    ll = np.array([_assess_dimension(spect, k, n) for k in range(1, p)])
    assert ll[1] > ll.max() - 0.01 * n


def test_infer_dim_2():
    # TODO: explain what this is testing
    # Or at least use explicit variable names...
    n, p = 1000, 5
    rng = np.random.RandomState(0)
    X = rng.randn(n, p) * 0.1
    X[:10] += np.array([3, 4, 5, 1, 2])
    X[10:20] += np.array([6, 0, 7, 2, -1])
    pca = PCA(n_components=p, svd_solver="full")
    pca.fit(X)
    spect = pca.explained_variance_
    assert _infer_dimension(spect, n) > 1


def test_infer_dim_3():
    n, p = 100, 5
    rng = np.random.RandomState(0)
    X = rng.randn(n, p) * 0.1
    X[:10] += np.array([3, 4, 5, 1, 2])
    X[10:20] += np.array([6, 0, 7, 2, -1])
    X[30:40] += 2 * np.array([-1, 1, -1, 1, -1])
    pca = PCA(n_components=p, svd_solver="full")
    pca.fit(X)
    spect = pca.explained_variance_
    assert _infer_dimension(spect, n) > 2


@pytest.mark.parametrize(
    "X, n_components, n_components_validated",
    [
        (iris.data, 0.95, 2),  # row > col
        (iris.data, 0.01, 1),  # row > col
        (np.random.RandomState(0).rand(5, 20), 0.5, 2),
    ],  # row < col
)
def test_infer_dim_by_explained_variance(X, n_components, n_components_validated):
    pca = PCA(n_components=n_components, svd_solver="full")
    pca.fit(X)
    assert pca.n_components == pytest.approx(n_components)
    assert pca.n_components_ == n_components_validated


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_score(svd_solver):
    # Test that probabilistic PCA scoring yields a reasonable score
    n, p = 1000, 3
    rng = np.random.RandomState(0)
    X = rng.randn(n, p) * 0.1 + np.array([3, 4, 5])
    pca = PCA(n_components=2, svd_solver=svd_solver)
    pca.fit(X)

    ll1 = pca.score(X)
    h = -0.5 * np.log(2 * np.pi * np.exp(1) * 0.1**2) * p
    assert_allclose(ll1 / h, 1, rtol=5e-2)

    ll2 = pca.score(rng.randn(n, p) * 0.2 + np.array([3, 4, 5]))
    assert ll1 > ll2

    pca = PCA(n_components=2, whiten=True, svd_solver=svd_solver)
    pca.fit(X)
    ll2 = pca.score(X)
    assert ll1 > ll2


def test_pca_score3():
    # Check that probabilistic PCA selects the right model
    n, p = 200, 3
    rng = np.random.RandomState(0)
    Xl = rng.randn(n, p) + rng.randn(n, 1) * np.array([3, 4, 5]) + np.array([1, 0, 7])
    Xt = rng.randn(n, p) + rng.randn(n, 1) * np.array([3, 4, 5]) + np.array([1, 0, 7])
    ll = np.zeros(p)
    for k in range(p):
        pca = PCA(n_components=k, svd_solver="full")
        pca.fit(Xl)
        ll[k] = pca.score(Xt)

    assert ll.argmax() == 1


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_sanity_noise_variance(svd_solver):
    # Sanity check for the noise_variance_. For more details see
    # https://github.com/scikit-learn/scikit-learn/issues/7568
    # https://github.com/scikit-learn/scikit-learn/issues/8541
    # https://github.com/scikit-learn/scikit-learn/issues/8544
    X, _ = datasets.load_digits(return_X_y=True)
    pca = PCA(n_components=30, svd_solver=svd_solver, random_state=0)
    pca.fit(X)
    assert np.all((pca.explained_variance_ - pca.noise_variance_) >= 0)


@pytest.mark.parametrize("svd_solver", ["arpack", "randomized"])
def test_pca_score_consistency_solvers(svd_solver):
    # Check the consistency of score between solvers
    X, _ = datasets.load_digits(return_X_y=True)
    pca_full = PCA(n_components=30, svd_solver="full", random_state=0)
    pca_other = PCA(n_components=30, svd_solver=svd_solver, random_state=0)
    pca_full.fit(X)
    pca_other.fit(X)
    assert_allclose(pca_full.score(X), pca_other.score(X), rtol=5e-6)


# arpack raises ValueError for n_components == min(n_samples,  n_features)
@pytest.mark.parametrize("svd_solver", ["full", "randomized"])
def test_pca_zero_noise_variance_edge_cases(svd_solver):
    # ensure that noise_variance_ is 0 in edge cases
    # when n_components == min(n_samples, n_features)
    n, p = 100, 3
    rng = np.random.RandomState(0)
    X = rng.randn(n, p) * 0.1 + np.array([3, 4, 5])

    pca = PCA(n_components=p, svd_solver=svd_solver)
    pca.fit(X)
    assert pca.noise_variance_ == 0
    # Non-regression test for gh-12489
    # ensure no divide-by-zero error for n_components == n_features < n_samples
    pca.score(X)

    pca.fit(X.T)
    assert pca.noise_variance_ == 0
    # Non-regression test for gh-12489
    # ensure no divide-by-zero error for n_components == n_samples < n_features
    pca.score(X.T)


@pytest.mark.parametrize(
    "data, n_components, expected_solver",
    [  # case: n_components in (0,1) => 'full'
        (np.random.RandomState(0).uniform(size=(1000, 50)), 0.5, "full"),
        # case: max(X.shape) <= 500 => 'full'
        (np.random.RandomState(0).uniform(size=(10, 50)), 5, "full"),
        # case: n_components >= .8 * min(X.shape) => 'full'
        (np.random.RandomState(0).uniform(size=(1000, 50)), 50, "full"),
        # n_components >= 1 and n_components < .8*min(X.shape) => 'randomized'
        (np.random.RandomState(0).uniform(size=(1000, 50)), 10, "randomized"),
    ],
)
def test_pca_svd_solver_auto(data, n_components, expected_solver):
    pca_auto = PCA(n_components=n_components, random_state=0)
    pca_test = PCA(
        n_components=n_components, svd_solver=expected_solver, random_state=0
    )
    pca_auto.fit(data)
    pca_test.fit(data)
    assert_allclose(pca_auto.components_, pca_test.components_)


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_deterministic_output(svd_solver):
    rng = np.random.RandomState(0)
    X = rng.rand(10, 10)

    transformed_X = np.zeros((20, 2))
    for i in range(20):
        pca = PCA(n_components=2, svd_solver=svd_solver, random_state=rng)
        transformed_X[i, :] = pca.fit_transform(X)[0]
    assert_allclose(transformed_X, np.tile(transformed_X[0, :], 20).reshape(20, 2))


@pytest.mark.parametrize("svd_solver", PCA_SOLVERS)
def test_pca_dtype_preservation(svd_solver):
    check_pca_float_dtype_preservation(svd_solver)
    check_pca_int_dtype_upcast_to_double(svd_solver)


def check_pca_float_dtype_preservation(svd_solver):
    # Ensure that PCA does not upscale the dtype when input is float32
    X_64 = np.random.RandomState(0).rand(1000, 4).astype(np.float64, copy=False)
    X_32 = X_64.astype(np.float32)

    pca_64 = PCA(n_components=3, svd_solver=svd_solver, random_state=0).fit(X_64)
    pca_32 = PCA(n_components=3, svd_solver=svd_solver, random_state=0).fit(X_32)

    assert pca_64.components_.dtype == np.float64
    assert pca_32.components_.dtype == np.float32
    assert pca_64.transform(X_64).dtype == np.float64
    assert pca_32.transform(X_32).dtype == np.float32

    # the rtol is set such that the test passes on all platforms tested on
    # conda-forge: PR#15775
    # see: https://github.com/conda-forge/scikit-learn-feedstock/pull/113
    assert_allclose(pca_64.components_, pca_32.components_, rtol=2e-4)


def check_pca_int_dtype_upcast_to_double(svd_solver):
    # Ensure that all int types will be upcast to float64
    X_i64 = np.random.RandomState(0).randint(0, 1000, (1000, 4))
    X_i64 = X_i64.astype(np.int64, copy=False)
    X_i32 = X_i64.astype(np.int32, copy=False)

    pca_64 = PCA(n_components=3, svd_solver=svd_solver, random_state=0).fit(X_i64)
    pca_32 = PCA(n_components=3, svd_solver=svd_solver, random_state=0).fit(X_i32)

    assert pca_64.components_.dtype == np.float64
    assert pca_32.components_.dtype == np.float64
    assert pca_64.transform(X_i64).dtype == np.float64
    assert pca_32.transform(X_i32).dtype == np.float64

    assert_allclose(pca_64.components_, pca_32.components_, rtol=1e-4)


def test_pca_n_components_mostly_explained_variance_ratio():
    # when n_components is the second highest cumulative sum of the
    # explained_variance_ratio_, then n_components_ should equal the
    # number of features in the dataset #15669
    X, y = load_iris(return_X_y=True)
    pca1 = PCA().fit(X, y)

    n_components = pca1.explained_variance_ratio_.cumsum()[-2]
    pca2 = PCA(n_components=n_components).fit(X, y)
    assert pca2.n_components_ == X.shape[1]


def test_assess_dimension_bad_rank():
    # Test error when tested rank not in [1, n_features - 1]
    spectrum = np.array([1, 1e-30, 1e-30, 1e-30])
    n_samples = 10
    for rank in (0, 5):
        with pytest.raises(ValueError, match=r"should be in \[1, n_features - 1\]"):
            _assess_dimension(spectrum, rank, n_samples)


def test_small_eigenvalues_mle():
    # Test rank associated with tiny eigenvalues are given a log-likelihood of
    # -inf. The inferred rank will be 1
    spectrum = np.array([1, 1e-30, 1e-30, 1e-30])

    assert _assess_dimension(spectrum, rank=1, n_samples=10) > -np.inf

    for rank in (2, 3):
        assert _assess_dimension(spectrum, rank, 10) == -np.inf

    assert _infer_dimension(spectrum, 10) == 1


def test_mle_redundant_data():
    # Test 'mle' with pathological X: only one relevant feature should give a
    # rank of 1
    X, _ = datasets.make_classification(
        n_features=20,
        n_informative=1,
        n_repeated=18,
        n_redundant=1,
        n_clusters_per_class=1,
        random_state=42,
    )
    pca = PCA(n_components="mle").fit(X)
    assert pca.n_components_ == 1


def test_fit_mle_too_few_samples():
    # Tests that an error is raised when the number of samples is smaller
    # than the number of features during an mle fit
    X, _ = datasets.make_classification(n_samples=20, n_features=21, random_state=42)

    pca = PCA(n_components="mle", svd_solver="full")
    with pytest.raises(
        ValueError,
        match="n_components='mle' is only supported if n_samples >= n_features",
    ):
        pca.fit(X)


def test_mle_simple_case():
    # non-regression test for issue
    # https://github.com/scikit-learn/scikit-learn/issues/16730
    n_samples, n_dim = 1000, 10
    X = np.random.RandomState(0).randn(n_samples, n_dim)
    X[:, -1] = np.mean(X[:, :-1], axis=-1)  # true X dim is ndim - 1
    pca_skl = PCA("mle", svd_solver="full")
    pca_skl.fit(X)
    assert pca_skl.n_components_ == n_dim - 1


def test_assess_dimesion_rank_one():
    # Make sure assess_dimension works properly on a matrix of rank 1
    n_samples, n_features = 9, 6
    X = np.ones((n_samples, n_features))  # rank 1 matrix
    _, s, _ = np.linalg.svd(X, full_matrices=True)
    # except for rank 1, all eigenvalues are 0 resp. close to 0 (FP)
    assert_allclose(s[1:], np.zeros(n_features - 1), atol=1e-12)

    assert np.isfinite(_assess_dimension(s, rank=1, n_samples=n_samples))
    for rank in range(2, n_features):
        assert _assess_dimension(s, rank, n_samples) == -np.inf


def test_pca_randomized_svd_n_oversamples():
    """Check that exposing and setting `n_oversamples` will provide accurate results
    even when `X` as a large number of features.

    Non-regression test for:
    https://github.com/scikit-learn/scikit-learn/issues/20589
    """
    rng = np.random.RandomState(0)
    n_features = 100
    X = rng.randn(1_000, n_features)

    # The default value of `n_oversamples` will lead to inaccurate results
    # We force it to the number of features.
    pca_randomized = PCA(
        n_components=1,
        svd_solver="randomized",
        n_oversamples=n_features,
        random_state=0,
    ).fit(X)
    pca_full = PCA(n_components=1, svd_solver="full").fit(X)
    pca_arpack = PCA(n_components=1, svd_solver="arpack", random_state=0).fit(X)

    assert_allclose(np.abs(pca_full.components_), np.abs(pca_arpack.components_))
    assert_allclose(np.abs(pca_randomized.components_), np.abs(pca_arpack.components_))


def test_feature_names_out():
    """Check feature names out for PCA."""
    pca = PCA(n_components=2).fit(iris.data)

    names = pca.get_feature_names_out()
    assert_array_equal([f"pca{i}" for i in range(2)], names)


@pytest.mark.parametrize("copy", [True, False])
def test_variance_correctness(copy):
    """Check the accuracy of PCA's internal variance calculation"""
    rng = np.random.RandomState(0)
    X = rng.randn(1000, 200)
    pca = PCA().fit(X)
    pca_var = pca.explained_variance_ / pca.explained_variance_ratio_
    true_var = np.var(X, ddof=1, axis=0).sum()
    np.testing.assert_allclose(pca_var, true_var)


def check_array_api_get_precision(name, estimator, array_namespace, device, dtype):
    xp, device, dtype = _array_api_for_tests(array_namespace, device, dtype)
    iris_np = iris.data.astype(dtype)
    iris_xp = xp.asarray(iris_np, device=device)

    estimator.fit(iris_np)
    precision_np = estimator.get_precision()
    covariance_np = estimator.get_covariance()

    with config_context(array_api_dispatch=True):
        estimator_xp = clone(estimator).fit(iris_xp)
        precision_xp = estimator_xp.get_precision()
        assert precision_xp.shape == (4, 4)
        assert precision_xp.dtype == iris_xp.dtype

        assert_allclose(
            _convert_to_numpy(precision_xp, xp=xp),
            precision_np,
            atol=_atol_for_type(dtype),
        )
        covariance_xp = estimator_xp.get_covariance()
        assert covariance_xp.shape == (4, 4)
        assert covariance_xp.dtype == iris_xp.dtype

        assert_allclose(
            _convert_to_numpy(covariance_xp, xp=xp),
            covariance_np,
            atol=_atol_for_type(dtype),
        )


@pytest.mark.parametrize(
    "array_namespace, device, dtype", yield_namespace_device_dtype_combinations()
)
@pytest.mark.parametrize(
    "check",
    [check_array_api_input_and_values, check_array_api_get_precision],
    ids=_get_check_estimator_ids,
)
@pytest.mark.parametrize(
    "estimator",
    [
        PCA(n_components=2, svd_solver="full"),
        PCA(n_components=2, svd_solver="full", whiten=True),
        PCA(
            n_components=2,
            svd_solver="randomized",
            power_iteration_normalizer="QR",
            random_state=0,  # how to use global_random_seed here?
        ),
    ],
    ids=_get_check_estimator_ids,
)
def test_pca_array_api_compliance(estimator, check, array_namespace, device, dtype):
    name = estimator.__class__.__name__
    check(name, estimator, array_namespace, device=device, dtype=dtype)


def test_array_api_error_and_warnings_on_unsupported_params():
    pytest.importorskip("array_api_compat")
    xp = pytest.importorskip("numpy.array_api")
    iris_xp = xp.asarray(iris.data)

    pca = PCA(n_components=2, svd_solver="arpack", random_state=0)
    expected_msg = re.escape(
        "PCA with svd_solver='arpack' is not supported for Array API inputs."
    )
    with pytest.raises(ValueError, match=expected_msg):
        with config_context(array_api_dispatch=True):
            pca.fit(iris_xp)

    pca.set_params(svd_solver="randomized", power_iteration_normalizer="LU")
    expected_msg = re.escape(
        "Array API does not support LU factorization. Set"
        " `power_iteration_normalizer='QR'` instead."
    )
    with pytest.raises(ValueError, match=expected_msg):
        with config_context(array_api_dispatch=True):
            pca.fit(iris_xp)

    pca.set_params(svd_solver="randomized", power_iteration_normalizer="auto")
    expected_msg = re.escape(
        "Array API does not support LU factorization, falling back to QR instead. Set"
        " `power_iteration_normalizer='QR'` explicitly to silence this warning."
    )
    with pytest.warns(UserWarning, match=expected_msg):
        with config_context(array_api_dispatch=True):
            pca.fit(iris_xp)
