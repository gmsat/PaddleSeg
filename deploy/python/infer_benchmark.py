# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import codecs
import os
import sys

import time
import yaml
import numpy as np
import paddle

LOCAL_PATH = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(LOCAL_PATH, '..', '..'))

from paddleseg.cvlibs import manager
from paddleseg.utils import logger, metrics, progbar

from infer import Predictor


def parse_args():
    parser = argparse.ArgumentParser(description='Model Infer')
    parser.add_argument(
        "--config",
        dest="cfg",
        help="The config file.",
        default=None,
        type=str,
        required=True)

    parser.add_argument(
        '--dataset_type',
        dest='dataset_type',
        help='The name of dataset, such as Cityscapes, PascalVOC and ADE20K.',
        type=str,
        default=None,
        required=True)
    parser.add_argument(
        '--dataset_path',
        dest='dataset_path',
        help='The directory of the dataset to be predicted. If set dataset_path, '
        'it use the test and label images to calculate the mIoU.',
        type=str,
        default=None,
        required=True)
    parser.add_argument(
        '--dataset_mode',
        dest='dataset_mode',
        help='The dataset mode, such as train, val.',
        type=str,
        default="val")
    parser.add_argument(
        '--batch_size',
        dest='batch_size',
        help='Mini batch size of one gpu or cpu.',
        type=int,
        default=1)

    parser.add_argument(
        '--device',
        choices=['cpu', 'gpu'],
        default="gpu",
        help="Select which device to inference, defaults to gpu.")

    parser.add_argument(
        '--use_trt',
        default=False,
        type=eval,
        choices=[True, False],
        help='Whether to use Nvidia TensorRT to accelerate prediction.')
    parser.add_argument(
        "--precision",
        default="fp32",
        type=str,
        choices=["fp32", "fp16", "int8"],
        help='The tensorrt precision.')

    parser.add_argument(
        '--cpu_threads',
        default=10,
        type=int,
        help='Number of threads to predict when using cpu.')
    parser.add_argument(
        '--enable_mkldnn',
        default=False,
        type=eval,
        choices=[True, False],
        help='Enable to use mkldnn to speed up when using cpu.')

    parser.add_argument(
        '--with_argmax',
        dest='with_argmax',
        help='Perform argmax operation on the predict result.',
        action='store_true')

    parser.add_argument(
        '--print_detail',
        dest='print_detail',
        help='Print GLOG information of Paddle Inference.',
        action='store_true')

    return parser.parse_args()


class DatasetPredictor(Predictor):
    def __init__(self, args):
        super().__init__(args)

    def test_dataset(self):
        """
        Read the data from dataset and calculate the accurary of the inference model.
        """
        comp = manager.DATASETS
        if self.args.dataset_type not in comp.components_dict:
            raise RuntimeError("The dataset is not supported.")
        kwargs = {
            'transforms': self.cfg.transforms.transforms,
            'dataset_root': self.args.dataset_path,
            'mode': self.args.dataset_mode
        }
        dataset = comp[self.args.dataset_type](**kwargs)

        input_names = self.predictor.get_input_names()
        input_handle = self.predictor.get_input_handle(input_names[0])
        output_names = self.predictor.get_output_names()
        output_handle = self.predictor.get_output_handle(output_names[0])

        intersect_area_all = 0
        pred_area_all = 0
        label_area_all = 0
        total_time = 0
        progbar_val = progbar.Progbar(target=len(dataset), verbose=1)

        for idx, (img, label) in enumerate(dataset):
            data = np.array([img])
            input_handle.reshape(data.shape)
            input_handle.copy_from_cpu(data)

            start_time = time.time()
            self.predictor.run()
            end_time = time.time()
            total_time += (end_time - start_time)

            pred = output_handle.copy_to_cpu()
            pred = self.postprocess(paddle.to_tensor(pred))
            label = paddle.to_tensor(label, dtype="int32")

            intersect_area, pred_area, label_area = metrics.calculate_area(
                pred,
                label,
                dataset.num_classes,
                ignore_index=dataset.ignore_index)

            intersect_area_all = intersect_area_all + intersect_area
            pred_area_all = pred_area_all + pred_area
            label_area_all = label_area_all + label_area

            progbar_val.update(idx + 1)

        class_iou, miou = metrics.mean_iou(intersect_area_all, pred_area_all,
                                           label_area_all)
        class_acc, acc = metrics.accuracy(intersect_area_all, pred_area_all)
        kappa = metrics.kappa(intersect_area_all, pred_area_all, label_area_all)

        logger.info(
            "[EVAL] #Images: {} mIoU: {:.4f} Acc: {:.4f} Kappa: {:.4f} ".format(
                len(dataset), miou, acc, kappa))
        logger.info("[EVAL] Class IoU: \n" + str(np.round(class_iou, 4)))
        logger.info("[EVAL] Class Acc: \n" + str(np.round(class_acc, 4)))
        logger.info("[EVAL] Average time: %.3f second/img" %
                    (total_time / len(dataset)))


def main(args):
    predictor = DatasetPredictor(args)
    if args.dataset_type and args.dataset_path:
        predictor.test_dataset()
    else:
        raise RuntimeError("Please set dataset_type and dataset_path.")


if __name__ == '__main__':
    """
    Based on the infer config and dataset, this program read the test and
    label images, applys the transfors, run the predictor, ouput the accuracy.

    For example:
    python deploy/python/infer_benchmark.py \
        --config path/to/bisenetv2/deploy.yaml \
        --dataset_type Cityscapes \
        --dataset_path path/to/cityscapes
    """
    args = parse_args()
    main(args)