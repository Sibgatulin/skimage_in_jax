from math import ceil, floor

import jax.numpy as jnp
from jax.dtypes import result_type
from jax.numpy.fft import fftfreq, fftn, ifftn
from jaxtyping import Array, Complex, Float


def _upsampled_dft(data, upsampled_region_size, upsample_factor=1, axis_offsets=None):
    """
    Upsampled DFT by matrix multiplication.

    This code is intended to provide the same result as if the following
    operations were performed:
        - Embed the array "data" in an array that is ``upsample_factor`` times
          larger in each dimension.  ifftshift to bring the center of the
          image to (1,1).
        - Take the FFT of the larger array.
        - Extract an ``[upsampled_region_size]`` region of the result, starting
          with the ``[axis_offsets+1]`` element.

    It achieves this result by computing the DFT in the output array without
    the need to zeropad. Much faster and memory efficient than the zero-padded
    FFT approach if ``upsampled_region_size`` is much smaller than
    ``data.size * upsample_factor``.

    Parameters
    ----------
    data : array
        The input data array (DFT of original data) to upsample.
    upsampled_region_size : integer or tuple of integers, optional
        The size of the region to be sampled.  If one integer is provided, it
        is duplicated up to the dimensionality of ``data``.
    upsample_factor : integer, optional
        The upsampling factor.  Defaults to 1.
    axis_offsets : tuple of integers, optional
        The offsets of the region to be sampled.  Defaults to None (uses
        image center)

    Returns
    -------
    output : ndarray
            The upsampled DFT of the specified region.
    """
    # if people pass in an integer, expand it to a list of equal-sized sections
    if not hasattr(upsampled_region_size, "__iter__"):
        upsampled_region_size = [
            upsampled_region_size,
        ] * data.ndim
    else:
        if len(upsampled_region_size) != data.ndim:
            raise ValueError(
                "shape of upsampled region sizes must be equal "
                "to input data's number of dimensions."
            )

    if axis_offsets is None:
        axis_offsets = [
            0,
        ] * data.ndim
    else:
        if len(axis_offsets) != data.ndim:
            raise ValueError(
                "number of axis offsets must be equal to input "
                "data's number of dimensions."
            )

    im2pi = 1j * 2 * jnp.pi

    dim_properties = list(zip(data.shape, upsampled_region_size, axis_offsets))

    for n_items, ups_size, ax_offset in dim_properties[::-1]:
        kernel = (jnp.arange(ups_size) - ax_offset)[:, None] * fftfreq(
            n_items, upsample_factor
        )
        kernel = jnp.exp(-im2pi * kernel)
        # use kernel with same precision as the data
        kernel = kernel.astype(data.dtype, copy=False)

        # Equivalent to:
        #   data[i, j, k] = kernel[i, :] @ data[j, k].T
        data = jnp.tensordot(kernel, data, axes=(1, -1))
    return data


def phase_cross_correlation(
    reference_image,
    moving_image,
    *,
    upsample_factor=1,
    space="real",
    disambiguate=False,
    reference_mask=None,
    moving_mask=None,
    overlap_ratio=0.3,
    normalization="phase",
):
    """Efficient subpixel image translation registration by cross-correlation.

    This code gives the same precision as the FFT upsampled cross-correlation
    in a fraction of the computation time and with reduced memory requirements.
    It obtains an initial estimate of the cross-correlation peak by an FFT and
    then refines the shift estimation by upsampling the DFT only in a small
    neighborhood of that estimate by means of a matrix-multiply DFT [1]_.

    Parameters
    ----------
    reference_image : array
        Reference image.
    moving_image : array
        Image to register. Must be same dimensionality as
        ``reference_image``.
    upsample_factor : int, optional
        Upsampling factor. Images will be registered to within
        ``1 / upsample_factor`` of a pixel. For example
        ``upsample_factor == 20`` means the images will be registered
        within 1/20th of a pixel. Default is 1 (no upsampling).
        Not used if any of ``reference_mask`` or ``moving_mask`` is not None.
    space : string, one of "real" or "fourier", optional
        Defines how the algorithm interprets input data. "real" means
        data will be FFT'd to compute the correlation, while "fourier"
        data will bypass FFT of input data. Case insensitive. Not
        used if any of ``reference_mask`` or ``moving_mask`` is not
        None.
    disambiguate : bool
        The shift returned by this function is only accurate *modulo* the
        image shape, due to the periodic nature of the Fourier transform. If
        this parameter is set to ``True``, the *real* space cross-correlation
        is computed for each possible shift, and the shift with the highest
        cross-correlation within the overlapping area is returned.
    reference_mask : ndarray
        Boolean mask for ``reference_image``. The mask should evaluate
        to ``True`` (or 1) on valid pixels. ``reference_mask`` should
        have the same shape as ``reference_image``.
    moving_mask : ndarray or None, optional
        Boolean mask for ``moving_image``. The mask should evaluate to ``True``
        (or 1) on valid pixels. ``moving_mask`` should have the same shape
        as ``moving_image``. If ``None``, ``reference_mask`` will be used.
    overlap_ratio : float, optional
        Minimum allowed overlap ratio between images. The correlation for
        translations corresponding with an overlap ratio lower than this
        threshold will be ignored. A lower `overlap_ratio` leads to smaller
        maximum translation, while a higher `overlap_ratio` leads to greater
        robustness against spurious matches due to small overlap between
        masked images. Used only if one of ``reference_mask`` or
        ``moving_mask`` is not None.
    normalization : {"phase", None}
        The type of normalization to apply to the cross-correlation. This
        parameter is unused when masks (`reference_mask` and `moving_mask`) are
        supplied.

    Returns
    -------
    shift : ndarray
        Shift vector (in pixels) required to register ``moving_image``
        with ``reference_image``. Axis ordering is consistent with
        the axis order of the input array.
    error : float
        Translation invariant normalized RMS error between
        ``reference_image`` and ``moving_image``. For masked cross-correlation
        this error is not available and NaN is returned.
    phasediff : float
        Global phase difference between the two images (should be
        zero if images are non-negative). For masked cross-correlation
        this phase difference is not available and NaN is returned.

    Notes
    -----
    The use of cross-correlation to estimate image translation has a long
    history dating back to at least [2]_. The "phase correlation"
    method (selected by ``normalization="phase"``) was first proposed in [3]_.
    Publications [1]_ and [2]_ use an unnormalized cross-correlation
    (``normalization=None``). Which form of normalization is better is
    application-dependent. For example, the phase correlation method works
    well in registering images under different illumination, but is not very
    robust to noise. In a high noise scenario, the unnormalized method may be
    preferable.

    When masks are provided, a masked normalized cross-correlation algorithm is
    used [5]_, [6]_.

    References
    ----------
    .. [1] Manuel Guizar-Sicairos, Samuel T. Thurman, and James R. Fienup,
           "Efficient subpixel image registration algorithms,"
           Optics Letters 33, 156-158 (2008). :DOI:`10.1364/OL.33.000156`
    .. [2] P. Anuta, Spatial registration of multispectral and multitemporal
           digital imagery using fast Fourier transform techniques, IEEE Trans.
           Geosci. Electron., vol. 8, no. 4, pp. 353–368, Oct. 1970.
           :DOI:`10.1109/TGE.1970.271435`.
    .. [3] C. D. Kuglin D. C. Hines. The phase correlation image alignment
           method, Proceeding of IEEE International Conference on Cybernetics
           and Society, pp. 163-165, New York, NY, USA, 1975, pp. 163–165.
    .. [4] James R. Fienup, "Invariant error metrics for image reconstruction"
           Optics Letters 36, 8352-8357 (1997). :DOI:`10.1364/AO.36.008352`
    .. [5] Dirk Padfield. Masked Object Registration in the Fourier Domain.
           IEEE Transactions on Image Processing, vol. 21(5),
           pp. 2706-2718 (2012). :DOI:`10.1109/TIP.2011.2181402`
    .. [6] D. Padfield. "Masked FFT registration". In Proc. Computer Vision and
           Pattern Recognition, pp. 2918-2925 (2010).
           :DOI:`10.1109/CVPR.2010.5540032`
    """
    if (reference_mask is not None) or (moving_mask is not None):
        raise NotImplementedError("Masked phase cross-correlation is not implemented.")
    float_dtype = result_type(reference_image.real.dtype, moving_image.real.dtype)

    # Ensure images have the same shape
    if reference_image.shape != moving_image.shape:
        raise ValueError(
            "images must be same shape, got "
            f"{reference_image.shape=} != {moving_image.shape=}"
        )

    # assume complex data is already in Fourier space
    if space.lower() == "fourier":
        src_freq = jnp.asarray(reference_image)
        target_freq = jnp.asarray(moving_image)
    # real data needs to be fft'd.
    elif space.lower() == "real":
        src_freq = fftn(reference_image)  # consider specifying axes
        target_freq = fftn(moving_image)  # consider specifying axes
    else:
        raise ValueError('space argument must be "real" or "fourier"')

    # Compute cross-correlation in the Fourier domain
    image_product = src_freq * target_freq.conj()

    if normalization == "phase":
        eps = jnp.finfo(float_dtype).eps
        image_product /= jnp.maximum(jnp.abs(image_product), 100 * eps)
    elif normalization is not None:
        raise ValueError("normalization must be either phase or None")

    cross_correlation = ifftn(image_product)

    # Locate maximum
    maxima = jnp.unravel_index(
        jnp.argmax(jnp.abs(cross_correlation)), cross_correlation.shape
    )
    shape = jnp.array(src_freq.shape)
    midpoint = jnp.floor(shape / 2)

    shift = jnp.array(maxima, dtype=float_dtype)
    shift = jnp.where(shift > midpoint, shift - shape, shift)

    if upsample_factor == 1:
        src_amp = jnp.sum(jnp.real(src_freq * jnp.conj(src_freq))) / src_freq.size
        target_amp = (
            jnp.sum(jnp.real(target_freq * jnp.conj(target_freq))) / target_freq.size
        )
        CCmax = cross_correlation[maxima]
    # If upsampling > 1, then refine estimate with matrix multiply DFT
    else:
        # Initial shift estimate in upsampled grid
        shift = jnp.round(shift * upsample_factor) / upsample_factor
        upsampled_region_size = ceil(upsample_factor * 1.5)  # DIFF
        # Center of output array at dftshift + 1
        dftshift = floor(upsampled_region_size / 2.0)
        # Matrix multiply DFT around the current shift estimate
        sample_region_offset = dftshift - shift * upsample_factor
        cross_correlation = _upsampled_dft(
            image_product.conj(),
            upsampled_region_size,
            upsample_factor,
            sample_region_offset,
        ).conj()

        maxima = jnp.unravel_index(
            jnp.argmax(jnp.abs(cross_correlation)), cross_correlation.shape
        )
        CCmax = cross_correlation[maxima]

        maxima = jnp.array(maxima, dtype=float_dtype)
        maxima -= dftshift
        shift += maxima / upsample_factor

        src_amp = jnp.sum(jnp.real(src_freq * src_freq.conj()))
        target_amp = jnp.sum(jnp.real(target_freq * target_freq.conj()))

    shift = jnp.where(shape == 1, 0, shift)

    if disambiguate:
        raise NotImplementedError("disambiguation is not yet supported")

    # cannot check for NaNs if I want to jit. Handle them differently
    # if jnp.isnan(CCmax) or jnp.isnan(src_amp) or jnp.isnan(target_amp):
    #     raise ValueError("NaN values found, please remove NaNs from your input data.")

    error = _compute_error(CCmax, src_amp, target_amp)
    phasediff = _compute_phasediff(CCmax)
    return shift, error, phasediff


def _compute_error(
    cross_correlation_max: Complex[Array, ""],
    src_amp: Float[Array, ""],
    target_amp: Float[Array, ""],
) -> Float[Array, ""]:
    """
    Compute RMS error metric between ``src_image`` and ``target_image``.

    Parameters
    ----------
    cross_correlation_max : complex
        The complex value of the cross correlation at its maximum point.
    src_amp : float
        The normalized average image intensity of the source image
    target_amp : float
        The normalized average image intensity of the target image
    """
    amp = src_amp * target_amp
    # TODO: the following is not compatible with jit
    # if amp == 0:
    #     warnings.warn(
    #         "Could not determine RMS error between images with the normalized "
    #         f"average intensities {src_amp!r} and {target_amp!r}. Either the "
    #         "reference or moving image may be empty.",
    #         UserWarning,
    #         stacklevel=3,
    #     )
    error = 1.0 - cross_correlation_max * cross_correlation_max.conj() / amp
    return jnp.sqrt(jnp.abs(error))


def _compute_phasediff(cross_correlation_max):
    """
    Compute global phase difference between the two images (should be
        zero if images are non-negative).

    Parameters
    ----------
    cross_correlation_max : complex
        The complex value of the cross correlation at its maximum point.
    """
    return jnp.arctan2(cross_correlation_max.imag, cross_correlation_max.real)
