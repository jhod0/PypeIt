# Module for guiding Arc/Sky line tracing
from __future__ import absolute_import, division, print_function

import os
import inspect
import numpy as np

#from importlib import reload

from astropy.io import fits

from pypeit import msgs
from pypeit import masterframe
from pypeit import ginga
from pypeit.core import arc
from pypeit.core import tracewave
from pypeit.par import pypeitpar
from pypeit.spectrographs.util import load_spectrograph

from pypeit import debugger


class WaveTilts(masterframe.MasterFrame):
    """Class to guide slit/order tracing

    Parameters
    ----------
    msarc : ndarray
      Arc image
    tslits_dict : dict
      Input from TraceSlits
    settings_det : dict
      Detector settings -- Needed for arc line saturation
    det : int
      Detector index
    settings : dict
      Tilts settings

    Attributes
    ----------
    frametype : str
      Hard-coded to 'tilts'
    steps : list
    mask : ndarray, bool
      True = Ignore this slit
    all_trcdict : list of dict
      All trace dict's
    tilts : ndarray
      Tilts for a single slit/order
    all_ttilts : list of tuples
      Tuple of tilts ndarray's
    final_tilts : ndarray
      Final tilts image

    """
    
    # Frametype is a class attribute
    frametype = 'tilts'

    def __init__(self, msarc, spectrograph=None, par=None, det=None, setup=None, master_dir=None,
                 mode=None, tslits_dict=None, redux_path=None, bpm=None):

        # TODO: (KBW) Why was setup='' in this argument list and
        # setup=None in all the others?  Is it because of the
        # from_master_files() classmethod below?  Changed it to match
        # the rest of the MasterFrame children.

        # Instantiate the spectograph
        # TODO: (KBW) Do we need this?  It's only used to get the
        # non-linear counts and the name of the master directory

        self.spectrograph = load_spectrograph(spectrograph)

        # MasterFrame
        masterframe.MasterFrame.__init__(self, self.frametype, setup,
                                         master_dir=master_dir, mode=mode)

        self.par = pypeitpar.WaveTiltsPar() if par is None else par

        # Parameters (but can be None)
        self.msarc = msarc
        if bpm is None:
            self.bpm = np.zeros_like(msarc)
        else:
            self.bpm = bpm
        self.tslits_dict = tslits_dict

        # Optional parameters
        self.det = det
        self.redux_path = redux_path

        # Attributes
        if self.tslits_dict is not None:
            self.nslit = self.tslits_dict['lcen'].shape[1]
        else:
            self.nslit = 0
        self.steps = []
        self.slitmask = None

        # Key Internals
        self.mask = None
        self.all_trcdict = [None]*self.nslit
        self.tilts = None
        self.all_ttilts = [None]*self.nslit

        # Main outputs
        self.final_tilts = None
        self.coeffs = None
        self.tilts_dict = None

    # This method does not appear finished
    @classmethod
    def from_master_files(cls, setup, mdir='./'):
        """
        Build the class from Master frames

        Parameters
        ----------
        setup : str
        mdir : str, optional

        Returns
        -------
        slf

        """

        # Instantiate
        slf = cls(None, setup=setup)
        msarc_file = masterframe.master_name('arc', setup, mdir)
        # Arc
        msarc, _, _ = slf.load_master(msarc_file)
        slf.msarc = msarc


        # Tilts
        mstilts_file = masterframe.master_name('tilts', setup, mdir)
        hdul = fits.open(mstilts_file)
        slf.final_tilts = hdul[0].data
        slf.tilts = slf.final_tilts
        slf.coeffs = slf.hdu[1].data

        # Dict
        slf.all_trcdict = []
        islit = 0
        for hdu in hdul[2:]:
            if hdu.name == 'FWM{:03d}'.format(islit):
                # Setup
                fwm_img = hdu.data
                narc = fwm_img.shape[1]
                trcdict = dict(xtfit=[], ytfit=[], xmodel=[], ymodel=[], ycen=[], aduse=np.zeros(narc, dtype=bool))
                # Fill  (the -1 are for ycen which is packed in at the end)
                for iarc in range(narc):
                    trcdict['xtfit'].append(fwm_img[:-1,iarc,0])
                    trcdict['ytfit'].append(fwm_img[:-1,iarc,1])
                    trcdict['ycen'].append(fwm_img[-1,iarc,1])  # Many of these are junk
                    if np.any(fwm_img[:-1,iarc,2] > 0):
                        trcdict['xmodel'].append(fwm_img[:-1,iarc,2])
                        trcdict['ymodel'].append(fwm_img[:-1,iarc,3])
                        trcdict['aduse'][iarc] = True
                #
                slf.all_trcdict.append(trcdict.copy())
            else:
                slf.all_trcdict.append(None)
            islit += 1
        # FInish
        return slf


    def _analyze_lines(self, slit):
        """
        Analyze the tilts of the arc lines in a given slit/order

        Wrapper to tracewave.analyze_lines()

        Parameters
        ----------
        slit : int

        Returns
        -------
        self.badlines

        """
        self.badlines, self.all_ttilts[slit] \
                = tracewave.analyze_lines(self.msarc, self.all_trcdict[slit], slit,
                                            self.tslits_dict['pixcen'], order=self.par['order'],
                                            function=self.par['function'])
        if self.badlines > 0:
            msgs.warn('There were {0:d} additional arc lines that '.format(self.badlines) +
                      'should have been traced' + msgs.newline() + '(perhaps lines were '
                      'saturated?). Check the spectral tilt solution')
        # Step
        self.steps.append(inspect.stack()[0][3])
        return self.badlines

    def _extract_arcs(self):
        """
        Extract the arcs down each slit/order

        Wrapper to arc.get_censpec()

        Returns
        -------
        self.arccen
        self.arc_maskslit

        """
        # Extract an arc down each slit/order
        inmask = (self.bpm == 0) if self.bpm is not None else None
        self.arccen, self.arc_maskslit = arc.get_censpec(self.tslits_dict['lcen'], self.tslits_dict['rcen'],
                                                         self.slitmask, self.msarc, inmask = inmask)
        # Step
        self.steps.append(inspect.stack()[0][3])
        return self.arccen, self.arc_maskslit

    def _fit_tilts(self, slit, show_QA=False, doqa=True):
        """

        Parameters
        ----------
        slit : int
        show_QA : bool, optional
          Show the QA plot (e.g. in a Notebook)
        doqa : bool, optional
          Perform the QA

        Returns
        -------
        self.tilts : ndarray
        coeffs

        """
        self.tilts, coeffs, self.outpar = tracewave.fit_tilts(self.msarc, slit, self.all_ttilts[slit],
                                                        order=self.par['order'],
                                                        yorder=self.par['yorder'],
                                                        func2D=self.par['func2D'],
                                                        setup=self.setup, show_QA=show_QA,
                                                        doqa=doqa, out_dir=self.redux_path)
        # Step
        self.steps.append(inspect.stack()[0][3])
        return self.tilts, coeffs

    def _trace_tilts(self, slit, wv_calib=None):
        """

        Parameters
        ----------
        slit : int
        wv_calib : dict, optional
          Used only for avoiding ghosts


        Returns
        -------
        trcdict : dict
          Filled in self.all_trcdict[]

        """
        # Determine the tilts for this slit
        tracethresh_in = self.par['tracethresh']
        if isinstance(tracethresh_in,(float, int)):
            tracethresh = tracethresh_in
        elif isinstance(tracethresh_in, (list, np.ndarray)):
            tracethresh = tracethresh_in[slit]
        else:
            raise ValueError('Invalid input for parameter tracethresh')

        nonlinear_counts = self.spectrograph.detector[self.det-1]['saturation'] \
                                * self.spectrograph.detector[self.det-1]['nonlinear']

        # JFH Code block starts here
        ########

        from pypeit.core import pixels
        from pypeit.core import trace_slits
        from pypeit.core import extract
        from astropy.stats import sigma_clipped_stats


        nspat = self.msarc.shape[1]
        arcimg = self.msarc
        arc_spec = self.arccen[:, slit]
        slit_left = self.tslits_dict['lcen'][:,slit].copy()
        slit_righ = self.tslits_dict['rcen'][:,slit].copy()
        slitmask = pixels.slit_pixels(self.tslits_dict['lcen'], self.tslits_dict['rcen'], nspat)
        thismask = slitmask == slit

        # Tilt specific Optional parameters
        tracethresh = 10.0 # threshold for tracing an arc line
        only_these_lines = None
        debug = True
        n_neigh = 15
        # Optional Parameters for arc line detection
        sigdetect = 5.0 # This is for line finding, and hence this
        # threshold determines the number of lines that may be removed because they are too close.
        fwhm = 4.0
        fit_frac_fwhm = 1.25
        mask_frac_fwhm = 1.0
        max_frac_fwhm = 2.0
        cont_samp = 30
        niter_cont = 3
        debug_lines = True
        # def trace_tilts(arcimg, arc_spec, thismask, slit_left, slit_righ, only_these_lines = None, tracethresh = 10.0, only_these_lines = None, n_neigh = 15, sigdetect = 5.0, fwhm = 4.0, fit_frac_fwhm=1.25, mask_frac_fwhm = 1.0, max_frac_fwhm = 2.0, cont_samp = 30,
        #    niter_cont = 3, nonlinear_counts = 1e10, verbose = False, debug=False, debug_lines = False)

        nspec, nspat = arcimg.shape
        spec_vec = np.arange(nspec)
        spat_vec = np.arange(nspat)

        # Find peaks with a liberal threshold of sigdetect = 5.0
        tampl_tot, tampl_cont_tot, tcent_tot, twid_tot, _, wgood, _, nsig_tot = arc.detect_lines(
            arc_spec, sigdetect = sigdetect, fwhm = fwhm,fit_frac_fwhm = fit_frac_fwhm,mask_frac_fwhm = mask_frac_fwhm,
            max_frac_fwhm = max_frac_fwhm,cont_samp = cont_samp, niter_cont = niter_cont,nonlinear_counts=nonlinear_counts,
            debug = debug_lines)
        # Good lines
        arcdet = tcent_tot[wgood]
        nsig = nsig_tot[wgood]

        # Determine the best lines to use to trace the tilts
        aduse = np.zeros(arcdet.size, dtype=np.bool)  # Which lines should be used to trace the tilts
        w = np.where(nsig >= tracethresh)
        aduse[w] = 1
        # Remove lines that are within n_neigh pixels
        nuse = np.sum(aduse)
        detuse = arcdet[aduse]
        idxuse = np.arange(arcdet.size)[aduse]
        olduse = aduse.copy()
        for s in range(nuse):
            w = np.where((np.abs(arcdet - detuse[s]) <= n_neigh) & (np.abs(arcdet - detuse[s]) >= 1.0))[0]
            for u in range(w.size):
                if nsig[w[u]] > nsig[olduse][s]:
                    aduse[idxuse[s]] = False
                    break

        # Restricted to ID lines? [introduced to avoid LRIS ghosts]
        if only_these_lines is not None:
            ids_pix = np.array(only_these_lines)
            idxuse = np.arange(arcdet.size)[aduse]
            for s in idxuse:
                if np.min(np.abs(arcdet[s] - ids_pix)) > 2.0:
                    msgs.info("Ignoring line at spectral position={:6.1f} which was not identified".format(arcdet[s]))
                    aduse[s] = False

        # Final spectral positions of arc lines we will trace
        lines_spec = arcdet[aduse]
        nlines = len(lines_spec)
        if nlines == 0:
            msgs.warn('No arc lines were deemed usable on this slit. Cannot compute tilts. Try lowering tracethresh.')
            return None
        else:
            msgs.info('Modelling arc line tilts with {:d} arc lines'.format(nlines))


        slit_cen= (slit_left + slit_righ)/2.0
        slit_widp2  = int(np.ceil((slit_righ - slit_left).max()) + 2)
        trace_int_even = slit_widp2 if slit_widp2 % 2 == 0 else slit_widp2 + 1
        trace_int = trace_int_even//2
        nsub = 2*trace_int + 1

        lines_spat = np.interp(lines_spec, spec_vec, slit_cen)
        lines_spat_int =np.round(lines_spat).astype(int)

        tilts_sub = np.zeros((nsub, nlines))
        tilts_sub_err = np.zeros((nsub, nlines))
        tilts_sub_mask = np.zeros((nsub, nlines),dtype=bool)
        tilts_sub_spat = np.outer(np.arange(nsub), np.ones(nlines))
        tilts_sub_spec = np.outer(np.ones(nsub), lines_spec)

        tilts = np.zeros((nspat, nlines))
        tilts_err  = np.zeros((nspat, nlines))
        tilts_mask = np.zeros((nspat, nlines),dtype=bool) # This is true if the pixel was in a region traced
        tilts_spat = np.outer(np.arange(nspat), np.ones(nlines))
        tilts_spec = np.outer(np.ones(nspat), lines_spec)
        spat_min = np.zeros(nlines,dtype=int)
        spat_max = np.zeros(nlines,dtype=int)
        arcimg_trans = (arcimg * thismask).T
        inmask = (thismask.astype(float)).T
        ncoeff = 3

        for iline in range(nlines):
            spat_min[iline] = lines_spat_int[iline] - trace_int
            spat_max[iline] = lines_spat_int[iline] + trace_int + 1
            sub_img = arcimg_trans[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1),:]
            sub_inmask = inmask[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1),:]
            trace_out = trace_slits.trace_crude_init(sub_img, np.array([lines_spec[iline]]), (sub_img.shape[0]-1)//2,
                                                     invvar=sub_inmask, radius=2., maxshift0=3.0,
                                                     maxshift=3.0, maxerr=0.2)
            tilts_now, err_now = trace_out[0].flatten(), trace_out[1].flatten()
            # Deal with possibly falling off the chip
            if spat_min[iline] < 0:
                tilts_sub[-spat_min[iline]:, iline] = tilts_now
                tilts_sub_err[-spat_min[iline]:, iline] = err_now
                tilts_sub_mask[-spat_min[iline]:, iline] = True
                tilts[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1), iline] = tilts_sub[-spat_min[iline]:, iline]
                tilts_err[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1), iline] = tilts_sub_err[-spat_min[iline]:, iline]
                tilts_mask[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1), iline] = tilts_sub_mask[-spat_min[iline]:, iline]
            elif spat_max[iline] > (nspat-1):
                tilts_sub[:-(spat_max[iline]-nspat +1),iline] = tilts_now
                tilts_sub_err[:-(spat_max[iline]-nspat +1),iline] = err_now
                tilts_sub_mask[:-(spat_max[iline]-nspat +1),iline] = True
                tilts[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1), iline] = tilts_sub[:-(spat_max[iline]-nspat +1),iline]
                tilts_err[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1), iline] = tilts_sub_err[:-(spat_max[iline]-nspat +1),iline]
                tilts_mask[np.fmax(spat_min[iline],0):np.fmin(spat_max[iline],nspat-1), iline] = tilts_sub_mask[:-(spat_max[iline]-nspat +1),iline]
            else:
                tilts_sub[:, iline], tilts_sub_err[:, iline], tilts_sub_mask[:,iline] = tilts_now, err_now, True
                tilts[np.fmax(spat_min[iline], 0):np.fmin(spat_max[iline], nspat - 1), iline] = tilts_sub[:,iline]
                tilts_err[np.fmax(spat_min[iline], 0):np.fmin(spat_max[iline], nspat - 1), iline] = tilts_sub_err[:,iline]
                tilts_mask[np.fmax(spat_min[iline], 0):np.fmin(spat_max[iline], nspat - 1), iline] = tilts_sub_mask[:,iline]


        from IPython import embed
        embed()
        viewer, ch = ginga.show_image(arcimg*thismask,chname = 'Tilts')
        ginga.show_tilts(viewer, ch, tilts,tilts_spat, tilts_mask, tilts_err, sedges = (slit_left, slit_righ))


        # iteratively fit and flux weight
        xcen_fit, xcen_fweight = extract.iter_tracefit(msarc_trans, xcen_tcrude, ncoeff, inmask=inmask, fwhm=5.0,
                                                       maxiter=35, maxdev=1.0, niter=6, xmin=0.0, xmax=1.0)

        # stack the good traces to determine the average trace profile  which will be used as a crutch for tracing
        delta_fit = np.abs(xcen_fit - xcen_fweight)
        dev_mean, dev_median, dev_sig = sigma_clipped_stats(delta_fit, axis=0, sigma=4.0, mask=(xerr > 1100))
        err_max = 0.1
        ispat = np.round(spat_arcdet).astype(int)
        iline = np.arange(nlines, dtype=int)
        good_lines = (dev_median.data < err_max) & (np.abs(delta_fit[ispat, iline]) < err_max)
        # ToDO put in a check here on good_lines. If the number is too small, do something less agressive for choosing these

        viewer, ch = ginga.show_image(arcimg_trans)
        for iline in range(nlines):
            # ToDO modify ginga to take segments!!
            ginga.show_trace(viewer, ch, tilts[:, iline], color='green')

        # Compute the ensemble of tilts


        # delta_x =  xcen_fit[:,good_lines] - np.outer(np.ones(nspat), arcdet[good_lines]) - np.outer()
        # delta_x_mean, delta_x_median, delta_x_sig = sigma_clipped_stats(delta_x,axis=1, sigma = 4.0,mask = (xerr[:,good_lines] > 1100))

        # JFH-JXP code block ends here

        # JFH Code block ends here

        trcdict = tracewave.trace_tilt(self.tslits_dict['pixcen'], self.tslits_dict['lcen'],
                                       self.tslits_dict['rcen'], self.det, self.msarc, slit,
                                       nonlinear_counts, idsonly=self.par['idsonly'],
                                       censpec=self.arccen[:, slit], nsmth=3,
                                       tracethresh=tracethresh, wv_calib=wv_calib,
                                       nonlinear_counts = nonlinear_counts)
        # Load up
        self.all_trcdict[slit] = trcdict.copy()
        # Step
        self.steps.append(inspect.stack()[0][3])
        # Return
        return trcdict

    def run(self, maskslits=None, doqa=True, wv_calib=None, gen_satmask=False):
        """ Main driver for tracing arc lines

        Code flow:
           1.  Extract an arc spectrum down the center of each slit/order
           2.  Loop on slits/orders
             i.   Trace the arc lines (fweight is the default)
             ii.  Fit the individual arc lines
             iii.  2D Fit to the offset from pixcen
             iv. Save

        Parameters
        ----------
        maskslits : ndarray (bool), optional
        doqa : bool
        wv_calib : dict
        gen_satmask : bool, optional
          Generate a saturation mask?

        Returns
        -------
        self.final_tilts
        maskslits
        """
        # If the user sets no tilts, return here
        if self.par['method'].lower() == "zero":
            # Assuming there is no spectral tilt
            self.final_tilts = np.outer(np.linspace(0.0, 1.0, self.msarc.shape[0]), np.ones(self.msarc.shape[1]))
            return self.final_tilts, None, None

        if maskslits is None:
            maskslits = np.zeros(self.nslit, dtype=bool)

        self.slitmask = self.spectrograph.slitmask(self.tslits_dict)

        # Extract the arc spectra for all slits
        self.arccen, self.arc_maskslit = self._extract_arcs()

        # maskslit
        self.mask = maskslits & (self.arc_maskslit==1)
        gdslits = np.where(self.mask == 0)[0]

        # Final tilts image
        self.final_tilts = np.zeros_like(self.msarc)
        self.coeffs = np.zeros((self.par['order'] + 2,self.par['yorder'] +1,self.nslit))
        # Loop on all slits
        for slit in gdslits:
            # Trace
            _ = self._trace_tilts(slit, wv_calib=wv_calib)

            # Model line-by-line
            _ = self._analyze_lines(slit)

            # 2D model of the tilts
            #   Includes QA
            self.tilts, self.coeffs[:,:,slit] = self._fit_tilts(slit, doqa=doqa)

            # Save to final image
            word = self.slitmask == slit
            self.final_tilts[word] = self.tilts[word]

        self.tilts_dict = {'tilts':self.final_tilts, 'coeffs':self.coeffs, 'func2D':self.par['func2D']}
        return self.tilts_dict, maskslits

    def _qa(self, slit):
        """
        QA
          Wrapper to traceslits.slit_trace_qa()

        Parameters
        ----------
        slit : int

        Returns
        -------

        """
        self.tiltsplot, self.ztilto, self.xdat = tracewave.prep_tilts_qa(
            self.msarc, self.all_ttilts[slit], self.tilts, self.all_trcdict[slit]['arcdet'],
            self.pixcen, slit)

    def load_master(self, filename, exten = 0, force = False):


        # Does the master file exist?
        if not os.path.isfile(filename):
            msgs.warn("No Master frame found of type {:s}: {:s}".format(self.frametype, filename))
            if force:
                msgs.error("Crashing out because reduce-masters-force=True:" + msgs.newline() + filename)
            return None
        else:
            msgs.info("Loading a pre-existing master calibration frame of type: {:}".format(self.frametype) + " from filename: {:}".format(filename))
            hdu = fits.open(filename)
            head0 = hdu[0].header
            tilts = hdu[0].data
            head1 = hdu[1].header
            coeffs = hdu[1].data
            tilts_dict = {'tilts':tilts,'coeffs':coeffs,'func2D': head1['FUNC2D']} # This is the tilts_dict
            return tilts_dict #, head0, [filename]

    # JFH THis routine does not follow the current master protocol of taking a data argument. There is no reason to
    # save all this other information here
    def save_master(self, outfile=None):
        """

        Parameters
        ----------
        outfile
        use_tilts_as_final

        Returns
        -------

        """
        if outfile is None:
            outfile = self.ms_name
        #
        if self.final_tilts is None:
            msgs.warn("final_tilts not yet created.  Make it!")
            return
        #
        hdu0 = fits.PrimaryHDU(self.final_tilts)
        hdul = [hdu0]
        hdu_coeff = fits.ImageHDU(self.coeffs)
        hdu_coeff.header['FUNC2D'] = self.par['func2D']
        hdul.append(hdu_coeff)

        for slit in range(self.nslit):
            # Bad slit?
            if self.mask[slit]:
                continue
            # fweight and model
            xtfits = self.all_trcdict[slit]['xtfit']  # For convenience
            xszs = [len(xtfit) if xtfit is not None else 0 for xtfit in xtfits]
            maxx = np.max(xszs)
            # Add 1 to pack in ycen
            fwm_img = np.zeros((maxx+1, len(xtfits), 4)) - 9999999.9
            # Fill fweight and model
            model_cnt = 0
            for kk, xtfit in enumerate(xtfits):
                if xtfit is None:
                    continue
                #
                fwm_img[0:xszs[kk], kk, 0] = xtfit
                fwm_img[0:xszs[kk], kk, 1] = self.all_trcdict[slit]['ytfit'][kk]
                #
                if self.all_trcdict[slit]['aduse'][kk]:
                    szmod = self.all_trcdict[slit]['xmodel'][model_cnt].size # Slits on edge can be smaller
                    fwm_img[0:szmod, kk, 2] = self.all_trcdict[slit]['xmodel'][model_cnt]
                    fwm_img[0:szmod, kk, 3] = self.all_trcdict[slit]['ymodel'][model_cnt]
                    model_cnt += 1
                    # ycen
                    xgd = self.all_trcdict[slit]['xtfit'][kk][self.all_trcdict[slit]['xtfit'][kk].size//2]
                    ycen = self.all_ttilts[slit][1][int(xgd),kk]
                    fwm_img[-1, kk, 1] = ycen
            hdu1 = fits.ImageHDU(fwm_img)
            hdu1.name = 'FWM{:03d}'.format(slit)
            hdul.append(hdu1)
        # Finish
        hdulist = fits.HDUList(hdul)
        hdulist.writeto(outfile, clobber=True)

    def show(self, attr, slit=None, display='ginga', cname=None):
        """
        Display an image or spectrum in TraceSlits

        Parameters
        ----------
        attr : str
          'fweight'  -- Show the msarc image and the tilts traced by fweight
          'model'    -- Show the msarc image and the poylynomial model fits to the individual arc lines that
                        were traced by fweight.
          'arcmodel -- This illustrates the global final 2-d model fit to the indivdiaul models of each traced fweight arc line
                       tilts evaluated at the location of the specific arclines that wered use for the fit.
          'final_tilts' -- Show the final 2-d tilt model for all the slits that were fit.
        slit : int, optional
                    -- The slit to plot. This needs to be an integer between 1 and nslit
        display : str (optional)
          'ginga' -- Display to an RC Ginga
        """
        # ToDO I don't see why we are not looping over all slits for all of this. Why should we restrict to an individual fit?
        if (self.tslits_dict['lcen'] is not None) and (slit is not None):
            sedges=(self.tslits_dict['lcen'][:,slit], self.tslits_dict['rcen'][:,slit])
        else:
            sedges = None
        if attr == 'fweight':
            if slit is None:
                msgs.error("Need to provide the slit with this option")
            ginga.chk_arc_tilts(self.msarc, self.all_trcdict[slit],
                                sedges=sedges)
            msgs.info("Green = ok line;  red=not used")
        elif attr == 'model':
            if slit is None:
                msgs.error("Need to provide the slit with this option")
            tmp = self.all_trcdict[slit-1].copy()
            tmp['xtfit'] = self.all_trcdict[slit-1]['xmodel']
            tmp['ytfit'] = self.all_trcdict[slit-1]['ymodel']
            ginga.chk_arc_tilts(self.msarc, tmp, sedges=sedges, all_green=True)
        elif attr in ['arcmodel']:
            if slit is None:
                msgs.error("Need to provide the slit with this option")
            tmp = self.all_trcdict[slit].copy()
            tmp['xtfit'] = []
            tmp['ytfit'] = []

            ynorm = np.outer(np.linspace(0., 1., self.msarc.shape[0]), np.ones(self.msarc.shape[1]))
            polytilts = (ynorm-self.tilts)*(self.msarc.shape[0]-1)
            # arcdet is only the approximately nearest pixel (not even necessarily)
            for idx in np.where(self.all_trcdict[slit-1]['aduse'])[0]:
                xnow = np.arange(self.msarc.shape[1])
                if self.all_ttilts is not None:  # None if read from disk
                    xgd = self.all_trcdict[slit-1]['xtfit'][idx][self.all_trcdict[slit]['xtfit'][idx].size//2]
                    ycen = self.all_ttilts[slit-1][1][int(xgd),idx]
                else:
                    ycen = self.all_trcdict[slit-1]['ycen'][idx]
                ynow = ycen + polytilts[int(ycen),:]
                # Only plot the xnow, ynow values that are on this slit
                onslit = (slitmask[int(np.rint(xnow)),int(np.rint(ynow))]) == slit
                tmp['xtfit'].append(xnow[onslit])
                tmp['ytfit'].append(ynow[onslit])

            # Show
            msgs.warn("Display via tilts is not exact")  # Could make a correction.  Probably is close enough
            ginga.chk_arc_tilts(self.msarc, tmp, sedges=sedges, all_green=True, cname=cname)
        elif attr == 'final_tilts':
            if self.final_tilts is not None:
                ginga.show_image(self.final_tilts)
        else:
            msgs.error('Unrecognized attribute')

    def __repr__(self):
        # Generate sets string
        txt = '<{:s}: '.format(self.__class__.__name__)
        if len(self.steps) > 0:
            txt+= ' steps: ['
            for step in self.steps:
                txt += '{:s}, '.format(step)
            txt = txt[:-2]+']'  # Trim the trailing comma
        txt += '>'
        return txt

