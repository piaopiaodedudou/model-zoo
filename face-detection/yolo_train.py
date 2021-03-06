from __future__ import print_function

import tensorflow as tf
import numpy as np
import logging
import sys
import time
import os
import math
from PIL import Image, ImageDraw
from argparse import ArgumentParser

from wider_loader import WIDERLoader

logging.basicConfig()
logger = logging.getLogger('yolo')
logger.setLevel(logging.INFO)

class YOLOFace(object):
  def __init__(self, input_size, output_size, num_bounding_box, inference,
      lambda_coord=6.0, lambda_size=6.0, lambda_no_obj=0.86,
      learning_rate=1e-3, noise=True):
    self.input_size = input_size
    self.output_size = output_size
    self.output_channel = 5 * num_bounding_box
    self.num_bounding_box = num_bounding_box
    self.lambda_coord = lambda_coord
    self.lambda_size = lambda_size
    self.lambda_no_obj = lambda_no_obj

    if hasattr(self, inference):
      self.inference_func = getattr(self, inference)
      self.inference = inference
    else:
      logger.error('no %s inference function is found.')
      sys.exit(1)

    self._setup_inputs()

    with tf.variable_scope('yolo'):
      if noise:
        preprocessed = self.preprocess_inputs(self.input_images)
        logits = self.inference_func(preprocessed)
        tf.summary.image('preprocessed_input_images', preprocessed)
      else:
        logits = self.inference_func(self.input_images)

      ind_index = [p * 5 for p in range(self.num_bounding_box)]
      x_index = [c * 5 + 1 for c in range(self.num_bounding_box)]
      y_index = [c * 5 + 2 for c in range(self.num_bounding_box)]
      w_index = [s * 5 + 3 for s in range(self.num_bounding_box)]
      h_index = [s * 5 + 4 for s in range(self.num_bounding_box)]
      logger.info(ind_index)
      logger.info(x_index)
      logger.info(y_index)
      logger.info(w_index)
      logger.info(h_index)
      ind = tf.gather(logits, ind_index, axis=3)
      x = tf.gather(logits, x_index, axis=3)
      y = tf.gather(logits, y_index, axis=3)
      w = tf.gather(logits, w_index, axis=3)
      h = tf.gather(logits, h_index, axis=3)

      tf.summary.image('input_images', self.input_images)

    # outputs
    with tf.variable_scope('yolo', reuse=True):
      outputs = self.inference_func(self.input_images)
      scale = []
      for i in range(self.num_bounding_box):
        scale += [1.0, self.input_size, self.input_size, self.input_size,
          self.input_size]
      scale = tf.constant(scale)
      self.output = tf.multiply(outputs, scale, name='prediction')

    with tf.name_scope('loss'):
      indicator = tf.gather(self.label_grids, ind_index, axis=3)
      x_label = tf.gather(self.label_grids, x_index, axis=3)
      y_label = tf.gather(self.label_grids, y_index, axis=3)
      w_label = tf.gather(self.label_grids, w_index, axis=3)
      h_label = tf.gather(self.label_grids, h_index, axis=3)
      tf.summary.image('indicator_prediction',
        tf.gather(ind, [0], axis=3))

      num_obj = tf.reduce_sum(indicator)
      with tf.name_scope('indicator'):
        indicator_error = ind - indicator
        no_obj = 1.0 - indicator
        num_empty_grid = tf.reduce_sum(no_obj)
        square_error = tf.square(indicator_error)
        self.ind_loss = tf.reduce_sum(indicator * square_error) / num_obj
        self.no_obj_loss = tf.reduce_sum(no_obj * square_error) / num_empty_grid

        tf.summary.scalar('indicator_loss', self.ind_loss)
        tf.summary.scalar('no_obj_loss', self.no_obj_loss)

      with tf.name_scope('coordinate'):
        self.coord_loss = (self.error_sum(indicator, x, x_label) +
          self.error_sum(indicator, y, y_label)) / num_obj
        tf.summary.scalar('coord_loss', self.coord_loss)

      with tf.name_scope('size'):
        self.size_loss = (self.error_sum(indicator, w, w_label) +
          self.error_sum(indicator, h, h_label)) / num_obj
        tf.summary.scalar('size_loss', self.size_loss)

      self.loss = self.ind_loss + \
          self.lambda_no_obj * self.no_obj_loss + \
          self.lambda_coord * self.coord_loss + \
          self.lambda_size * self.size_loss
      tf.summary.scalar('loss', self.loss)

    with tf.name_scope('optimization'):
      self.learning_rate = tf.Variable(learning_rate,
        trainable=False, name='learning_rate')
      self.decay_learning_rate = tf.assign(self.learning_rate,
        self.learning_rate * 0.9)
      tf.summary.scalar('learning_rate', self.learning_rate)

      optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
      self.train_ops = optimizer.minimize(self.loss)

    with tf.variable_scope('yolo', reuse=True):
      validation_logits = self.inference_func(self.valid_images)
      valid_indicator = tf.gather(self.valid_labels, ind_index, axis=3)
      valid_x_label = tf.gather(self.valid_labels, x_index, axis=3)
      valid_y_label = tf.gather(self.valid_labels, y_index, axis=3)
      valid_w_label = tf.gather(self.valid_labels, w_index, axis=3)
      valid_h_label = tf.gather(self.valid_labels, h_index, axis=3)

      valid_ind = tf.gather(validation_logits, ind_index, axis=3)
      valid_x = tf.gather(validation_logits, x_index, axis=3)
      valid_y = tf.gather(validation_logits, y_index, axis=3)
      valid_w = tf.gather(validation_logits, w_index, axis=3)
      valid_h = tf.gather(validation_logits, h_index, axis=3)

      tf.summary.image('valid_indicator_prediction',
        tf.gather(valid_ind, [0], axis=3))

    with tf.name_scope('evaluation'):
      self.indicator_accuracy, self.intersect_area = \
        self.evaluate(ind, indicator, x, x_label, y, y_label,
          w, w_label, h, h_label)
      tf.summary.scalar('indicator_accuracy', self.indicator_accuracy)
      tf.summary.scalar('intersect_area_ratio', self.intersect_area)

      self.valid_indicator_accuarcy, self.valid_intersect_area = \
        self.evaluate(valid_ind, valid_indicator,
          valid_x, valid_x_label, valid_y, valid_y_label,
          valid_w, valid_w_label, valid_h, valid_h_label)

      tf.summary.scalar('valid_indicator_accuracy',
        self.valid_indicator_accuarcy)
      tf.summary.scalar('valid_intersect_area_ratio',
        self.valid_intersect_area)

    with tf.name_scope('summary'):
      self.summary = tf.summary.merge_all()

  def _setup_inputs(self):
    self.input_images = tf.placeholder(dtype=tf.float32,
      shape=[None, self.input_size, self.input_size, 3], name='input_images')
    self.label_grids = tf.placeholder(dtype=tf.float32, name='labels',
      shape=[None, self.output_size, self.output_size, self.output_channel])

    # validation
    self.valid_images = tf.placeholder(dtype=tf.float32,
      shape=[None, self.input_size, self.input_size, 3], name='valid_images')
    self.valid_labels = tf.placeholder(dtype=tf.float32, name='valid_labels',
      shape=[None, self.output_size, self.output_size, self.output_channel])
    tf.summary.image('validation_images', self.valid_images)

    self.keep_prob = tf.placeholder(dtype=tf.float32,
      shape=[], name='keep_prob')

  def preprocess_inputs(self, inputs):
    random_noise = tf.random_normal_initializer(mean=0.0, stddev=6.0)
    noise = random_noise([self.input_size, self.input_size, 3])
    processed = tf.minimum(tf.maximum(inputs + noise, 0), 255)
    processed = tf.image.random_brightness(processed, max_delta=1e-3)
    processed = tf.image.random_contrast(processed, lower=0.0, upper=2.0)

    reshaped = tf.reshape(processed, [-1, self.input_size, 3])
    processed = tf.image.random_saturation(reshaped, lower=0.01, upper=2.0)
    processed = tf.reshape(processed, [-1, self.input_size, self.input_size, 3])
    return processed

  def inference_v0(self, inputs):
    ksize = 3
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 16, stride=1, kernel_size=ksize,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = tf.contrib.layers.conv2d(pool, 32, stride=1, kernel_size=ksize,
        weights_initializer=tf.variance_scaling_initializer())

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(64, ksize, pool, multiple=1)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(256, ksize, pool, multiple=1)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(384, ksize, pool, multiple=1)

    with tf.name_scope('pool5'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv6'):
      conv = self.multiple_conv(512, ksize, pool, multiple=1)

    with tf.name_scope('drop6'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, self.output_channel,
        stride=1, kernel_size=ksize, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return logits

  def inference_v1(self, inputs):
    ksize = 3
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 8, stride=1, kernel_size=ksize,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = tf.contrib.layers.conv2d(pool, 16, stride=1, kernel_size=ksize,
        weights_initializer=tf.variance_scaling_initializer())

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(128, ksize, pool, multiple=1)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(256, ksize, pool, multiple=2)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(384, ksize, pool, multiple=2)

    with tf.name_scope('drop5'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, self.output_channel,
        stride=1, kernel_size=ksize, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return logits

  def inference_v2(self, inputs):
    ksize = 3
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 8, stride=1, kernel_size=ksize,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = tf.contrib.layers.conv2d(pool, 16, stride=1, kernel_size=ksize,
        weights_initializer=tf.variance_scaling_initializer())

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(64, ksize, pool, multiple=1)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(128, ksize, pool)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(256, ksize, pool, multiple=3)

    with tf.name_scope('drop5'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, self.output_channel,
        stride=1, kernel_size=ksize, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return logits

  def inference_v3(self, inputs):
    ksize = 3
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 8, stride=1, kernel_size=ksize,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = tf.contrib.layers.conv2d(pool, 16, stride=1, kernel_size=ksize,
        weights_initializer=tf.variance_scaling_initializer())

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(64, ksize, pool, multiple=1)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(128, ksize, pool)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(256, ksize, pool)

    with tf.name_scope('drop5'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('conv6'):
      conv = self.multiple_conv(512, ksize, drop)

    with tf.name_scope('drop6'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, self.output_channel,
        stride=1, kernel_size=ksize, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return logits

  def inference_v4(self, inputs):
    ksize = 3
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 12, stride=1, kernel_size=ksize,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = self.multiple_conv(24, ksize, pool, multiple=0)

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(48, ksize, pool, multiple=0)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(96, ksize, pool, multiple=1)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(192, ksize, pool, multiple=2)

    with tf.name_scope('pool5'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv6'):
      conv = self.multiple_conv(384, ksize, pool, multiple=3)

    with tf.name_scope('pool6'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('drop6'):
      drop = tf.nn.dropout(pool, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, self.output_channel,
        stride=1, kernel_size=ksize, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return logits

  def inference_v5(self, inputs):
    ksize = 3
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 12, stride=1, kernel_size=ksize,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = self.multiple_conv(24, ksize, pool, multiple=0)

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(48, ksize, pool, multiple=1)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(96, ksize, pool)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(192, ksize, pool, multiple=3)

    with tf.name_scope('drop5'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, self.output_channel,
        stride=1, kernel_size=ksize, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return logits

  def inference_v6(self, inputs):
    ksize = 3
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 8, stride=1, kernel_size=ksize,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = tf.contrib.layers.conv2d(pool, 16, stride=1, kernel_size=ksize,
        weights_initializer=tf.variance_scaling_initializer())

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(64, ksize, pool, multiple=1)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(192, ksize, pool)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(384, ksize, pool, multiple=3)

    with tf.name_scope('drop5'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, self.output_channel,
        stride=1, kernel_size=ksize, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return logits

  def multiple_conv(self, size, ksize, inputs, multiple=2):
    conv = tf.contrib.layers.conv2d(inputs, size, stride=1, kernel_size=ksize,
      weights_initializer=tf.variance_scaling_initializer())

    for i in range(multiple):
      conv = tf.contrib.layers.conv2d(conv, size / 2, stride=1, kernel_size=1,
        weights_initializer=tf.variance_scaling_initializer())

      conv = tf.contrib.layers.conv2d(conv, size, stride=1, kernel_size=ksize,
        weights_initializer=tf.variance_scaling_initializer())

    return conv

  def error_sum(self, indicator, p, l):
    return tf.reduce_sum(indicator * tf.square(p - l))

  def predict_batch(self, sess, input_images):
    return sess.run(self.output, feed_dict={
      self.input_images: input_images,
      self.keep_prob: 1.0
    })

  def predict(self, sess, input_image):
    return sess.run(self.output, feed_dict={
      self.input_images: np.expand_dims(input_image, axis=0),
      self.keep_prob: 1.0
    })[0]

  def prepare_folder(self):
    index = 0
    folder = 'yolo_face-%s_%d' % (self.inference, index)
    while os.path.isdir(folder):
      index += 1
      folder = 'yolo_face-%s_%d' % (self.inference, index)
    os.mkdir(folder)
    return folder

  def evaluate(self,
      ind, ind_label,
      x, x_label,
      y, y_label,
      w, w_label,
      h, h_label):
    num_obj = tf.reduce_sum(ind_label)
    certain = tf.cast(tf.greater_equal(ind, 0.66), tf.float32)
    indicator_accuracy = tf.reduce_sum(
      ind_label * tf.cast(tf.equal(certain, ind_label), tf.float32)) / num_obj

    left = tf.maximum(x - w / 2.0, x_label - w_label / 2.0)
    bottom = tf.minimum(y + h / 2.0, y_label + h_label / 2.0)
    right = tf.minimum(x + w / 2.0, x_label + w_label / 2.0)
    top = tf.maximum(y - h / 2.0, y_label - h_label / 2.0)

    valid = tf.cast(tf.logical_and(tf.greater_equal(right, left),
      tf.greater_equal(bottom, top)), tf.float32)
    area = (right - left) * (bottom - top)
    true_area = tf.maximum(w_label * h_label, 1e-5)
    intersect_area = tf.reduce_sum(valid * area / true_area) / num_obj
    return indicator_accuracy, intersect_area


def train(args):
  loader = WIDERLoader(args.dbname)
  loader.load_training_data()
  loader.load_validation_data()

  yolo = YOLOFace(
    loader.get_input_size(),
    loader.get_output_size(),
    loader.get_num_bounding_box(),
    args.inference,
    lambda_coord=args.lambda_coord,
    lambda_size=args.lambda_size,
    lambda_no_obj=args.lambda_no_obj,
    learning_rate=args.learning_rate,
    noise=(args.noise == 'True'))

  if args.saving == 'True':
    folder = yolo.prepare_folder()
    checkpoint = os.path.join(folder, 'yolo')
    saver = tf.train.Saver()
    summary_writer = tf.summary.FileWriter(os.path.join(folder, 'summary'),
      tf.get_default_graph())

  training_data, training_label = loader.get_training_data()
  valid_data, valid_label = loader.get_validation_data()
  valid_index = 0
  training_data_size = len(training_data)
  valid_data_size = len(valid_data)
  logger.info('training data: %d, validation data: %d',
    training_data_size, valid_data_size)

  config = tf.ConfigProto()
  config.gpu_options.allow_growth = True
  with tf.Session(config=config) as sess:
    sess.run(tf.global_variables_initializer())

    offset = 0
    total_time = 0
    for epoch in range(args.max_epoches + 1):
      to = offset + args.batch_size

      training_data_batch = training_data[offset:to, :]
      training_label_batch = training_label[offset:to, :]

      if epoch == 0:
        prediction = sess.run(yolo.output, feed_dict={
          yolo.input_images: training_data_batch,
          yolo.label_grids: training_label_batch,
          yolo.keep_prob: 1.0,
        })
        logger.info('prediction stddev: %s',
          str(np.std(prediction.reshape([-1, yolo.output_channel]), axis=0)))

      if epoch % args.display_epoches == 0:
        valid_offset = valid_index
        to = valid_offset + args.batch_size
        valid_data_batch = valid_data[valid_offset:to, :]
        valid_label_batch = valid_label[valid_offset:to, :]

        losses_tensor = [yolo.loss, yolo.ind_loss, yolo.no_obj_loss,
          yolo.coord_loss, yolo.size_loss,
          yolo.indicator_accuracy, yolo.intersect_area,
          yolo.valid_indicator_accuarcy, yolo.valid_intersect_area]
        losses = sess.run(losses_tensor, feed_dict={
            yolo.input_images: training_data_batch,
            yolo.label_grids: training_label_batch,
            yolo.valid_images: valid_data_batch,
            yolo.valid_labels: valid_label_batch,
            yolo.keep_prob: 1.0,
          })
        logger.info('%d. loss: %f, ind: %f, no-obj: %f, coord: %f, size: %f',
          epoch, losses[0], losses[1], losses[2], losses[3], losses[4])
        logger.info('train: %f, %f, valid: %f, %f',
          losses[5], losses[6], losses[7], losses[8])

        valid_index = (valid_index + args.batch_size) % (valid_data_size -
          args.batch_size)

        ave = total_time / (epoch + 1)
        time_remaining = (args.max_epoches - epoch) * ave
        days = int(time_remaining / 86400)
        time_remaining %= 86400
        hours = int(time_remaining / 3600)
        time_remaining %= 3600
        minutes = int(time_remaining / 60)
        time_remaining %= 60
        seconds = int(time_remaining)
        logger.info('time remaining: %d days %d hours %d minutes %d seconds' %
          (days, hours, minutes, seconds))

      if epoch % args.save_epoches == 0 and epoch != 0 and args.saving == 'True':
        saver.save(sess, checkpoint, global_step=epoch)

      if epoch % args.summary_epoches == 0 and epoch != 0 and \
          args.saving == 'True':
        valid_offset = valid_index
        to = valid_index + args.batch_size
        valid_data_batch = valid_data[valid_offset:to, :]
        valid_label_batch = valid_label[valid_offset:to, :]

        summary = sess.run(yolo.summary, feed_dict={
          yolo.input_images: training_data_batch,
          yolo.label_grids: training_label_batch,
          yolo.valid_images: valid_data_batch,
          yolo.valid_labels: valid_label_batch,
          yolo.keep_prob: 1.0,
        })
        summary_writer.add_summary(summary, global_step=epoch)

      start = time.time()
      sess.run(yolo.train_ops, feed_dict={
        yolo.input_images: training_data_batch,
        yolo.label_grids: training_label_batch,
        yolo.keep_prob: args.keep_prob,
      })
      passed = time.time() - start
      total_time += passed

      if epoch % args.decay_epoches == 0 and epoch != 0:
        logger.info('decay learning rate...')
        sess.run(yolo.decay_learning_rate)

      offset += args.batch_size
      if offset >= training_data_size - args.batch_size and \
          offset < training_data_size:
        offset = training_data_size - args.batch_size
      elif offset >= training_data_size:
        offset = 0


def fit(original, size):
  w, h = original.size
  output_image = Image.new('RGB', (size, size))
  if w >= h:
    scale = float(size) / w
    oh = int(h * scale)
    output_image.paste(original.resize((size, oh)),
      (0, (size - oh) / 2))
  else:
    scale = float(size) / h
    ow = int(w * scale)
    output_image.paste(original.resize((ow, size)),
      ((size - ow) / 2, 0))
  return np.array(output_image, np.uint8)


def inference(args):
  with tf.device('/:cpu0'):
    yolo = YOLOFace(args.test_input_size, args.test_output_size,
      args.test_num_bounding_box, args.inference)

  config = tf.ConfigProto()
  config.gpu_options.allow_growth = True
  with tf.Session(config=config) as sess:
    sess.run(tf.global_variables_initializer())

    evalutated = False
    try:
      import cv2
      camera = cv2.VideoCapture(0)

      count = 0
      total_time = 0
      while True:
        _, img = camera.read(0)
        img = fit(Image.fromarray(img), yolo.input_size)

        start = time.time()
        output = yolo.predict(sess, img)
        if not evalutated:
          logger.info('output mean: %s',
            np.mean(np.reshape(output, [-1, 5]), axis=0))
          logger.info('output stddev: %s',
            np.std(np.reshape(output, [-1, 5]), axis=0))
          evalutated = True
        passed = time.time() - start
        total_time += passed
        count += 1

        cv2.imshow('Image', img)
        print('\raverage: %f' % (total_time / count), end='')
        sys.stdout.flush()

        key = cv2.waitKey(10)
        if key == ord('q'):
          break

    except Exception as e:
      logger.error(e)


def main():
  parser = ArgumentParser()
  parser.add_argument('--dbname', dest='dbname', type=str,
    default='wider.sqlite3', help='sqlite3 db to load')

  parser.add_argument('--inference', dest='inference', type=str,
    default='inference_v0', help='model version to use')

  parser.add_argument('--mode', dest='mode', type=str,
    default='train', help='mode to run')

  # training parameters
  parser.add_argument('--noise', dest='noise',
    default='True', help='add noise')
  parser.add_argument('--learning-rate', dest='learning_rate', type=float,
    default=1e-3, help='learning rate for training')
  parser.add_argument('--lambda-coord', dest='lambda_coord', type=float,
    default=6.0, help='coefficient for coordinate loss')
  parser.add_argument('--lambda-size', dest='lambda_size', type=float,
    default=6.0, help='coefficient for size loss')
  parser.add_argument('--lambda-no-obj', dest='lambda_no_obj', type=float,
    default=0.86, help='coefficient for no-obj loss')
  parser.add_argument('--batch-size', dest='batch_size', type=int,
    default=32, help='batch size for training')
  parser.add_argument('--max-epoches', dest='max_epoches', type=int,
    default=100000, help='max epoches to train')
  parser.add_argument('--display-epoches', dest='display_epoches', type=int,
    default=10, help='epoches to display training result')
  parser.add_argument('--save-epoches', dest='save_epoches', type=int,
    default=10000, help='epoches to save training result')
  parser.add_argument('--summary-epoches', dest='summary_epoches', type=int,
    default=10, help='epoches to save training summary')
  parser.add_argument('--decay-epoches', dest='decay_epoches', type=int,
    default=10000, help='epoches to decay learning rate for training')
  parser.add_argument('--keep-prob', dest='keep_prob', type=float,
    default=0.8, help='keep probability for dropout')
  parser.add_argument('--saving', dest='saving', type=str,
    default='False', help='rather to save the training result')

  parser.add_argument('--test-input-size', dest='test_input_size',
    default=224, type=int, help='input size for inference mode')
  parser.add_argument('--test-output-size', dest='test_output_size',
    default=14, type=int, help='output size for inference mode')
  parser.add_argument('--test-num-bounding-box', dest='test_num_bounding_box',
    default=1, type=int, help='output size for inference mode')

  args = parser.parse_args()

  if args.mode == 'train':
    train(args)
  elif args.mode == 'inference':
    inference(args)


if __name__ == '__main__':
  main()
