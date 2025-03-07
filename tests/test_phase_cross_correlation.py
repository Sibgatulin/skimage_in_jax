import itertools
import warnings

import jax.numpy as jnp
import jax.numpy.fft as fft
import pytest
from jax import jit
from numpy.testing import assert_allclose, assert_array_equal, assert_equal
from scipy.ndimage import fourier_shift
from skimage import img_as_float
from skimage._shared._warnings import expected_warnings
from skimage._shared.testing import assert_stacklevel
from skimage._shared.utils import _supported_float_type
from skimage.data import binary_blobs, camera, eagle

from skimage_in_jax.registration import _upsampled_dft, phase_cross_correlation


@pytest.fixture(params=[False, True])
def phase_cross_correlation_maybe_jitted(request):
    if request.param:
        return jit(
            phase_cross_correlation,
            static_argnames=[
                "upsample_factor",
                "space",
                "disambiguate",
                "reference_mask",
                "moving_mask",
                "overlap_ratio",
                "normalization",
            ],
        )
    else:
        return phase_cross_correlation


@pytest.mark.parametrize("normalization", [None, "phase"])
def test_correlation(normalization, phase_cross_correlation_maybe_jitted):
    reference_image = fft.fftn(camera())
    shift = (-7, 12)
    shifted_image = fourier_shift(reference_image, shift)

    # pixel precision
    result, _, _ = phase_cross_correlation_maybe_jitted(
        reference_image, shifted_image, space="fourier", normalization=normalization
    )
    assert_allclose(result[:2], -jnp.array(shift))


@pytest.mark.parametrize("normalization", ["nonexisting"])
def test_correlation_invalid_normalization(
    normalization, phase_cross_correlation_maybe_jitted
):
    reference_image = fft.fftn(camera())
    shift = (-7, 12)
    shifted_image = fourier_shift(reference_image, shift)

    # pixel precision
    with pytest.raises(ValueError):
        phase_cross_correlation_maybe_jitted(
            reference_image, shifted_image, space="fourier", normalization=normalization
        )


@pytest.mark.parametrize("normalization", [None, "phase"])
def test_subpixel_precision(normalization, phase_cross_correlation_maybe_jitted):
    reference_image = fft.fftn(camera())
    subpixel_shift = (-2.4, 1.32)
    shifted_image = fourier_shift(reference_image, subpixel_shift)

    # subpixel precision
    result, _, _ = phase_cross_correlation_maybe_jitted(
        reference_image,
        shifted_image,
        upsample_factor=100,
        space="fourier",
        normalization=normalization,
    )
    assert_allclose(result[:2], -jnp.array(subpixel_shift), atol=0.05)


@pytest.mark.parametrize(
    "dtype",
    [
        pytest.param(jnp.float16, marks=pytest.mark.skip),
        jnp.float32,
        pytest.param(jnp.float64, marks=pytest.mark.skip),
    ],
)
def test_real_input(dtype, phase_cross_correlation_maybe_jitted):
    reference_image = camera().astype(dtype, copy=False)
    subpixel_shift = (-2.4, 1.32)
    shifted_image = fourier_shift(fft.fftn(reference_image), subpixel_shift)
    shifted_image = fft.ifftn(shifted_image).real.astype(dtype, copy=False)

    # subpixel precision
    result, error, diffphase = phase_cross_correlation_maybe_jitted(
        reference_image, shifted_image, upsample_factor=100
    )
    assert result.dtype == _supported_float_type(dtype)
    assert_allclose(result[:2], -jnp.array(subpixel_shift), atol=0.05)


def test_size_one_dimension_input(phase_cross_correlation_maybe_jitted):
    # take a strip of the input image
    reference_image = fft.fftn(camera()[:, 15]).reshape((-1, 1))
    subpixel_shift = (-2.4, 4)
    shifted_image = fourier_shift(reference_image, subpixel_shift)

    # subpixel precision
    result, error, diffphase = phase_cross_correlation_maybe_jitted(
        reference_image, shifted_image, upsample_factor=20, space="fourier"
    )
    assert_allclose(result[:2], -jnp.array((-2.4, 0)), atol=0.05)


def test_3d_input(phase_cross_correlation_maybe_jitted):
    phantom = img_as_float(binary_blobs(length=32, n_dim=3))
    reference_image = fft.fftn(phantom)
    shift = (-2.0, 1.0, 5.0)
    shifted_image = fourier_shift(reference_image, shift)

    result, error, diffphase = phase_cross_correlation_maybe_jitted(
        reference_image, shifted_image, space="fourier"
    )
    assert_allclose(result, -jnp.array(shift), atol=0.05)

    # subpixel precision now available for 3-D data

    subpixel_shift = (-2.3, 1.7, 5.4)
    shifted_image = fourier_shift(reference_image, subpixel_shift)
    result, error, diffphase = phase_cross_correlation_maybe_jitted(
        reference_image, shifted_image, upsample_factor=100, space="fourier"
    )
    assert_allclose(result, -jnp.array(subpixel_shift), atol=0.05)


def test_unknown_space_input(phase_cross_correlation_maybe_jitted):
    image = jnp.ones((5, 5))
    with pytest.raises(ValueError):
        phase_cross_correlation_maybe_jitted(image, image, space="frank")


@pytest.mark.xfail(
    reason="in order to be JIT-table, the implementation does not check for values"
)
def test_wrong_input(phase_cross_correlation_maybe_jitted):
    # Dimensionality mismatch
    image = jnp.ones((5, 5, 1))
    template = jnp.ones((5, 5))
    with pytest.raises(ValueError):
        phase_cross_correlation_maybe_jitted(template, image)

    # Size mismatch
    image = jnp.ones((5, 5))
    template = jnp.ones((4, 4))
    with pytest.raises(ValueError):
        phase_cross_correlation_maybe_jitted(template, image)

    # NaN values in data
    image = jnp.ones((5, 5)).at[0, 0].set(jnp.nan)
    template = jnp.ones((5, 5))
    with expected_warnings(
        [
            r"invalid value encountered in true_divide"
            + r"|"
            + r"invalid value encountered in divide"
            + r"|\A\Z"
        ]
    ):
        with pytest.raises(ValueError):
            phase_cross_correlation_maybe_jitted(template, image)


def test_4d_input_pixel(phase_cross_correlation_maybe_jitted):
    phantom = img_as_float(binary_blobs(length=32, n_dim=4))
    reference_image = fft.fftn(phantom)
    shift = (-2.0, 1.0, 5.0, -3)
    shifted_image = fourier_shift(reference_image, shift)
    result, error, diffphase = phase_cross_correlation_maybe_jitted(
        reference_image, shifted_image, space="fourier"
    )
    assert_allclose(result, -jnp.array(shift), atol=0.05)


def test_4d_input_subpixel(phase_cross_correlation_maybe_jitted):
    phantom = img_as_float(binary_blobs(length=32, n_dim=4))
    reference_image = fft.fftn(phantom)
    subpixel_shift = (-2.3, 1.7, 5.4, -3.2)
    shifted_image = fourier_shift(reference_image, subpixel_shift)
    result, error, diffphase = phase_cross_correlation_maybe_jitted(
        reference_image, shifted_image, upsample_factor=10, space="fourier"
    )
    assert_allclose(result, -jnp.array(subpixel_shift), atol=0.05)


def test_mismatch_upsampled_region_size():
    with pytest.raises(ValueError):
        _upsampled_dft(jnp.ones((4, 4)), upsampled_region_size=[3, 2, 1, 4])


def test_mismatch_offsets_size():
    with pytest.raises(ValueError):
        _upsampled_dft(jnp.ones((4, 4)), 3, axis_offsets=[3, 2, 1, 4])


@pytest.mark.skip(reason="disambiguate is not yet supported")
@pytest.mark.parametrize(
    ("shift0", "shift1"),
    itertools.product((100, -100, 350, -350), (100, -100, 350, -350)),
)
def test_disambiguate_2d(shift0, shift1, phase_cross_correlation_maybe_jitted):
    image = eagle()[500:, 900:]  # use a highly textured part of image
    shift = (shift0, shift1)
    origin0 = []
    for s in shift:
        if s > 0:
            origin0.append(0)
        else:
            origin0.append(-s)
    origin1 = jnp.array(origin0) + shift
    slice0 = tuple(slice(o, o + 450) for o in origin0)
    slice1 = tuple(slice(o, o + 450) for o in origin1)
    reference = image[slice0]
    moving = image[slice1]
    computed_shift, _, _ = phase_cross_correlation_maybe_jitted(
        reference,
        moving,
        disambiguate=True,
    )
    assert_equal(shift, computed_shift)


@pytest.mark.skip(reason="disambiguate is not yet supported")
def test_invalid_value_in_division_warnings(phase_cross_correlation_maybe_jitted):
    """Regression test for https://github.com/scikit-image/scikit-image/issues/7146."""
    im1 = jnp.zeros((100, 100)).at[50, 50].set(1)
    im2 = jnp.zeros((100, 100)).at[60, 60].set(1)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        phase_cross_correlation_maybe_jitted(im1, im2, disambiguate=True)


@pytest.mark.skip(reason="disambiguate is not yet supported")
@pytest.mark.parametrize("disambiguate", [True, False])
def test_disambiguate_zero_shift(disambiguate, phase_cross_correlation_maybe_jitted):
    """When the shift is 0, disambiguation becomes degenerate.

    Some quadrants become size 0, which prevents computation of
    cross-correlation. This test ensures that nothing bad happens in that
    scenario.
    """
    image = camera()
    computed_shift, _, _ = phase_cross_correlation_maybe_jitted(
        image,
        image,
        disambiguate=disambiguate,
    )
    assert isinstance(computed_shift, jnp.ndarray)
    assert_array_equal(computed_shift, jnp.array((0.0, 0.0)))


@pytest.mark.skip(reason="disambiguate is not yet supported")
@pytest.mark.parametrize("null_images", [(1, 0), (0, 1), (0, 0)])
def test_disambiguate_empty_image(null_images, phase_cross_correlation_maybe_jitted):
    """When the image is empty, disambiguation becomes degenerate."""
    image = camera()
    with pytest.warns(UserWarning) as records:
        shift, error, phasediff = phase_cross_correlation_maybe_jitted(
            image * null_images[0], image * null_images[1], disambiguate=True
        )
        assert_stacklevel(records, offset=-3)
    assert_array_equal(shift, jnp.array([0.0, 0.0]))
    assert jnp.isnan(error)
    assert phasediff == 0.0

    # Check warnings
    assert len(records) == 2
    assert "Could not determine real-space shift" in records[0].message.args[0]
    assert "Could not determine RMS error between images" in records[1].message.args[0]
