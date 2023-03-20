""" Uber object for calibration images, e.g. arc, flat

.. include common links, assuming primary doc root is up one directory
.. include:: ../include/links.rst
"""

from IPython import embed

import numpy as np

from pypeit import msgs
from pypeit.par import pypeitpar
from pypeit.images import combineimage
from pypeit.images import pypeitimage
from pypeit.core.framematch import valid_frametype


class ArcImage(pypeitimage.PypeItCalibrationImage):
    """
    Simple DataContainer for the Arc Image
    """
    # version is inherited from PypeItImage

    # I/O
    output_to_disk = ('ARC_IMAGE', 'ARC_FULLMASK', 'ARC_DETECTOR',
                      'ARC_DET_IMG', # For echelle multi-detector wavelengths
                      )
    hdu_prefix = 'ARC_'

    # Master fun
    calib_type = 'Arc'


class AlignImage(pypeitimage.PypeItCalibrationImage):
    """
    Simple DataContainer for the Alignment Image
    """
    # version is inherited from PypeItImage

    # I/O
    output_to_disk = ('ALIGN_IMAGE', 'ALIGN_FULLMASK', 'ALIGN_DETECTOR')
    hdu_prefix = 'ALIGN_'

    # Master fun
    calib_type = 'Align'


class BiasImage(pypeitimage.PypeItCalibrationImage):
    """
    Simple DataContainer for the Bias Image
    """
    # version is inherited from PypeItImage

    # Output to disk
    output_to_disk = ('BIAS_IMAGE', 'BIAS_IVAR', 'BIAS_DETECTOR')
    hdu_prefix = 'BIAS_'
    calib_type = 'Bias'


class DarkImage(pypeitimage.PypeItCalibrationImage):
    """
    Simple DataContainer for the Dark Image
    """
    # version is inherited from PypeItImage

    # Output to disk
    output_to_disk = ('DARK_IMAGE', 'DARK_IVAR', 'DARK_DETECTOR')
    hdu_prefix = 'DARK_'
    calib_type = 'Dark'


class TiltImage(pypeitimage.PypeItCalibrationImage):
    """
    Simple DataContainer for the Tilt Image
    """
    # version is inherited from PypeItImage

    # I/O
    output_to_disk = ('TILT_IMAGE', 'TILT_FULLMASK', 'TILT_DETECTOR')
    hdu_prefix = 'TILT_'

    # Master fun
    calib_type = 'Tiltimg'


class TraceImage(pypeitimage.PypeItCalibrationImage):
    """
    Simple DataContainer for the Trace Image
    """
    # version is inherited from PypeItImage

    # I/O
    output_to_disk = ('TRACE_IMAGE', 'TRACE_FULLMASK', 'TRACE_DETECTOR')
    hdu_prefix = 'TRACE_'

    # TODO: This is a hack to limit the arrays written to the MasterEdges object
    # to just the selection above.  The reason this doesn't work like the other
    # master images above is because the `to_master_file` function is not used
    # to build the output MasterEdges file.  We might want to come up with a
    # better long-term fix for this.
    def _bundle(self):
        return [_d for _d in super()._bundle() 
                    if any(np.isin(list(_d.keys()), ['image', 'fullmask', 'detector']))]


class SkyRegions(pypeitimage.PypeItCalibrationImage):
    """
    Simple DataContainer for the SkyRegions Image
    """
    # version is inherited from PypeItImage

    # I/O
    output_to_disk = ('SKYREG_IMAGE')
    hdu_prefix = 'SKYREG_'

    # Master fun
    calib_type = 'SkyRegions'
    calib_file_format = 'fits.gz'


# Convert frame type into an Image.  These all should subclass from
# PypeItCalibrationImage.
frame_image_classes = dict(
    bias=BiasImage,
    dark=DarkImage,
    arc=ArcImage,
    tilt=TiltImage,
    trace=TraceImage,
    align=AlignImage)
"""
The list of classes that :func:`buildimage_fromlist` should use to decorate for
the specified frame types.
"""


def buildimage_fromlist(spectrograph, det, frame_par, file_list, bias=None, bpm=None, dark=None,
                        flatimages=None, maxiters=5, ignore_saturation=True, slits=None,
                        mosaic=None, calib_dir=None, setup=None, calib_id=None):
    """
    Perform basic image processing on a list of images and combine the results.

    .. warning::

        For image mosaics (when ``det`` is a tuple) the processing behavior is
        hard-coded such that bias and dark frames are *not* reformatted into a
        mosaic image.  They are saved in their native multi-image format.
        Bad-pixel masks are also expected to be in multi-image format.  See
        :class:`~pypeit.images.rawimage.RawImage`.

    Args:
        spectrograph (:class:`~pypeit.spectrographs.spectrograph.Spectrograph`):
            Spectrograph used to take the data.
        det (:obj:`int`, :obj:`tuple`):
            The 1-indexed detector number(s) to process.  If a tuple, it must
            include detectors viable as a mosaic for the provided spectrograph;
            see :func:`~pypeit.spectrographs.spectrograph.Spectrograph.allowed_mosaics`.
        frame_par (:class:`~pypeit.par.pypeitpar.FramePar`):
            Parameters that dictate the processing of the images.  See
            :class:`~pypeit.par.pypeitpar.ProcessImagesPar` for the
            defaults.
        file_list (:obj:`list`):
            List of files
        bias (:class:`~pypeit.images.buildimage.BiasImage`, optional):
            Bias image for bias subtraction; passed directly to
            :func:`~pypeit.images.rawimage.RawImage.process` for all images.
        bpm (`numpy.ndarray`_, optional):
            Bad pixel mask; passed directly to
            :func:`~pypeit.images.rawimage.RawImage.process` for all images.
        dark (:class:`~pypeit.images.buildimage.DarkImage`, optional):
            Dark-current image; passed directly to
            :func:`~pypeit.images.rawimage.RawImage.process` for all images.
        flatimages (:class:`~pypeit.flatfield.FlatImages`, optional):
            Flat-field images for flat fielding; passed directly to
            :func:`~pypeit.images.rawimage.RawImage.process` for all images.
        maxiters (:obj:`int`, optional):
            When ``combine_method='mean'``) and sigma-clipping
            (``sigma_clip`` is True), this sets the maximum number of
            rejection iterations.  If None, rejection iterations continue
            until no more data are rejected; see
            :func:`~pypeit.core.combine.weighted_combine``.
        ignore_saturation (:obj:`bool`, optional):
            If True, turn off the saturation flag in the individual images
            before stacking.  This avoids having such values set to 0, which
            for certain images (e.g. flat calibrations) can have unintended
            consequences.
        slits (:class:`~pypeit.slittrace.SlitTraceSet`, optional):
            Edge traces for all slits.  These are used to calculate spatial
            flexure between the image and the slits, and for constructing the
            slit-illumination correction.  See
            :class:`pypeit.images.rawimage.RawImage.process`.
        mosaic (:obj:`bool`, optional):
            Flag processed image will be a mosaic of multiple detectors.  By
            default, this is determined by the format of ``det`` and whether or
            not this is a bias or dark frame.  *Only used for testing purposes.*
        calib_dir (:obj:`str`, `Path`_, optional):
            The directory for processed calibration files.  Required for
            elements of :attr:`frame_image_classes`, ignored otherwise.
        setup (:obj:`str`, optional):
            The setup/configuration identifier to use for this dataset.
            Required for elements of :attr:`frame_image_classes`, ignored
            otherwise.
        calib_id (:obj:`str`, optional):
            The string listing the set of calibration groups associated with
            this dataset.  Required for elements of :attr:`frame_image_classes`,
            ignored otherwise.

    Returns:
        :class:`~pypeit.images.pypeitimage.PypeItImage`,
        :class:`~pypeit.images.pypeitimage.PypeItCalibrationImage`:  The
        processed and combined image.
    """
    # Check
    if not isinstance(frame_par, pypeitpar.FrameGroupPar):
        msgs.error('Provided ParSet for must be type FrameGroupPar, not '
                   f'{frame_par.__class__.__name__}.')
    if not valid_frametype(frame_par['frametype'], quiet=True):
        # NOTE: This should not be necessary because FrameGroupPar explicitly
        # requires frametype to be valid
        msgs.error(f'{frame_par["frametype"]} is not a valid PypeIt frame type.')

    # Should the detectors be reformatted into a single image mosaic?
    if mosaic is None:
        mosaic = isinstance(det, tuple) and frame_par['frametype'] not in ['bias', 'dark']

    # Do it
    combineImage = combineimage.CombineImage(spectrograph, det, frame_par['process'], file_list)
    pypeitImage = combineImage.run(bias=bias, bpm=bpm, dark=dark, flatimages=flatimages,
                                   sigma_clip=frame_par['process']['clip'],
                                   sigrej=frame_par['process']['comb_sigrej'],
                                   maxiters=maxiters, ignore_saturation=ignore_saturation,
                                   slits=slits, combine_method=frame_par['process']['combine'],
                                   mosaic=mosaic)

    # Return class type, if returning any of the frame_image_classes
    cls = frame_image_classes[frame_par['frametype']] \
            if frame_par['frametype'] in frame_image_classes.keys() else None

    # Decorate and return according to the type of calibration, primarily as
    # needed for handling calibration images.  WARNING: Any internals (i.e., the
    # ones defined by the _init_internals method) in pypeitImage are lost here.
    return pypeitImage if cls is None \
            else cls.from_pypeitimage(pypeitImage, calib_dir, setup, calib_id,
                                      spectrograph.get_det_name(det))


