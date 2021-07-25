import numpy as np
import tensorflow as tf
import tensorflow.keras.backend as K
from tensorflow import keras
from tqdm import tqdm

from nets.frcnn import get_model
from nets.frcnn_training import (Generator, LossHistory, class_loss_cls,
                                 class_loss_regr, cls_loss,
                                 get_img_output_length, smooth_l1)
from utils.anchors import get_anchors
from utils.config import Config
from utils.roi_helpers import calc_iou
from utils.utils import BBoxUtility

gpus = tf.config.experimental.list_physical_devices(device_type='GPU')
for gpu in gpus:
    tf.config.experimental.set_memory_growth(gpu, True)


def write_log(callback, names, logs, batch_no):
    with callback.as_default():
        for name, value in zip(names, logs):
            tf.summary.scalar(name, value, step=batch_no)
            callback.flush()


def fit_one_epoch(model_rpn, model_all, epoch, epoch_size, epoch_size_val, gen, genval, Epoch, callback):
    total_loss = 0
    rpn_loc_loss = 0
    rpn_cls_loss = 0
    roi_loc_loss = 0
    roi_cls_loss = 0

    val_toal_loss = 0
    with tqdm(total=epoch_size, desc=f'Epoch {epoch + 1}/{Epoch}', postfix=dict, mininterval=0.3) as pbar:
        for iteration, batch in enumerate(gen):
            if iteration >= epoch_size:
                break
            X, Y, boxes = batch[0], batch[1], batch[2]
            P_rpn = model_rpn.predict_on_batch(X)

            height, width, _ = np.shape(X[0])
            base_feature_width, base_feature_height = get_img_output_length(width, height)
            anchors = get_anchors([base_feature_width, base_feature_height], width, height)
            results = bbox_util.detection_out_rpn(P_rpn, anchors)

            roi_inputs = []
            out_classes = []
            out_regrs = []
            for i in range(len(X)):
                R = results[i][:, 1:]
                X2, Y1, Y2 = calc_iou(R, config, boxes[i], NUM_CLASSES)
                roi_inputs.append(X2)
                out_classes.append(Y1)
                out_regrs.append(Y2)

            loss_class = model_all.train_on_batch([X, np.array(roi_inputs)],
                                                  [Y[0], Y[1], np.array(out_classes), np.array(out_regrs)])

            write_log(callback,
                      ['total_loss', 'rpn_cls_loss', 'rpn_reg_loss', 'detection_cls_loss', 'detection_reg_loss'],
                      loss_class, iteration)

            rpn_cls_loss += loss_class[1]
            rpn_loc_loss += loss_class[2]
            roi_cls_loss += loss_class[3]
            roi_loc_loss += loss_class[4]
            total_loss = rpn_loc_loss + rpn_cls_loss + roi_loc_loss + roi_cls_loss

            pbar.set_postfix(**{'total': total_loss / (iteration + 1),
                                'rpn_cls': rpn_cls_loss / (iteration + 1),
                                'rpn_loc': rpn_loc_loss / (iteration + 1),
                                'roi_cls': roi_cls_loss / (iteration + 1),
                                'roi_loc': roi_loc_loss / (iteration + 1),
                                'lr': K.get_value(model_rpn.optimizer.lr)})
            pbar.update(1)

    print('Start Validation')
    with tqdm(total=epoch_size_val, desc=f'Epoch {epoch + 1}/{Epoch}', postfix=dict, mininterval=0.3) as pbar:
        for iteration, batch in enumerate(genval):
            if iteration >= epoch_size_val:
                break
            X, Y, boxes = batch[0], batch[1], batch[2]
            P_rpn = model_rpn.predict_on_batch(X)

            height, width, _ = np.shape(X[0])
            base_feature_width, base_feature_height = get_img_output_length(width, height)
            anchors = get_anchors([base_feature_width, base_feature_height], width, height)
            results = bbox_util.detection_out_rpn(P_rpn, anchors)

            roi_inputs = []
            out_classes = []
            out_regrs = []
            for i in range(len(X)):
                R = results[i][:, 1:]
                X2, Y1, Y2 = calc_iou(R, config, boxes[i], NUM_CLASSES)
                roi_inputs.append(X2)
                out_classes.append(Y1)
                out_regrs.append(Y2)

            loss_class = model_all.test_on_batch([X, np.array(roi_inputs)],
                                                 [Y[0], Y[1], np.array(out_classes), np.array(out_regrs)])

            val_toal_loss += loss_class[0]
            pbar.set_postfix(**{'total': val_toal_loss / (iteration + 1)})
            pbar.update(1)

    loss_history.append_loss(total_loss / (epoch_size + 1), val_toal_loss / (epoch_size_val + 1))
    print('Finish Validation')
    print('Epoch:' + str(epoch + 1) + '/' + str(Epoch))
    print('Total Loss: %.4f || Val Loss: %.4f ' % (total_loss / (epoch_size + 1), val_toal_loss / (epoch_size_val + 1)))
    print('Saving state, iter:', str(epoch + 1))
    model_all.save_weights('logs/Epoch%d-Total_Loss%.4f-Val_Loss%.4f.h5' % (
    (epoch + 1), total_loss / (epoch_size + 1), val_toal_loss / (epoch_size_val + 1)))
    return


# ----------------------------------------------------#
#   检测精度mAP和pr曲线计算参考视频
#   https://www.bilibili.com/video/BV1zE411u7Vw
# ----------------------------------------------------#
if __name__ == "__main__":
    config = Config()
    # ----------------------------------------------------#
    #   训练之前一定要修改NUM_CLASSES
    #   修改成所需要区分的类的个数+1。
    # ----------------------------------------------------#
    NUM_CLASSES = 21
    # -----------------------------------------------------#
    #   input_shape是输入图片的大小，默认为800,800,3
    #   随着输入图片的增大，占用显存会增大
    #   视频上为600,600,3，多次训练测试后发现800,800,3更优
    # -----------------------------------------------------#
    input_shape = [800, 800, 3]

    model_rpn, model_all = get_model(config, NUM_CLASSES)
    # ------------------------------------------------------#
    #   权值文件请看README，百度网盘下载
    #   训练自己的数据集时提示维度不匹配正常
    #   预测的东西都不一样了自然维度不匹配
    # ------------------------------------------------------#
    base_net_weights = "model_data/voc_weights.h5"
    model_rpn.load_weights(base_net_weights, by_name=True)
    model_all.load_weights(base_net_weights, by_name=True)

    bbox_util = BBoxUtility(overlap_threshold=config.rpn_max_overlap, ignore_threshold=config.rpn_min_overlap,
                            top_k=config.num_RPN_train_pre)

    # --------------------------------------------#
    #   训练参数的设置
    # --------------------------------------------#
    callback = tf.summary.create_file_writer("logs")
    loss_history = LossHistory("logs/")

    annotation_path = '2007_train.txt'
    # ----------------------------------------------------------------------#
    #   验证集的划分在train.py代码里面进行
    #   2007_test.txt和2007_val.txt里面没有内容是正常的。训练不会使用到。
    #   当前划分方式下，验证集和训练集的比例为1:9
    # ----------------------------------------------------------------------#
    val_split = 0.1
    with open(annotation_path) as f:
        lines = f.readlines()
    np.random.seed(10101)
    np.random.shuffle(lines)
    np.random.seed(None)
    num_val = int(len(lines) * val_split)
    num_train = len(lines) - num_val

    # ------------------------------------------------------#
    #   主干特征提取网络特征通用，使用预训练权重可以加快训练
    #   Init_Epoch为起始世代
    #   Interval_Epoch为中间训练的世代
    #   Epoch总训练世代
    #   提示OOM或者显存不足请调小Batch_size
    # ------------------------------------------------------#
    if True:
        lr = 1e-4
        Batch_size = 2
        Init_Epoch = 0
        Interval_Epoch = 50

        model_rpn.compile(
            loss={
                'classification': cls_loss(),
                'regression': smooth_l1()
            }, optimizer=keras.optimizers.Adam(lr=lr)
        )
        model_all.compile(
            loss={
                'classification': cls_loss(),
                'regression': smooth_l1(),
                'dense_class_{}'.format(NUM_CLASSES): class_loss_cls,
                'dense_regress_{}'.format(NUM_CLASSES): class_loss_regr(NUM_CLASSES - 1)
            }, optimizer=keras.optimizers.Adam(lr=lr)
        )

        gen = Generator(bbox_util, lines[:num_train], NUM_CLASSES, Batch_size,
                        input_shape=[input_shape[0], input_shape[1]]).generate()
        gen_val = Generator(bbox_util, lines[num_train:], NUM_CLASSES, Batch_size,
                            input_shape=[input_shape[0], input_shape[1]]).generate()

        epoch_size = num_train // Batch_size
        epoch_size_val = num_val // Batch_size

        if epoch_size == 0 or epoch_size_val == 0:
            raise ValueError("数据集过小，无法进行训练，请扩充数据集。")

        for epoch in range(Init_Epoch, Interval_Epoch):
            fit_one_epoch(model_rpn, model_all, epoch, epoch_size, epoch_size_val, gen, gen_val, Interval_Epoch,
                          callback)
            lr = lr * 0.92
            K.set_value(model_rpn.optimizer.lr, lr)
            K.set_value(model_all.optimizer.lr, lr)

    if True:
        lr = 1e-5
        Batch_size = 2
        Interval_Epoch = 50
        Epoch = 100

        model_rpn.compile(
            loss={
                'classification': cls_loss(),
                'regression': smooth_l1()
            }, optimizer=keras.optimizers.Adam(lr=lr)
        )
        model_all.compile(
            loss={
                'classification': cls_loss(),
                'regression': smooth_l1(),
                'dense_class_{}'.format(NUM_CLASSES): class_loss_cls,
                'dense_regress_{}'.format(NUM_CLASSES): class_loss_regr(NUM_CLASSES - 1)
            }, optimizer=keras.optimizers.Adam(lr=lr)
        )

        gen = Generator(bbox_util, lines[:num_train], NUM_CLASSES, Batch_size,
                        input_shape=[input_shape[0], input_shape[1]]).generate()
        gen_val = Generator(bbox_util, lines[num_train:], NUM_CLASSES, Batch_size,
                            input_shape=[input_shape[0], input_shape[1]]).generate()

        epoch_size = num_train // Batch_size
        epoch_size_val = num_val // Batch_size

        if epoch_size == 0 or epoch_size_val == 0:
            raise ValueError("数据集过小，无法进行训练，请扩充数据集。")

        for epoch in range(Interval_Epoch, Epoch):
            fit_one_epoch(model_rpn, model_all, epoch, epoch_size, epoch_size_val, gen, gen_val, Epoch, callback)
            lr = lr * 0.92
            K.set_value(model_rpn.optimizer.lr, lr)
            K.set_value(model_all.optimizer.lr, lr)
