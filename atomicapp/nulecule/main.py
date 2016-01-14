# -*- coding: utf-8 -*-
import anymarkup
import copy
import distutils.dir_util
import logging
import os
import tempfile

from atomicapp.constants import (GLOBAL_CONF,
                                 ANSWERS_FILE_SAMPLE_FORMAT,
                                 ANSWERS_FILE,
                                 ANSWERS_FILE_SAMPLE,
                                 ANSWERS_RUNTIME_FILE,
                                 DEFAULT_ANSWERS,
                                 DEFAULT_NAMESPACE,
                                 MAIN_FILE,
                                 NAMESPACE_KEY,
                                 PROVIDER_KEY)
from atomicapp.nulecule.base import Nulecule
from atomicapp.nulecule.exceptions import NuleculeException
from atomicapp.utils import Utils

logger = logging.getLogger(__name__)


class NuleculeManager(object):

    """
    Interface to install, run, stop a Nulecule application.
    """

    def __init__(self, app_spec, destination=None,
                 cli_answers=None, answers_file=None):
        """
        init function for NuleculeManager. Sets a few instance variables.

        Args:
            app_spec: either a path to an unpacked nulecule app or a
                      container image name where a nulecule can be found
            destination: where to unpack a nulecule to if it isn't local
            cli_answers: some answer file values provided from cli args
            answers_file: the location of the answers file
        """
        self.answers = copy.deepcopy(DEFAULT_ANSWERS)
        self.cli_answers = cli_answers
        self.answers_format = None
        self.answers_file = None  # The path to an answer file
        self.app_path = None  # The path where the app resides or will reside
        self.image = None     # The container image to pull the app from

        # Adjust app_spec, destination, and answer file paths if absolute.
        if os.path.isabs(app_spec):
            app_spec = os.path.join(Utils.getRoot(),
                                    app_spec.lstrip('/'))
        if destination and os.path.isabs(destination):
            destination = os.path.join(Utils.getRoot(),
                                       destination.lstrip('/'))
        if answers_file and os.path.isabs(answers_file):
            answers_file = os.path.join(Utils.getRoot(),
                                        answers_file.lstrip('/'))

        # If the user doesn't want the files copied to a permanent
        # location then he provides 'none'. If that is the case we'll
        # use a temporary directory
        if destination and destination.lower() == 'none':
            logger.debug("'none' destination requested. Using tmp dir")
            destination = tempfile.mkdtemp(prefix='atomicapp')

        # Determine if the user passed us an image or a path to an app
        if not os.path.exists(app_spec):
            self.image = app_spec
        else:
            self.app_path = app_spec

        # Doesn't really make much sense to provide an app path and destination,
        # but if they want to we'll simply just copy the files for them
        if self.app_path and destination:
            Utils.copy_dir(self.app_path, destination, update=True)
            self.app_path = destination

        # If the user provided an image, make sure we have a destination
        if self.image:
            if destination:
                self.app_path = destination
            else:
                self.app_path = Utils.getNewAppCacheDir(self.image)

        logger.debug("NuleculeManager init app_path: %s", self.app_path)
        logger.debug("NuleculeManager init    image: %s", self.image)

        # Set where the main nulecule file should be
        self.main_file = os.path.join(self.app_path, MAIN_FILE)

        # If user provided a path to answers, then load them
        if answers_file:
            if not os.path.isfile(answers_file):
                raise NuleculeException(
                    "Answers file doesn't exist: {}".format(answers_file))
            self.answers_file = answers_file
        self._process_answers()

        # TODO: put this in a better place in the future.
        # If we are running inside of an openshift pod then override
        # some of the config by detecting some values from the environment
        if Utils.running_on_openshift():
            self.answers[GLOBAL_CONF]['provider'] = 'openshift'
            self.answers[GLOBAL_CONF]['accesstoken'] = os.environ['TOKEN_ENV_VAR']
            self.answers[GLOBAL_CONF]['namespace'] = os.environ['POD_NAMESPACE']
            self.answers[GLOBAL_CONF]['providerapi'] = \
                Utils.get_openshift_api_endpoint_from_env()

    def unpack(self, update=False,
               dryrun=False, nodeps=False, config=None):
        """
        Unpacks a Nulecule application from a Nulecule image to a path
        or load a Nulecule that already exists locally.

        Args:
            update (bool): Update existing Nulecule application in
                           app_path, if True
            dryrun (bool): Do not make any change to the host system
            nodeps (bool): Do not unpack external dependencies
            config (dict): Config data, if any, to use for unpacking

        Returns:
            A Nulecule instance.
        """
        logger.debug('Request to unpack to %s to %s' %
                     (self.image, self.app_path))

        # If the user provided an image then unpack it and return the
        # resulting Nulecule. Else, load from existing path
        if self.image:
            return Nulecule.unpack(
                self.image, self.app_path, config=config,
                nodeps=nodeps, dryrun=dryrun, update=update)
        else:
            return Nulecule.load_from_path(
                self.app_path, dryrun=dryrun, config=config)

    def genanswers(self, dryrun=False, answers_format=None, **kwargs):
        """
        Renders artifacts and then generates an answer file. Finally
        copies answer file to the current working directory.

        Args:
            dryrun (bool): Do not make any change to the host system if True
            answers_format (str): File format for writing sample answers file
            kwargs (dict): Extra keyword arguments

        Returns:
            None
        """
        self.answers_format = answers_format or ANSWERS_FILE_SAMPLE_FORMAT

        # Check to make sure an answers.conf file doesn't exist already
        answers_file = os.path.join(os.getcwd(), ANSWERS_FILE)
        if os.path.exists(answers_file):
            raise NuleculeException(
                "Can't generate answers.conf over existing file")

        # Call unpack to get the app code
        self.nulecule = self.unpack(update=False, dryrun=dryrun, config=self.answers)

        self.nulecule.load_config(config=self.nulecule.config,
                                  skip_asking=True)
        # Get answers and write them out to answers.conf in cwd
        answers = self._get_runtime_answers(
            self.nulecule.config, None)
        self._write_answers(answers_file, answers, answers_format)

    def install(self, nodeps=False, update=False, dryrun=False,
                answers_format=ANSWERS_FILE_SAMPLE_FORMAT, **kwargs):
        """
        Installs (unpacks) a Nulecule application from a Nulecule image
        to a target path.

        Args:
            answers (dict or str): Answers data or local path to answers file
            nodeps (bool): Install the nulecule application without installing
                           external dependencies
            update (bool): Pull requisite Nulecule image and install or
                           update already installed Nulecule application
            dryrun (bool): Do not make any change to the host system if True
            answers_format (str): File format for writing sample answers file
            kwargs (dict): Extra keyword arguments

        Returns:
            None
        """
        self.answers_format = answers_format or ANSWERS_FILE_SAMPLE_FORMAT

        # Call unpack. If the app doesn't exist it will be pulled. If
        # it does exist it will be just be loaded and returned
        self.nulecule = self.unpack(update, dryrun, config=self.answers)

        self.nulecule.load_config(config=self.nulecule.config,
                                  skip_asking=True)
        runtime_answers = self._get_runtime_answers(
            self.nulecule.config, None)
        # write sample answers file
        self._write_answers(
            os.path.join(self.app_path, ANSWERS_FILE_SAMPLE),
            runtime_answers, answers_format)

    def run(self, cli_provider, answers_output, ask,
            answers_format=ANSWERS_FILE_SAMPLE_FORMAT, **kwargs):
        """
        Runs a Nulecule application from a local path or a Nulecule image
        name.

        Args:
            answers (dict or str): Answers data or local path to answers file
            cli_provider (str): Provider to use to run the Nulecule
                                application
            answers_output (str): Path to file to export runtime answers data
                                  to
            ask (bool): Ask for values for params with default values from
                        user, if True
            answers_format (str): File format for writing sample answers file
            kwargs (dict): Extra keyword arguments

        Returns:
            None
        """
        self.answers_format = answers_format or ANSWERS_FILE_SAMPLE_FORMAT
        dryrun = kwargs.get('dryrun') or False

        # Call unpack. If the app doesn't exist it will be pulled. If
        # it does exist it will be just be loaded and returned
        self.nulecule = self.unpack(dryrun=dryrun, config=self.answers)

        # If we didn't find an answers file before then call _process_answers
        # again just in case the app developer embedded an answers file
        if not self.answers_file:
            self._process_answers()

        # Unless otherwise specified with CLI arguments we will
        # default to the first provider available
        providers = Utils.getSupportedProviders(self.app_path)
        if cli_provider is None and len(providers) == 1:
            self.answers[GLOBAL_CONF][PROVIDER_KEY] = providers[0]

        self.nulecule.load_config(config=self.nulecule.config, ask=ask)
        self.nulecule.render(cli_provider, dryrun)
        self.nulecule.run(cli_provider, dryrun)
        runtime_answers = self._get_runtime_answers(
            self.nulecule.config, cli_provider)
        self._write_answers(
            os.path.join(self.app_path, ANSWERS_RUNTIME_FILE),
            runtime_answers, self.answers_format)
        if answers_output:
            self._write_answers(answers_output, runtime_answers,
                                self.answers_format)

    def stop(self, cli_provider, **kwargs):
        """
        Stops a running Nulecule application.

        Args:
            cli_provider (str): Provider running the Nulecule application
            kwargs (dict): Extra keyword arguments
        """
        # For stop we use the generated answer file from the run
        self.answers_file = os.path.join(self.app_path, ANSWERS_RUNTIME_FILE)
        self._process_answers()

        dryrun = kwargs.get('dryrun') or False
        self.nulecule = Nulecule.load_from_path(
            self.app_path, config=self.answers, dryrun=dryrun)
        self.nulecule.load_config(config=self.answers)
        self.nulecule.render(cli_provider, dryrun=dryrun)
        self.nulecule.stop(cli_provider, dryrun)

    def uninstall(self):
        # For future use
        self.stop()
        self.nulecule.uninstall()

    def clean(self, force=False):
        # For future use
        self.uninstall()
        distutils.dir_util.remove_tree(self.unpack_path)
        self.initialize()

    def _process_answers(self):
        """
        Processes answer files to load data from them and then merges
        any cli provided answers into the config.

        NOTE: This function should be called once on startup and then
        once more after the application has been extracted, but only
        if answers file wasn't found on the first invocation. The idea
        is to allow for people to embed an answers file in the application
        if they want, which won't be available until after extraction.

        Returns:
            None
        """

        # If the user didn't provide an answers file then check the app
        # dir to see if one exists.
        if not self.answers_file:
            f = os.path.join(self.app_path, ANSWERS_FILE)
            if os.path.isfile(f):
                self.answers_file = f

        # At this point if we have an answers file, load it
        if self.answers_file:
            self.answers = Utils.loadAnswers(self.answers_file)

        # If there is answers data from the cli then merge it in now
        if self.cli_answers:
            for k, v in self.cli_answers.iteritems():
                self.answers[GLOBAL_CONF][k] = v

    def _write_answers(self, path, answers, answers_format):
        """
        Write answers data to file.

        Args:
            path (str): path to answers file to write to
            answers (dict): Answers data
            answers_format (str): Format to use to dump answers data to file,
                                  e.g., json
        Returns:
            None
        """
        logger.debug("Writing answers to file.")
        logger.debug("FILE: %s", path)
        logger.debug("ANSWERS: %s", answers)
        anymarkup.serialize_file(answers, path, format=answers_format)

    def _get_runtime_answers(self, config, cli_provider):
        """
        Get runtime answers data from config (Nulecule config) by adding
        default data if missing.

        Args:
            config (dict): Nulecule config data
            cli_provider (str): Provider used for running Nulecule application

        Returns:
            dict
        """
        _config = copy.deepcopy(config)
        _config[GLOBAL_CONF] = config.get(GLOBAL_CONF) or {}
        _config[GLOBAL_CONF][NAMESPACE_KEY] = _config[GLOBAL_CONF].get(
            NAMESPACE_KEY) or DEFAULT_NAMESPACE
        # If a provider is provided via CLI, override the config parameter
        if cli_provider:
            _config[GLOBAL_CONF][PROVIDER_KEY] = cli_provider

        return _config
