"""
This module contains objects to characterize the pure-component adsorption
isotherms from experimental or simulated data. These will be fed into the
IAST functions in pyiast.py
"""
__author__ = 'Cory M. Simon'
__version__ = "2"
__all__ = ["ModelIsotherm", "InterpolatorIsotherm",
           "plot_isotherm", "_MODELS", "_MODEL_PARAMS"]

import scipy.optimize
from scipy.interpolate import interp1d
import numpy as np
import copy
import matplotlib.pyplot as plt
import pandas as pd

# ! list of models implemented in pyIAST
_MODELS = ["Langmuir", "Quadratic", "BET", "Sips", "DSLF"]

# ! dictionary of parameters involved in each model
_MODEL_PARAMS = {"Langmuir": {"M": np.nan, "K": np.nan},
                 "Quadratic": {"M": np.nan, "Ka": np.nan, "Kb": np.nan},
                 "BET": {"M": np.nan, "Ka": np.nan, "Kb": np.nan},
                 "Sips": {"M": np.nan, "K": np.nan, "n": np.nan},
                 "DSLF": {"M1": np.nan, "K1": np.nan, "n1": np.nan,
                          "M2": np.nan, "K2": np.nan, "n2": np.nan}
                 }


def get_default_guess_params(model, df, pressure_key, loading_key):
    """
    Get dictionary of default parameters for starting guesses in data fitting
    routine.

    The philosophy behind the default starting guess is that (1) the saturation
    loading is close to the highest loading observed in the data, and (2) the
    default is a Langmuir isotherm.

    :param model: String name of analytical model
    :param df: DataFrame adsorption isotherm data
    :param pressure_key: String key for pressure column in df
    :param loading_key: String key for loading column in df
    """
    # guess saturation loading to 10% more than highest loading
    saturation_loading = 1.1 * df[loading_key].max()
    # guess Langmuir K using the guess for saturation loading and lowest
    #   pressure point (but not zero)
    df_nonzero = df[df[loading_key] != 0.0]
    idx_min = df_nonzero[loading_key].argmin()
    langmuir_k = df_nonzero[loading_key].iloc[idx_min] /\
        df_nonzero[pressure_key].iloc[idx_min] / (
        saturation_loading - df_nonzero[pressure_key].iloc[idx_min])

    if model == "Langmuir":
        return {"M": saturation_loading, "K": langmuir_k}

    if model == "Quadratic":
        # Quadratic = Langmuir when Kb = Ka^2. This is our default assumption.
        # Also, M is half of the saturation loading in the Quadratic model.
        return {"M": saturation_loading / 2.0, "Ka": langmuir_k,
                "Kb": langmuir_k ** 2.0}

    if model == "BET":
        # BET = Langmuir when Kb = 0.0. This is our default assumption.
        return {"M": saturation_loading, "Ka": langmuir_k,
                "Kb": langmuir_k * 0.01}

    if model == "Sips":
        return {"M": saturation_loading, "K": langmuir_k, "n": 1.0}

    if model == "DSLF":
        return {"M1": saturation_loading, "K1": langmuir_k, "n1": 1.0,
                "M2": 0.01 * saturation_loading, "K2": langmuir_k, "n2": 1.0}


class ModelIsotherm:
    """
    Class to characterize pure-component isotherm data with an analytical model.

    Models supported are as follows. Here, :math:`L` is the gas uptake,
    :math:`P` is pressure (fugacity technically).

    * Langmuir isotherm model

    .. math::

        L(P) = M\\frac{KP}{1+KP},

    * Quadratic isotherm model
    .. math::

        L(P) = M \\frac{(K_a + 2 K_b P)P}{1+K_aP+K_bP^2}

    * Brunauer-Emmett-Teller (BET) adsorption isotherm

    .. math::

        L(P) = M\\frac{K_A P}{(1-K_B P)(1-K_B P+ K_A P)}

    * Sips adsorption isotherm

    .. math::

        L(P) = M\\frac{K^nP^n}{1+K^nP^n}

    * Dual-site Langmuir-Fruendlich (DSLF) adsorption isotherm

    .. math::

        L(P) = M_1\\frac{(K_1 P)^{n_1}}{1+(K_1 P)^{n_1}} +  M_2\\frac{(K_2 P)^{n_2}}{1+(K_2 P)^{n_2}}

    """

    def __init__(self, df, loading_key=None, pressure_key=None, model=None,
                 param_guess=None, optimization_method="Nelder-Mead"):
        """
        Instantiation. A ModelIsotherm class is instantiated by passing it the
        pure-component adsorption isotherm in the form of a Pandas DataFrame.
        The least squares data fitting is done here.

        :param df: DataFrame adsorption isotherm data
        :param loading_key: String key for loading column in df
        :param pressure_key: String key for pressure column in df
        :param param_guess: Dict starting guess for model parameters using
            fitting routine
        :param optimization_method: String method in SciPy minimization function
            to use in fitting model to data.
            See [here](http://docs.scipy.org/doc/scipy/reference/optimize.html#module-scipy.optimize).

        :return: self
        :rtype: ModelIsotherm
        """
        if model is None:
            raise Exception("Specify a model to fit to the pure-component"
                            " isotherm data. e.g. model=\"Langmuir\"")
        if model not in _MODELS:
            raise Exception("Model %s not an option in pyIAST. See viable"
                            "models with pyiast._MODELS" % model)

        #: Name of analytical odel to fit to data to characterize pure-component
        #: adsorption isotherm
        self.model = model

        #: Pandas DataFrame on which isotherm was fit
        self.df = df
        if None in [loading_key, pressure_key]:
            raise Exception(
            "Pass loading_key and pressure_key, the names of the loading and"
            " pressure columns in the DataFrame, to the constructor.")
        #: name of loading column
        self.loading_key = loading_key
        #: name of pressure column
        self.pressure_key = pressure_key

        #! root mean square error in fit
        self.rmse = np.nan

        # Guess parameters as a starting point in minimizing RSS
        self.param_guess = get_default_guess_params(model, df, pressure_key,
                                                    loading_key)
        # Override defaults if user provides param_guess dictionary
        if param_guess is not None:
            for param, guess_val in param_guess.iteritems():
                if param not in self.param_guess.keys():
                    raise Exception("%s is not a valid parameter"
                                    " in the %s model." % (param, model))
                self.param_guess[param] = guess_val

        # initialize params as nan
        self.params = copy.deepcopy(_MODEL_PARAMS[model])

        # fit model to isotherm data in self.df
        self._fit(optimization_method)

    def loading(self, pressure):
        """
        Given stored model parameters, compute loading at pressure P.

        :param pressure: Float or Array pressure (in corresponding units as df
            in instantiation)
        :return: loading at pressure P (in corresponding units as df in
            instantiation)
        :rtype: Float or Array
        """
        if self.model == "Langmuir":
            return self.params["M"] * self.params["K"] * pressure /\
                (1.0 + self.params["K"] * pressure)

        if self.model == "Quadratic":
            return self.params["M"] * (self.params["Ka"] +\
                2.0 * self.params["Kb"] * pressure) * pressure / (
                1.0 + self.params["Ka"] * pressure +
                self.params["Kb"] * pressure ** 2)

        if self.model == "BET":
            return self.params["M"] * self.params["Ka"] * pressure / (
                (1.0 - self.params["Kb"] * pressure) *
                (1.0 - self.params["Kb"] * pressure +
                self.params["Ka"] * pressure))

        if self.model == "Sips":
            return self.params["M"] * (self.params["K"] * pressure) **\
                self.params["n"] / (
                1.0 + (self.params["K"] * pressure) ** self.params["n"])

        if self.model == "DSLF":
            # (K_i P) ^ n_i
            k1p_n1 = (self.params["K1"] * pressure) ** self.params["n1"]
            k2p_n2 = (self.params["K2"] * pressure) ** self.params["n2"]
            return self.params["M1"] * k1p_n1 / (1.0 + k1p_n1) +\
                self.params["M2"] * k2p_n2 / (1.0 + k2p_n2)

    def _fit(self, optimization_method):
        """
        Fit model to data using nonlinear optimization with least squares loss
            function. Assigns params to self.

        :param K_guess: float guess Langmuir constant (units: 1/pressure)
        :param M_guess: float guess saturation loading (units: loading)
        """
        # parameter names (cannot rely on order in Dict)
        param_names = [param for param in self.params.keys()]
        # guess
        guess = np.array([self.param_guess[param] for param in param_names])

        def residual_sum_of_squares(params_):
            """
            Residual Sum of Squares between model and data in df
            :param params_: Array of parameters
            """
            # change params to those in x
            for i in range(len(param_names)):
                self.params[param_names[i]] = params_[i]

            return np.sum((self.df[self.loading_key].values -
                           self.loading(self.df[self.pressure_key].values))** 2)

        # minimize RSS
        opt_res = scipy.optimize.minimize(residual_sum_of_squares, guess,
                                          method=optimization_method)
        if not opt_res.success:
            print opt_res.message
            raise Exception("""Minimization of RSS for %s isotherm fitting
            failed. Try a different starting point in the nonlinear optimization
            by passing a dictionary of parameter guesses, param_guess, to the
            constructor""" % self.model)

        # assign params
        for j in range(len(param_names)):
            self.params[param_names[j]] = opt_res.x[j]

        self.rmse = np.sqrt(opt_res.fun / self.df.shape[0])

    def spreading_pressure(self, pressure):
        """
        Calculate reduced spreading pressure at a bulk gas pressure P.

        :param pressure: float pressure (in corresponding units as df in
            instantiation)
        :return: spreading pressure, :math:`\\Pi`
        :rtype: Float
        """
        if self.model == "Langmuir":
            return self.params["M"] * np.log(1.0 + self.params["K"] * pressure)

        if self.model == "Quadratic":
            return self.params["M"] * np.log(1.0 + self.params["Ka"] * pressure +
                self.params["Kb"] * pressure ** 2)

        if self.model == "BET":
            return self.params["M"] * np.log(
                (1.0 - self.params["Kb"] * pressure +
                self.params["Ka"] * pressure) /
                (1.0 - self.params["Kb"] * pressure))

        if self.model == "Sips":
            return self.params["M"] / self.params["n"] * np.log(1.0 +
                (self.params["K"] * pressure) ** self.params["n"])

        if self.model == "DSLF":
            return self.params["M1"] / self.params["n1"] * np.log(
                1.0 + (self.params["K1"] * pressure) ** self.params["n1"]
            ) + self.params["M2"] / self.params["n2"] * np.log(
                1.0 + (self.params["K2"] * pressure) ** self.params["n2"]
            )

    def print_params(self):
        """
        Print identified model parameters
        """
        print "%s identified model parameters:" % self.model
        for param, val in self.params.iteritems():
            print "\t%s = %f" % (param, val)
        print "RMSE = ", self.rmse


class InterpolatorIsotherm:
    """
    Interpolator isotherm object to store pure-component adsorption isotherm.

    Here, the isotherm is characterized by linear interpolation of data.

    Loading = 0.0 at pressure = 0.0 is enforced here automatically for
    interpolation at low pressures.

    Default for extrapolating isotherm beyond highest pressure in available data
    is to throw an exception. Pass a value for `fill_value` in instantiation to extrapolate loading as `fill_value`.
    """

    def __init__(self, df, loading_key=None, pressure_key=None, fill_value=None):
        """
        Instantiation. InterpolatorIsotherm is instantiated by passing it the
        pure-component adsorption isotherm in the form of a Pandas DataFrame.
        Contructs linear interpolator from `interp1d` function in Scipy during
        instantiation.

        e.g. to extrapolate loading beyond highest pressure point as 100.0,
        pass `fill_value=100.0`.

        :param df: DataFrame adsorption isotherm data
        :param loading_key: String key for loading column in df
        :param pressure_key: String key for pressure column in df
        :param fill_value: Float value of loading to assume when an attempt is
            made to interpolate at a pressure greater than the largest pressure
            observed in the data

        :return: self
        :rtype: InterpolatorIsotherm
        """
        # if pressure = 0 not in data frame, add it for interpolation between
        #   p = 0 and the lowest, nonzero pressure point.
        if 0.0 not in df[pressure_key].values:
            df = pd.concat([pd.DataFrame({pressure_key: 0.0, loading_key: 0.0},
                                         index=[0]), df])

        # store isotherm data in self
        #: Pandas DataFrame on which isotherm was fit
        self.df = df.sort([pressure_key], ascending=True)
        if None in [loading_key, pressure_key]:
            raise Exception("Pass loading_key and pressure_key, names of "
                            "loading and pressure cols in DataFrame, to "
                            "constructor.")
        #: name of loading column
        self.loading_key = loading_key
        #: name of pressure column
        self.pressure_key = pressure_key

        if fill_value is None:
            self.interp1d = interp1d(self.df[pressure_key],
                self.df[loading_key])
        else:
            self.interp1d = interp1d(self.df[pressure_key],
                self.df[loading_key], fill_value=fill_value, bounds_error=False)
        #: value of loading to assume beyond highest pressure in the data
        self.fill_value = fill_value

    def loading(self, pressure):
        """
        Linearly interpolate isotherm to compute loading at pressure P.

        :param pressure: float pressure (in corresponding units as df in
            instantiation)
        :return: loading at pressure P (in corresponding units as df in
            instantiation)
        :rtype: Float or Array
        """
        return self.interp1d(pressure)

    def spreading_pressure(self, pressure):
        """
        Calculate reduced spreading pressure at a bulk gas pressure P.
        (see Tarafder eqn 4)

        Use numerical quadrature on isotherm data points to compute the reduced
        spreading pressure via the integral:

        .. math::

            \\Pi(p) = \\int_0^p \\frac{q(\\hat{p})}{ \\hat{p}} d\\hat{p}.

        In this integral, the isotherm :math:`q(\\hat{p})` is represented by a
        linear interpolation of the data.

        :param pressure: float pressure (in corresponding units as df in
            instantiation)
        :return: spreading pressure, :math:`\\Pi`
        :rtype: Float
        """
        # throw exception if interpolating outside the range.
        if (self.fill_value == None) &\
           (pressure > self.df[self.pressure_key].max()):
            raise Exception("""To compute the spreading pressure at this bulk
            gas pressure, we would need to extrapolate the isotherm since this
            pressure is outside the range of the highest pressure in your
            pure-component isotherm data, %f.

            At present, your InterpolatorIsotherm object is set to throw an
            exception when this occurs, as we do not have data outside this
            pressure range to characterize the isotherm at higher pressures.

            Option 1: fit an analytical model to extrapolate the isotherm
            Option 2: pass a `fill_value` to the construction of the
                InterpolatorIsotherm object. Then, InterpolatorIsotherm will
                assume that the uptake beyond pressure %f is equal to
                `fill_value`. This is reasonable if your isotherm data exhibits
                a plateau at the highest pressures.
            Option 3: Go back to the lab or computer to collect isotherm data
                at higher pressures. (Extrapolation can be dangerous!)"""
            % (self.df[self.pressure_key].max(),
               self.df[self.pressure_key].max()))

        # Get all data points that are at nonzero pressures
        pressures = self.df[self.pressure_key].values[
            self.df[self.pressure_key].values != 0.0]
        loadings = self.df[self.loading_key].values[
            self.df[self.pressure_key].values != 0.0]

        # approximate loading up to first pressure point with Henry's law
        # loading = henry_const * P
        # henry_const is the initial slope in the adsorption isotherm
        henry_const = loadings[0] / pressures[0]

        # get how many of the points are less than pressure P
        n_points = np.sum(pressures < pressure)

        if n_points == 0:
            # if this pressure is between 0 and first pressure point...
            return henry_const * pressure  # \int_0^P henry_const P /P dP = henry_const * P ...
        else:
            # P > first pressure point
            area = loadings[0]  # area of first segment \int_0^P_1 n(P)/P dP

            # get area between P_1 and P_k, where P_k < P < P_{k+1}
            for i in range(n_points - 1):
                # linear interpolation of isotherm data
                slope = (loadings[i + 1] - loadings[i]) / (pressures[i + 1] -\
                    pressures[i])
                intercept = loadings[i] - slope * pressures[i]
                # add area of this segment
                area += slope * (pressures[i + 1] - pressures[i]) + intercept *\
                    np.log(pressures[i + 1] / pressures[i])

            # finally, area of last segment
            slope = (self.loading(pressure) - loadings[n_points - 1]) / (
                pressure - pressures[n_points - 1])
            intercept = loadings[n_points - 1] - slope * pressures[n_points - 1]
            area += slope * (pressure - pressures[n_points - 1]) + intercept *\
                np.log(pressure / pressures[n_points - 1])

            return area


def plot_isotherm(isotherm, withfit=True, xlogscale=False,
                  ylogscale=False, pressure=None):
    """
    Plot isotherm data and fit using Matplotlib.

    :param isotherm: pyIAST isotherm object
    :param withfit: Bool plot fit as well
    :param pressure: numpy.array optional pressure array to pass for plotting
    :param xlogscale: Bool log-scale on x-axis
    :param ylogscale: Bool log-scale on y-axis
    """

    plt.figure()
    if withfit:
        # array of pressures to plot model
        if pressure is None:
            if xlogscale:
                # do not include zero for log-scale
                idx = isotherm.df[isotherm.pressure_key].values != 0.0
                min_p = np.min(isotherm.df[isotherm.pressure_key].iloc[idx])
                pressure = np.logspace(np.log(min_p), np.log(isotherm.df[
                    isotherm.pressure_key].max()), 200)
            else:
                pressure = np.linspace(isotherm.df[isotherm.pressure_key].min(),
                                       isotherm.df[isotherm.pressure_key].max(),
                                       200)
        plt.plot(pressure, isotherm.loading(pressure))
    plt.scatter(isotherm.df[isotherm.pressure_key], isotherm.df[
        isotherm.loading_key])
    if xlogscale:
        plt.xscale("log")
    if ylogscale:
        plt.yscale("log")
    plt.xlim(xmin=0.0)
    plt.ylim(ymin=0.0)
    plt.xlabel('Pressure')
    plt.ylabel('Loading')
    plt.show()


def print_selectivity(component_loadings, partial_pressures):
    """
    Calculate selectivity as a function of component loadings and bulk gas
    pressures

    :param component_loadings: numpy array of component loadings
    :param partial_pressures: partial pressures of components
    """
    n = np.size(component_loadings)
    for i in range(n):
        for j in range(i + 1, n):
            print "Selectivity for component %d over %d = %f" % (i, j,
                    component_loadings[i] / component_loadings[j] /\
                    (partial_pressures[i] / partial_pressures[j]))
