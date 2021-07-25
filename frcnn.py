import colorsys
import copy
import os
import time

import numpy as np
import tensorflow as tf
from PIL import Image, ImageDraw, ImageFont
from tensorflow.keras.applications.imagenet_utils import preprocess_input

import nets.frcnn as frcnn
from nets.frcnn_training import get_new_img_size
from utils.anchors import get_anchors
from utils.config import Config
from utils.utils import BBoxUtility


# --------------------------------------------#
#   使用自己训练好的模型预测需要修改2个参数
#   model_path和classes_path都需要修改！
#   如果出现shape不匹配
#   一定要注意训练时的NUM_CLASSES、
#   model_path和classes_path参数的修改
# --------------------------------------------#
class FRCNN(object):
    _defaults = {
        "model_path": 'model_data/voc_weights.h5',
        "classes_path": 'model_data/voc_classes.txt',
        "confidence": 0.5,
        "iou": 0.3
    }

    @classmethod
    def get_defaults(cls, n):
        if n in cls._defaults:
            return cls._defaults[n]
        else:
            return "Unrecognized attribute name '" + n + "'"

    # ---------------------------------------------------#
    #   初始化faster RCNN
    # ---------------------------------------------------#
    def __init__(self, **kwargs):
        self.__dict__.update(self._defaults)
        self.class_names = self._get_class()
        self.config = Config()
        self.generate()
        self.bbox_util = BBoxUtility()

    # ---------------------------------------------------#
    #   获得所有的分类
    # ---------------------------------------------------#
    def _get_class(self):
        classes_path = os.path.expanduser(self.classes_path)
        with open(classes_path) as f:
            class_names = f.readlines()
        class_names = [c.strip() for c in class_names]
        return class_names

    # ---------------------------------------------------#
    #   获得所有的分类
    # ---------------------------------------------------#
    def generate(self):
        model_path = os.path.expanduser(self.model_path)
        assert model_path.endswith('.h5'), 'Keras model or weights must be a .h5 file.'

        # -------------------------------#
        #   计算总的类的数量
        # -------------------------------#
        self.num_classes = len(self.class_names) + 1

        # -------------------------------#
        #   载入模型与权值
        # -------------------------------#
        self.model_rpn, self.model_classifier = frcnn.get_predict_model(self.config, self.num_classes)
        self.model_rpn.load_weights(self.model_path, by_name=True)
        self.model_classifier.load_weights(self.model_path, by_name=True)

        print('{} model, anchors, and classes loaded.'.format(model_path))

        # 画框设置不同的颜色
        hsv_tuples = [(x / len(self.class_names), 1., 1.)
                      for x in range(len(self.class_names))]
        self.colors = list(map(lambda x: colorsys.hsv_to_rgb(*x), hsv_tuples))
        self.colors = list(
            map(lambda x: (int(x[0] * 255), int(x[1] * 255), int(x[2] * 255)),
                self.colors))

    # ---------------------------------------------------#
    #   用于计算共享特征层的大小
    # ---------------------------------------------------#
    def get_img_output_length(self, width, height):
        def get_output_length(input_length):
            # input_length += 6
            filter_sizes = [7, 3, 1, 1]
            padding = [3, 1, 0, 0]
            stride = 2
            for i in range(4):
                # input_length = (input_length - filter_size + stride) // stride
                input_length = (input_length + 2 * padding[i] - filter_sizes[i]) // stride + 1
            return input_length

        return get_output_length(width), get_output_length(height)

    @tf.function(experimental_relax_shapes=True)
    def model_rpn_get_pred(self, photo):
        preds = self.model_rpn(photo, training=False)
        return preds

    @tf.function(experimental_relax_shapes=True)
    def model_classifier_get_pred(self, photo):
        preds = self.model_classifier(photo, training=False)
        return preds

    # ---------------------------------------------------#
    #   检测图片
    # ---------------------------------------------------#
    def detect_image(self, image):
        # -------------------------------------#
        #   转换成RGB图片，可以用于灰度图预测。
        # -------------------------------------#
        image = image.convert("RGB")

        image_shape = np.array(np.shape(image)[0:2])
        old_width, old_height = image_shape[1], image_shape[0]
        old_image = copy.deepcopy(image)

        # ---------------------------------------------------------#
        #   给原图像进行resize，resize到短边为600的大小上
        # ---------------------------------------------------------#
        width, height = get_new_img_size(old_width, old_height)
        image = image.resize([width, height], Image.BICUBIC)
        photo = np.array(image, dtype=np.float64)

        # -----------------------------------------------------------#
        #   图片预处理，归一化。
        # -----------------------------------------------------------#
        photo = preprocess_input(np.expand_dims(photo, 0))
        rpn_pred = self.model_rpn_get_pred(photo)
        rpn_pred = [x.numpy() for x in rpn_pred]

        # -----------------------------------------------------------#
        #   将建议框网络的预测结果进行解码
        # -----------------------------------------------------------#
        base_feature_width, base_feature_height = self.get_img_output_length(width, height)
        anchors = get_anchors([base_feature_width, base_feature_height], width, height)
        rpn_results = self.bbox_util.detection_out_rpn(rpn_pred, anchors)

        # -------------------------------------------------------------#
        #   在获得建议框和共享特征层后，将二者传入classifier中进行预测
        # -------------------------------------------------------------#
        base_layer = rpn_pred[2]
        proposal_box = np.array(rpn_results)[:, :, 1:]
        temp_ROIs = np.zeros_like(proposal_box)
        temp_ROIs[:, :, [0, 1, 2, 3]] = proposal_box[:, :, [1, 0, 3, 2]]
        classifier_pred = self.model_classifier_get_pred([base_layer, temp_ROIs])
        classifier_pred = [x.numpy() for x in classifier_pred]

        # -------------------------------------------------------------#
        #   利用classifier的预测结果对建议框进行解码，获得预测框
        # -------------------------------------------------------------#
        results = self.bbox_util.detection_out_classifier(classifier_pred, proposal_box, self.config, self.confidence)

        if len(results[0]) == 0:
            return old_image

        results = np.array(results[0])
        boxes = results[:, :4]
        top_conf = results[:, 4]
        top_label_indices = results[:, 5]
        boxes[:, [0, 2]] = boxes[:, [0, 2]] * old_width
        boxes[:, [1, 3]] = boxes[:, [1, 3]] * old_height

        font = ImageFont.truetype(font='model_data/simhei.ttf',
                                  size=np.floor(3e-2 * np.shape(image)[1] + 0.5).astype('int32'))

        thickness = max((np.shape(old_image)[0] + np.shape(old_image)[1]) // old_width * 2, 1)

        image = old_image
        for i, c in enumerate(top_label_indices):
            predicted_class = self.class_names[int(c)]
            score = top_conf[i]

            left, top, right, bottom = boxes[i]
            top = top - 5
            left = left - 5
            bottom = bottom + 5
            right = right + 5

            top = max(0, np.floor(top + 0.5).astype('int32'))
            left = max(0, np.floor(left + 0.5).astype('int32'))
            bottom = min(np.shape(image)[0], np.floor(bottom + 0.5).astype('int32'))
            right = min(np.shape(image)[1], np.floor(right + 0.5).astype('int32'))

            # 画框框
            label = '{} {:.2f}'.format(predicted_class, score)
            draw = ImageDraw.Draw(image)
            label_size = draw.textsize(label, font)
            label = label.encode('utf-8')
            print(label, top, left, bottom, right)

            if top - label_size[1] >= 0:
                text_origin = np.array([left, top - label_size[1]])
            else:
                text_origin = np.array([left, top + 1])

            for i in range(thickness):
                draw.rectangle(
                    [left + i, top + i, right - i, bottom - i],
                    outline=self.colors[int(c)])
            draw.rectangle(
                [tuple(text_origin), tuple(text_origin + label_size)],
                fill=self.colors[int(c)])
            draw.text(text_origin, str(label, 'UTF-8'), fill=(0, 0, 0), font=font)
            del draw
        return image

    def get_FPS(self, image, test_interval):
        # -------------------------------------#
        #   转换成RGB图片，可以用于灰度图预测。
        # -------------------------------------#
        image = image.convert("RGB")

        image_shape = np.array(np.shape(image)[0:2])
        old_width, old_height = image_shape[1], image_shape[0]

        # ---------------------------------------------------------#
        #   给原图像进行resize，resize到短边为600的大小上
        # ---------------------------------------------------------#
        width, height = get_new_img_size(old_width, old_height)
        image = image.resize([width, height], Image.BICUBIC)
        photo = np.array(image, dtype=np.float64)

        # -----------------------------------------------------------#
        #   图片预处理，归一化。
        # -----------------------------------------------------------#
        photo = preprocess_input(np.expand_dims(photo, 0))
        rpn_pred = self.model_rpn.predict(photo)

        # -----------------------------------------------------------#
        #   将建议框网络的预测结果进行解码
        # -----------------------------------------------------------#
        base_feature_width, base_feature_height = self.get_img_output_length(width, height)
        anchors = get_anchors([base_feature_width, base_feature_height], width, height)
        rpn_results = self.bbox_util.detection_out_rpn(rpn_pred, anchors)

        # -------------------------------------------------------------#
        #   在获得建议框和共享特征层后，将二者传入classifier中进行预测
        # -------------------------------------------------------------#
        base_layer = rpn_pred[2]
        proposal_box = np.array(rpn_results)[:, :, 1:]
        temp_ROIs = np.zeros_like(proposal_box)
        temp_ROIs[:, :, [0, 1, 2, 3]] = proposal_box[:, :, [1, 0, 3, 2]]
        classifier_pred = self.model_classifier.predict([base_layer, temp_ROIs])

        # -------------------------------------------------------------#
        #   利用classifier的预测结果对建议框进行解码，获得预测框
        # -------------------------------------------------------------#
        results = self.bbox_util.detection_out_classifier(classifier_pred, proposal_box, self.config, self.confidence)

        if len(results[0]) > 0:
            results = np.array(results[0])
            boxes = results[:, :4]
            top_conf = results[:, 4]
            top_label_indices = results[:, 5]
            boxes[:, [0, 2]] = boxes[:, [0, 2]] * old_width
            boxes[:, [1, 3]] = boxes[:, [1, 3]] * old_height

        t1 = time.time()
        for _ in range(test_interval):
            rpn_pred = self.model_rpn.predict(photo)

            # -----------------------------------------------------------#
            #   将建议框网络的预测结果进行解码
            # -----------------------------------------------------------#
            base_feature_width, base_feature_height = self.get_img_output_length(width, height)
            anchors = get_anchors([base_feature_width, base_feature_height], width, height)
            rpn_results = self.bbox_util.detection_out_rpn(rpn_pred, anchors)

            # -------------------------------------------------------------#
            #   在获得建议框和共享特征层后，将二者传入classifier中进行预测
            # -------------------------------------------------------------#
            base_layer = rpn_pred[2]
            proposal_box = np.array(rpn_results)[:, :, 1:]
            temp_ROIs = np.zeros_like(proposal_box)
            temp_ROIs[:, :, [0, 1, 2, 3]] = proposal_box[:, :, [1, 0, 3, 2]]
            classifier_pred = self.model_classifier.predict([base_layer, temp_ROIs])

            # -------------------------------------------------------------#
            #   利用classifier的预测结果对建议框进行解码，获得预测框
            # -------------------------------------------------------------#
            results = self.bbox_util.detection_out_classifier(classifier_pred, proposal_box, self.config,
                                                              self.confidence)

            if len(results[0]) > 0:
                results = np.array(results[0])
                boxes = results[:, :4]
                top_conf = results[:, 4]
                top_label_indices = results[:, 5]
                boxes[:, [0, 2]] = boxes[:, [0, 2]] * old_width
                boxes[:, [1, 3]] = boxes[:, [1, 3]] * old_height

        t2 = time.time()
        tact_time = (t2 - t1) / test_interval
        return tact_time
