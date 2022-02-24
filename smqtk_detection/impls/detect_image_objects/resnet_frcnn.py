import importlib.util
import logging
from typing import Tuple, Iterable, Dict, Hashable, List
from types import MethodType

import numpy as np

try:
    import torch  # type: ignore
    import torchvision.models as models  # type: ignore
    from torchvision import transforms  # type: ignore
    from torchvision.ops import boxes as box_ops  # type: ignore
    import torch.nn.functional as F  # type: ignore
    from torchvision.models.detection.roi_heads import RoIHeads  # type: ignore
except ModuleNotFoundError:
    pass

from smqtk_detection.interfaces.detect_image_objects import DetectImageObjects
from smqtk_detection.utils.bbox import AxisAlignedBoundingBox


LOG = logging.getLogger(__name__)


class ResNetFRCNN(DetectImageObjects):
    """
    ``DetectImageObjects`` implementation using ``torchvision``'s Faster R-CNN
    with a ResNet-50-FPN backbone, pretrained on COCO train2017.

    :param box_thresh: Confidence threshold for detections.
    :param num_dets: Maximum number of detections per image.
    :param img_batch_size: Batch size in images for inferences.
    :param use_cuda: Attempt to use a cuda device for inferences. If no
        device is found, CPU is used.
    """

    def __init__(
        self,
        box_thresh: float = 0.05,
        num_dets: int = 100,
        img_batch_size: int = 1,
        use_cuda: bool = False,
    ):
        self.box_thresh = box_thresh
        self.num_dets = num_dets
        self.img_batch_size = img_batch_size
        self.use_cuda = use_cuda

        # Set to None for lazy loading later.
        self.model: torch.nn.Module = None  # type: ignore

        # The model already has normalization and resizing baked into the
        # layers.
        self.model_loader = transforms.Compose([
            # transforms.ToPILImage(),
            transforms.ToTensor(),
        ])

    def get_model(self) -> "torch.nn.Module":
        """Lazy load the torch model in an idempotent manner."""
        model = self.model
        if model is None:
            model = models.detection.fasterrcnn_resnet50_fpn(
                pretrained=True,
                progress=False,
                box_detections_per_img=self.num_dets,
                box_score_thresh=self.box_thresh
            )
            model = model.eval()
            if self.use_cuda:
                if torch.cuda.is_available():
                    model = model.cuda()
                else:
                    LOG.warning("Use of CUDA requested, but not available. "
                                "Proceeding with model NOT transitioned to "
                                "CUDA.")
            model.roi_heads.postprocess_detections = (
                MethodType(_postprocess_detections, model.roi_heads)
            )
            # store the loaded model for later return.
            self.model = model
        return model

    def detect_objects(
        self,
        img_iter: Iterable[np.ndarray]
    ) -> Iterable[Iterable[Tuple[AxisAlignedBoundingBox, Dict[Hashable, float]]]]:
        formatted_dets = []  # AxisAlignedBoundingBox detections to return
        model = self.get_model()

        img_list = list(img_iter)
        img_tensors = [self.model_loader(img) for img in img_list]

        if self.use_cuda:
            img_tensors = [tensor.cuda() for tensor in img_tensors]

        # split into batches
        batches = []
        for i in range(0, len(img_tensors), self.img_batch_size):
            batches.append(img_tensors[i: i + self.img_batch_size])

        with torch.no_grad():
            all_img_dets = sum([model(batch) for batch in batches], [])  # type: List[Dict]

        for img_dets in all_img_dets:
            bboxes = img_dets['boxes'].cpu().numpy()
            scores = img_dets['scores'].cpu().numpy()

            a_bboxes = [AxisAlignedBoundingBox(
                [box[0], box[1]], [box[2], box[3]]
            ) for box in bboxes]

            score_dicts = []

            for img_scores in scores:
                score_dict = {}  # type: Dict[Hashable, float]
                # Scores returned start at COCO i.d. 1
                for i, n in enumerate(img_scores, start=1):
                    score_dict[COCO_INSTANCE_CATEGORY_NAMES[i]] = n
                score_dicts.append(score_dict)

            formatted_dets.append(list(zip(a_bboxes, score_dicts)))

        return formatted_dets

    def get_config(self) -> dict:
        return {
            "box_thresh": self.box_thresh,
            "num_dets": self.num_dets,
            "img_batch_size": self.img_batch_size,
            "use_cuda": self.use_cuda,
        }

    @classmethod
    def is_usable(cls) -> bool:
        # check for optional dependencies
        torch_spec = importlib.util.find_spec('torch')
        torchvision_spec = importlib.util.find_spec('torchvision')
        if torch_spec is not None and torchvision_spec is not None:
            return True
        else:
            return False


try:
    def _postprocess_detections(
        self: RoIHeads,
        class_logits: torch.Tensor,
        box_regression: torch.Tensor,
        proposals: List[torch.Tensor],
        image_shapes: List[Tuple[int, int]]
    ) -> Tuple[List[torch.Tensor], List[torch.Tensor], List[torch.Tensor]]:
        """
        Modified bounding box postprocessing function that returns class
        probabilites instead of just a confidence score. Taken from
        https://github.com/XAITK/xaitk-saliency/blob/master/examples/DRISE.ipynb
        """

        device = class_logits.device
        num_classes = class_logits.shape[-1]

        boxes_per_image = [boxes_in_image.shape[0] for boxes_in_image in proposals]
        pred_boxes = self.box_coder.decode(box_regression, proposals)

        pred_scores = F.softmax(class_logits, -1)

        pred_boxes_list = pred_boxes.split(boxes_per_image, 0)
        pred_scores_list = pred_scores.split(boxes_per_image, 0)

        all_boxes = []
        all_scores = []
        all_labels = []
        for boxes, scores, image_shape in zip(pred_boxes_list, pred_scores_list, image_shapes):
            boxes = box_ops.clip_boxes_to_image(boxes, image_shape)

            # create labels for each prediction
            labels = torch.arange(num_classes, device=device)
            labels = labels.view(1, -1).expand_as(scores)

            # remove predictions with the background label
            boxes = boxes[:, 1:]
            scores = scores[:, 1:]
            scores_orig = scores.clone()
            labels = labels[:, 1:]

            # batch everything, by making every class prediction be a separate instance
            boxes = boxes.reshape(-1, 4)
            scores = scores.reshape(-1)
            labels = labels.reshape(-1)

            # remove low scoring boxes
            inds = torch.where(scores > self.score_thresh)[0]
            boxes, scores, labels = boxes[inds], scores[inds], labels[inds]

            # remove empty boxes
            keep = box_ops.remove_small_boxes(boxes, min_size=1e-2)
            inds = inds[keep]
            boxes, scores, labels = boxes[keep], scores[keep], labels[keep]

            # non-maximum suppression, independently done per class
            keep = box_ops.batched_nms(boxes, scores, labels, self.nms_thresh)
            # keep only topk scoring predictions
            keep = keep[:self.detections_per_img]
            inds = inds[keep]
            boxes, scores, labels = boxes[keep], scores[keep], labels[keep]

            # Find corresponding row of matrix
            inds = inds // (num_classes - 1)

            all_boxes.append(boxes)
            all_scores.append(scores_orig[inds, :])
            all_labels.append(labels)

        return all_boxes, all_scores, all_labels

    # Labels for this pretrained model are detailed here
    # https://pytorch.org/vision/stable/models.html#object-detection-instance-segmentation-and-person-keypoint-detection
    COCO_INSTANCE_CATEGORY_NAMES = (
        '__background__', 'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
        'train', 'truck', 'boat', 'traffic light', 'fire hydrant', 'N/A', 'stop sign',
        'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow',
        'elephant', 'bear', 'zebra', 'giraffe', 'N/A', 'backpack', 'umbrella', 'N/A', 'N/A',
        'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball',
        'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
        'bottle', 'N/A', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
        'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza',
        'donut', 'cake', 'chair', 'couch', 'potted plant', 'bed', 'N/A', 'dining table',
        'N/A', 'N/A', 'toilet', 'N/A', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone',
        'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'N/A', 'book',
        'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush'
    )
except NameError:
    pass
