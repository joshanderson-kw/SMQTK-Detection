from smqtk_detection.exceptions import NoDetectionError
from smqtk_detection.interfaces.detection_element import DetectionElement
from smqtk_classifier.interfaces.classification_element import ClassificationElement
from smqtk_detection.utils.bbox import AxisAlignedBoundingBox
from typing import Hashable, Optional, Tuple


class MemoryDetectionElement (DetectionElement):  # lgtm[py/missing-equals]
    """
    In-memory backend of the DetectionElement representation interface.  This
    implementation has no persistence.

    See ``DetectionElement`` for documentation on abstract method implemented
    here.
    """

    __slots__ = ('_bbox', '_classification')

    @classmethod
    def is_usable(cls) -> bool:
        # In-memory implementation does not require any additional
        # dependencies.
        return True

    def __init__(self, uuid: Hashable) -> None:
        super(MemoryDetectionElement, self).__init__(uuid)
        #: :type: None | AxisAlignedMemoryDetectionElementBoundingBox
        self._bbox: Optional[AxisAlignedBoundingBox] = None
        #: :type: None | ClassificationElement
        self._classification: Optional[ClassificationElement] = None

    def __getstate__(self) -> dict:
        return {
            'parent': super(MemoryDetectionElement, self).__getstate__(),
            'bbox': self._bbox,
            'classification': self._classification,
        }

    def __setstate__(self, state: dict) -> None:
        super(MemoryDetectionElement, self).__setstate__(state['parent'])
        self._bbox = state['bbox']
        self._classification = state['classification']

    def get_config(self) -> dict:
        # No additional constructor parameters for in-memory implementation.
        return {}

    def has_detection(self) -> bool:
        # We are a valid detection if our components are non-null and
        # True-evaluation (meaning they have valid contents).
        if self._bbox is None or self._classification is None:
            return False
        else:
            return self._classification.has_classifications()

    def get_bbox(self) -> Optional[AxisAlignedBoundingBox]:
        if not self._bbox:
            raise NoDetectionError("Missing detection bounding box for "
                                   "in-memory detection with UUID {}"
                                   .format(self.uuid))
        return self._bbox

    def get_classification(self) -> Optional[ClassificationElement]:
        if not (self._classification and
                self._classification.has_classifications()):
            raise NoDetectionError("Missing or empty classification for "
                                   "in-memory detection with UUID {}"
                                   .format(self.uuid))
        return self._classification

    def get_detection(self) -> Tuple[AxisAlignedBoundingBox, ClassificationElement]:
        if (
            self._bbox is None
            or self._classification is None
            or not self._classification.has_classifications()
        ):
            raise NoDetectionError("Missing detection bounding box or "
                                   "missing/invalid classification for "
                                   "in-memory detection with UUID {}"
                                   .format(self.uuid))
        return self._bbox, self._classification

    def set_detection(self, bbox: Optional[AxisAlignedBoundingBox],
                      classification_element: Optional[ClassificationElement]) -> \
            "MemoryDetectionElement":
        if not isinstance(bbox, AxisAlignedBoundingBox):
            raise ValueError("Provided an invalid AxisAlignedBoundingBox "
                             "instance. Given '{}' (type={})."
                             .format(bbox, type(bbox).__name__))
        if not isinstance(classification_element, ClassificationElement):
            raise ValueError("Provided an invalid ClassificationElement "
                             "instance. Given '{}' (type={})."
                             .format(classification_element,
                                     type(classification_element).__name__))
        if not classification_element.has_classifications():
            raise ValueError("Given an empty ClassificationElement instance.")
        self._bbox = bbox
        self._classification = classification_element
        return self
