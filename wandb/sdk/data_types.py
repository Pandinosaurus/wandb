import hashlib
import os
import shutil

import wandb
from wandb import util
from wandb._globals import _datatypes_callback

if wandb.TYPE_CHECKING:
    from typing import (
        TYPE_CHECKING,
        ClassVar,
        Dict,
        Optional,
        Type,
        Union,
        Sequence,
        Tuple,
        List,
    )

    if TYPE_CHECKING:
        from .wandb_artifacts import Artifact as LocalArtifact
        from .wandb_run import Run as LocalRun
        from wandb.apis.public import Artifact as PublicArtifact
        from numpy import np  # type: ignore

        TypeMappingType = Dict[str, Type["WBValue"]]
        NumpyHistogram = Tuple[np.ndarray, np.ndarray]


def _safe_sdk_import() -> Tuple[Type["LocalRun"], Type["LocalArtifact"]]:
    """Safely import due to circular deps"""

    from .wandb_artifacts import Artifact as LocalArtifact
    from .wandb_run import Run as LocalRun

    return LocalRun, LocalArtifact


class _WBValueArtifactSource(object):
    artifact: "PublicArtifact"
    name: Optional[str]

    def __init__(self, artifact: "PublicArtifact", name: str = None) -> None:
        self.artifact = artifact
        self.name = name


class WBValue(object):
    """
    Abstract parent class for things that can be logged by `wandb.log()` and
    visualized by wandb.

    The objects will be serialized as JSON and always have a _type attribute
    that indicates how to interpret the other fields.
    """

    # Class Attributes
    _type_mapping: ClassVar[Optional["TypeMappingType"]] = None
    # override artifact_type to indicate the type which the subclass deserializes
    artifact_type: ClassVar[Optional[str]] = None

    # Instance Attributes
    artifact_source: Optional[_WBValueArtifactSource]

    def __init__(self) -> None:
        self.artifact_source = None

    def to_json(self, run_or_artifact: Union["LocalRun", "LocalArtifact"]) -> dict:
        """Serializes the object into a JSON blob, using a run or artifact to store additional data.

        Args:
            run_or_artifact (wandb.Run | wandb.Artifact): the Run or Artifact for which this object should be generating
            JSON for - this is useful to to store additional data if needed.

        Returns:
            dict: JSON representation
        """
        raise NotImplementedError

    @classmethod
    def from_json(
        cls: Type["WBValue"], json_obj: dict, source_artifact: "PublicArtifact"
    ) -> "WBValue":
        """Deserialize a `json_obj` into it's class representation. If additional resources were stored in the
        `run_or_artifact` artifact during the `to_json` call, then those resources are expected to be in
        the `source_artifact`.

        Args:
            json_obj (dict): A JSON dictionary to deserialize
            source_artifact (wandb.Artifact): An artifact which will hold any additional resources which were stored
            during the `to_json` function.
        """
        raise NotImplementedError

    @classmethod
    def with_suffix(cls: Type["WBValue"], name: str, filetype: str = "json") -> str:
        """Helper function to return the name with suffix added if not already

        Args:
            name (str): the name of the file
            filetype (str, optional): the filetype to use. Defaults to "json".

        Returns:
            str: a filename which is suffixed with it's `artifact_type` followed by the filetype
        """
        if cls.artifact_type is not None:
            suffix = cls.artifact_type + "." + filetype
        else:
            suffix = filetype
        if not name.endswith(suffix):
            return name + "." + suffix
        return name

    @staticmethod
    def init_from_json(
        json_obj: dict, source_artifact: "PublicArtifact"
    ) -> "Optional[WBValue]":
        """Looks through all subclasses and tries to match the json obj with the class which created it. It will then
        call that subclass' `from_json` method. Importantly, this function will set the return object's `source_artifact`
        attribute to the passed in source artifact. This is critical for artifact bookkeeping. If you choose to create
        a wandb.Value via it's `from_json` method, make sure to properly set this `artifact_source` to avoid data duplication.

        Args:
            json_obj (dict): A JSON dictionary to deserialize. It must contain a `_type` key. The value of
            this key is used to lookup the correct subclass to use.
            source_artifact (wandb.Artifact): An artifact which will hold any additional resources which were stored
            during the `to_json` function.

        Returns:
            wandb.Value: a newly created instance of a subclass of wandb.Value
        """
        class_option = WBValue.type_mapping().get(json_obj["_type"])
        if class_option is not None:
            obj = class_option.from_json(json_obj, source_artifact)
            obj.set_artifact_source(source_artifact)
            return obj

        return None

    @staticmethod
    def type_mapping() -> "TypeMappingType":
        """Returns a map from `artifact_type` to subclass. Used to lookup correct types for deserialization.

        Returns:
            dict: dictionary of str:class
        """
        if WBValue._type_mapping is None:
            WBValue._type_mapping = {}
            frontier = [WBValue]
            explored = set([])
            while len(frontier) > 0:
                class_option = frontier.pop()
                explored.add(class_option)
                if class_option.artifact_type is not None:
                    WBValue._type_mapping[class_option.artifact_type] = class_option
                for subclass in class_option.__subclasses__():
                    if subclass not in explored:
                        frontier.append(subclass)
        return WBValue._type_mapping

    def __eq__(self, other: object) -> bool:
        return id(self) == id(other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def set_artifact_source(self, artifact: "PublicArtifact", name: str = None) -> None:
        self.artifact_source = _WBValueArtifactSource(artifact, name)


class Histogram(WBValue):
    """wandb class for histograms.

    This object works just like numpy's histogram function
    https://docs.scipy.org/doc/numpy/reference/generated/numpy.histogram.html

    Examples:
        Generate histogram from a sequence
        ```
        wandb.Histogram([1,2,3])
        ```

        Efficiently initialize from np.histogram.
        ```
        hist = np.histogram(data)
        wandb.Histogram(np_histogram=hist)
        ```

    Arguments:
        sequence: (array_like) input data for histogram
        np_histogram: (numpy histogram) alternative input of a precoomputed histogram
        num_bins: (int) Number of bins for the histogram.  The default number of bins
            is 64.  The maximum number of bins is 512

    Attributes:
        bins: ([float]) edges of bins
        histogram: ([int]) number of elements falling in each bin
    """

    MAX_LENGTH: int = 512

    def __init__(
        self,
        sequence: Optional[Sequence] = None,
        np_histogram: Optional["NumpyHistogram"] = None,
        num_bins: int = 64,
    ) -> None:

        if np_histogram:
            if len(np_histogram) == 2:
                self.histogram = (
                    np_histogram[0].tolist()
                    if hasattr(np_histogram[0], "tolist")
                    else np_histogram[0]
                )
                self.bins = (
                    np_histogram[1].tolist()
                    if hasattr(np_histogram[1], "tolist")
                    else np_histogram[1]
                )
            else:
                raise ValueError(
                    "Expected np_histogram to be a tuple of (values, bin_edges) or sequence to be specified"
                )
        else:
            np = util.get_module(
                "numpy", required="Auto creation of histograms requires numpy"
            )

            self.histogram, self.bins = np.histogram(sequence, bins=num_bins)
            self.histogram = self.histogram.tolist()
            self.bins = self.bins.tolist()
        if len(self.histogram) > self.MAX_LENGTH:
            raise ValueError(
                "The maximum length of a histogram is %i" % self.MAX_LENGTH
            )
        if len(self.histogram) + 1 != len(self.bins):
            raise ValueError("len(bins) must be len(histogram) + 1")

    def to_json(self, run: Union["LocalRun", "LocalArtifact"] = None) -> dict:
        return {"_type": "histogram", "values": self.histogram, "bins": self.bins}


def wb_filename(key: str, step: int, id: str, extension: str) -> str:
    return "{}_{}_{}{}".format(key, str(step), id, extension)


class Media(WBValue):
    """A WBValue that we store as a file outside JSON and show in a media panel
    on the front end.

    If necessary, we move or copy the file into the Run's media directory so that it gets
    uploaded.
    """

    _path: Optional[str]
    _run: Optional["LocalRun"]
    _caption: Optional[str]
    _is_tmp: Optional[bool]
    _extension: Optional[str]
    _sha256: Optional[str]
    _size: Optional[int]

    def __init__(self, caption: Optional[str] = None) -> None:
        super(Media, self).__init__()
        self._path = None
        # The run under which this object is bound, if any.
        self._run = None
        self._caption = caption

    def _set_file(
        self, path: str, is_tmp: bool = False, extension: Optional[str] = None
    ) -> None:
        self._path = path
        self._is_tmp = is_tmp
        self._extension = extension
        if extension is not None and not path.endswith(extension):
            raise ValueError(
                'Media file extension "{}" must occur at the end of path "{}".'.format(
                    extension, path
                )
            )

        with open(self._path, "rb") as f:
            self._sha256 = hashlib.sha256(f.read()).hexdigest()
        self._size = os.path.getsize(self._path)

    @classmethod
    def get_media_subdir(cls: Type["Media"]) -> str:
        raise NotImplementedError

    @staticmethod
    def captions(media_items: List[Type["Media"]]) -> Union[bool, List[Optional[str]]]:
        if media_items[0]._caption is not None:
            return [m._caption for m in media_items]
        else:
            return False

    def is_bound(self) -> bool:
        return self._run is not None

    def file_is_set(self) -> bool:
        return self._path is not None and self._sha256 is not None

    def bind_to_run(
        self, run: "LocalRun", key: str, step: int, id_: Optional[str] = None
    ) -> None:
        """Bind this object to a particular Run.

        Calling this function is necessary so that we have somewhere specific to
        put the file associated with this object, from which other Runs can
        refer to it.
        """
        if not self.file_is_set():
            raise AssertionError("bind_to_run called before _set_file")

        # The following two assertions are guaranteed to pass
        # by definition file_is_set, but are needed for
        # mypy to understand that these are strings below.
        assert isinstance(self._path, str)
        assert isinstance(self._sha256, str)

        if run is None:
            raise TypeError('Argument "run" must not be None.')
        self._run = run

        # Following assertion required for mypy
        assert self._run is not None

        base_path = os.path.join(self._run.dir, self.get_media_subdir())

        if self._extension is None:
            _, extension = os.path.splitext(os.path.basename(self._path))
        else:
            extension = self._extension

        if id_ is None:
            id_ = self._sha256[:8]

        file_path = wb_filename(key, step, id_, extension)
        media_path = os.path.join(self.get_media_subdir(), file_path)
        new_path = os.path.join(base_path, file_path)
        util.mkdir_exists_ok(os.path.dirname(new_path))

        if self._is_tmp:
            shutil.move(self._path, new_path)
            self._path = new_path
            self._is_tmp = False
            _datatypes_callback(media_path)
        else:
            shutil.copy(self._path, new_path)
            self._path = new_path
            _datatypes_callback(media_path)

    def to_json(self, run: Union["LocalRun", "LocalArtifact"]) -> dict:
        """Serializes the object into a JSON blob, using a run or artifact to store additional data. If `run_or_artifact`
        is a wandb.Run then `self.bind_to_run()` must have been previously been called.

        Args:
            run_or_artifact (wandb.Run | wandb.Artifact): the Run or Artifact for which this object should be generating
            JSON for - this is useful to to store additional data if needed.

        Returns:
            dict: JSON representation
        """
        json_obj = {}
        run_class, artifact_class = _safe_sdk_import()
        if isinstance(run, run_class):
            if not self.is_bound():
                raise RuntimeError(
                    "Value of type {} must be bound to a run with bind_to_run() before being serialized to JSON.".format(
                        type(self).__name__
                    )
                )

            assert (
                self._run is run
            ), "We don't support referring to media files across runs."

            # The following two assertions are guaranteed to pass
            # by definition is_bound, but are needed for
            # mypy to understand that these are strings below.
            assert isinstance(self._path, str)

            json_obj.update(
                {
                    "_type": "file",  # TODO(adrian): This isn't (yet) a real media type we support on the frontend.
                    "path": util.to_forward_slash_path(
                        os.path.relpath(self._path, self._run.dir)
                    ),
                    "sha256": self._sha256,
                    "size": self._size,
                }
            )
        elif isinstance(run, artifact_class):
            if self.file_is_set():
                # The following two assertions are guaranteed to pass
                # by definition of the call above, but are needed for
                # mypy to understand that these are strings below.
                assert isinstance(self._path, str)
                assert isinstance(self._sha256, str)
                artifact = run  # Checks if the concrete image has already been added to this artifact
                name = artifact.get_added_local_path_name(self._path)
                if name is None:
                    if self._is_tmp:
                        name = os.path.join(
                            self.get_media_subdir(), os.path.basename(self._path)
                        )
                    else:
                        # If the files is not temporary, include the first 8 characters of the file's SHA256 to
                        # avoid name collisions. This way, if there are two images `dir1/img.png` and `dir2/img.png`
                        # we end up with a unique path for each.
                        name = os.path.join(
                            self.get_media_subdir(),
                            self._sha256[:8],
                            os.path.basename(self._path),
                        )

                    # if not, check to see if there is a source artifact for this object
                    if (
                        self.artifact_source
                        is not None
                        # and self.artifact_source.artifact != artifact
                    ):
                        default_root = self.artifact_source.artifact._default_root()
                        # if there is, get the name of the entry (this might make sense to move to a helper off artifact)
                        if self._path.startswith(default_root):
                            name = self._path[len(default_root) :]
                            name = name.lstrip(os.sep)

                        # Add this image as a reference
                        path = self.artifact_source.artifact.get_path(name)
                        artifact.add_reference(path.ref_url(), name=name)
                    else:
                        entry = artifact.add_file(
                            self._path, name=name, is_tmp=self._is_tmp
                        )
                        name = entry.path

                json_obj["path"] = name
            json_obj["_type"] = self.artifact_type
        return json_obj

    @classmethod
    def from_json(
        cls: Type["Media"], json_obj: dict, source_artifact: "PublicArtifact"
    ) -> "Media":
        """Likely will need to override for any more complicated media objects"""
        return cls(source_artifact.get_path(json_obj["path"]).download())

    def __eq__(self, other: object) -> bool:
        """Likely will need to override for any more complicated media objects"""
        return (
            isinstance(other, self.__class__)
            and hasattr(self, "_sha256")
            and hasattr(other, "_sha256")
            and self._sha256 == other._sha256
        )


__all__ = ["WBValue", "Histogram", "Media"]