import tensorflow as tf
import numpy as np
import sys
import coloredlogs
import logging
import os
from argparse import ArgumentParser

from cifar10_data_util import CIFAR10Data


coloredlogs.install()
logging.basicConfig()
logger = logging.getLogger('cifar10')
logger.setLevel(logging.INFO)


class CIFAR10Model(object):
  def __init__(self, inference, learning_rate=1e-3, lambda_reg=1e-4):
    with tf.name_scope('inputs'):
      self._setup_inputs()
      tf.summary.image('input_images', self.input_images)

    self.inference = inference
    if hasattr(self, inference):
      inference_fn = getattr(self, inference)
    else:
      sys.exit(1)
    with tf.variable_scope('cifar10'):
      logits, self.outputs = inference_fn(self.input_images)

    with tf.variable_scope('cifar10', reuse=True):
      _, self.validation_output = inference_fn(self.validation_images)

    with tf.name_scope('loss'):
      self.loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
        logits=logits, labels=self.labels))

      train_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, 'cifar10')
      assert len(train_vars) != 0
      reg = 0
      for i in range(len(train_vars)):
        reg += tf.reduce_sum(tf.square(train_vars[i]))

      self.loss += lambda_reg * reg
      tf.summary.scalar('loss', self.loss)

    with tf.name_scope('optimization'):
      self.learning_rate = tf.Variable(learning_rate, trainable=False,
        name='learning_rate')
      self.decay_lr = tf.assign(self.learning_rate,
        self.learning_rate * 0.9, name='decay_learning_rate')
      tf.summary.scalar('learning_rate', self.learning_rate)
      optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
      self.train_ops = optimizer.minimize(self.loss)

    with tf.name_scope('evaluation'):
      self.train_accuracy = self.evaluate(self.outputs, self.labels)
      self.validation_accuracy = self.evaluate(self.validation_output,
        self.validation_labels)
      tf.summary.scalar('train_accuarcy', self.train_accuracy)
      tf.summary.scalar('validation_accuracy', self.validation_accuracy)

    with tf.name_scope('summary'):
      self.summary = tf.summary.merge_all()

  def _setup_inputs(self):
    self.input_images = tf.placeholder(dtype=tf.float32, name='input_images',
      shape=[None, 32, 32, 3])
    self.labels = tf.placeholder(dtype=tf.float32, name='labels',
      shape=[None, 10])

    self.keep_prob = tf.placeholder(dtype=tf.float32, name='keep_prob',
      shape=())

    self.validation_images = tf.placeholder(dtype=tf.float32,
      name='validation_images', shape=[None, 32, 32, 3])
    self.validation_labels = tf.placeholder(dtype=tf.float32,
      name='validation_labels', shape=[None, 10])

  def inference_v0(self, inputs):
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 64, stride=1, kernel_size=3,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('drop2'):
      drop = tf.nn.dropout(pool, keep_prob=self.keep_prob)

    with tf.name_scope('conv2'):
      conv = self.multiple_conv(drop, 128, multiples=0)

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(drop, 256, multiples=0)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(drop, 512, multiples=1)

    with tf.name_scope('drop4'):
      drop = tf.nn.dropout(conv, keep_prob=self.keep_prob)

    with tf.name_scope('fully_connected'):
      connect_shape = drop.get_shape().as_list()
      connect_size = connect_shape[1] * connect_shape[2] * connect_shape[3]
      fc = tf.contrib.layers.fully_connected(
        tf.reshape(drop, [-1, connect_size]), 1024,
        weights_initializer=tf.variance_scaling_initializer())

    with tf.name_scope('output'):
      logits = tf.contrib.layers.fully_connected(fc, 10,
        activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
      output = tf.nn.softmax(logits, name='prediction')
    return logits, output

  def inference_v1(self, inputs):
    with tf.name_scope('conv1'):
      conv = tf.contrib.layers.conv2d(inputs, 256, stride=1, kernel_size=5,
        weights_initializer=tf.random_normal_initializer(stddev=0.006))

    with tf.name_scope('pool1'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv2'):
      conv = self.multiple_conv(pool, 384, multiples=1, ksize=3)

    with tf.name_scope('pool2'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv3'):
      conv = self.multiple_conv(pool, 384, multiples=1, ksize=3)

    with tf.name_scope('pool3'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv4'):
      conv = self.multiple_conv(pool, 512, multiples=1, ksize=3)

    with tf.name_scope('pool4'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('conv5'):
      conv = self.multiple_conv(pool, 1024, multiples=1, ksize=1)

    with tf.name_scope('pool5'):
      pool = tf.contrib.layers.max_pool2d(conv, 2)

    with tf.name_scope('drop5'):
      drop = tf.nn.dropout(pool, keep_prob=self.keep_prob)

    with tf.name_scope('output'):
      logits = tf.contrib.layers.conv2d(drop, 10,
        stride=1, kernel_size=1,
        activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
      logits = tf.reshape(logits, [-1, 10])
      output = tf.nn.softmax(logits, name='prediction')
    return logits, output

  def multiple_conv(self, inputs, output_size, ksize=3, multiples=1):
    conv = tf.contrib.layers.conv2d(inputs, output_size,
      stride=1, kernel_size=ksize,
      weights_initializer=tf.variance_scaling_initializer())
    for i in range(multiples):
      conv = tf.contrib.layers.conv2d(conv, output_size / 2,
        stride=1, kernel_size=1,
        weights_initializer=tf.variance_scaling_initializer())
      conv = tf.contrib.layers.conv2d(conv, output_size,
        stride=1, kernel_size=ksize,
        weights_initializer=tf.variance_scaling_initializer())
    return conv

  def evaluate(self, prediction, labels):
    p = tf.argmax(prediction, axis=1)
    l = tf.argmax(labels, axis=1)
    return tf.reduce_mean(tf.cast(tf.equal(p, l), tf.float32))

  def prepare_folder(self):
    index = 0
    folder = 'cifar10_%s_%d' % (self.inference, index)
    while os.path.isdir(folder):
      index += 1
      folder = 'cifar10_%s_%d' % (self.inference, index)
    os.mkdir(folder)
    return folder


def train(args):
  logger.info('setting up models...')
  model = CIFAR10Model(args.inference, learning_rate=args.learning_rate)

  logger.info('preparing cifar10 data...')
  cifar10_data = CIFAR10Data(args.dbname)
  training_data, training_label = cifar10_data.get_training_data()
  valid_data, valid_label = cifar10_data.get_validation_data()
  training_data = np.concatenate([training_data, valid_data], axis=0)
  training_label = np.concatenate([training_label, valid_label], axis=0)

  valid_data, valid_label = cifar10_data.get_test_data()

  logger.info('training data: %s', str(training_data.shape))
  logger.info('validation data: %s', str(valid_data.shape))

  saving = (args.saving == 'True')

  if saving:
    folder = model.prepare_folder()
    checkpoint = os.path.join(folder, 'cifar10')
    saver = tf.train.Saver()
    summary_writer = tf.summary.FileWriter(os.path.join(folder, 'summary'),
      tf.get_default_graph())

  config = tf.ConfigProto()
  config.gpu_options.allow_growth = True
  with tf.Session(config=config) as sess:
    logger.info('initializing variables...')
    sess.run(tf.global_variables_initializer())

    training_size = len(training_data)
    valid_size = len(valid_data)
    valid_index = 0
    offset = 0
    for epoch in range(args.max_epoches + 1):
      training_data_batch = training_data[offset:offset+args.batch_size, :]
      training_label_batch = training_label[offset:offset+args.batch_size, :]

      offset += args.batch_size
      if offset >= training_size - args.batch_size and offset < training_size:
        offset = training_size - args.batch_size
      elif offset >= training_size:
        offset = 0

      if epoch % args.display_epoches == 0:
        to = valid_index + args.batch_size
        valid_data_batch = valid_data[valid_index:to, :]
        valid_label_batch = valid_label[valid_index:to, :]

        loss, train, valid = sess.run(
          [model.loss, model.train_accuracy, model.validation_accuracy],
          feed_dict={
            model.input_images: training_data_batch,
            model.labels: training_label_batch,
            model.validation_images: valid_data_batch,
            model.validation_labels: valid_label_batch,
            model.keep_prob: 1.0
          })

        valid_index = to % (valid_size - args.batch_size)

        logger.info('%d. loss: %f, train: %f, valid: %f',
          epoch, loss, train, valid)

      # train the model
      sess.run(model.train_ops, feed_dict={
        model.input_images: training_data_batch,
        model.labels: training_label_batch,
        model.keep_prob: args.keep_prob,
      })

      if epoch % args.save_epoches == 0 and epoch != 0 and saving:
        saver.save(sess, checkpoint, global_step=epoch)

      if epoch % args.summary_epoches == 0 and epoch != 0 and saving:
        summary = sess.run(model.summary, feed_dict={
          model.input_images: training_data_batch,
          model.labels: training_label_batch,
          model.validation_images: valid_data_batch,
          model.validation_labels: valid_label_batch,
          model.keep_prob: 1.0
        })
        summary_writer.add_summary(summary, global_step=epoch)

      if epoch % args.decay_epoches == 0 and epoch != 0:
        sess.run(model.decay_lr)


def main():
  parser = ArgumentParser()

  parser.add_argument('--dbname', dest='dbname', default='cifar.sqlite3',
    help='dbname to load for training')

  parser.add_argument('--inference', dest='inference',
    default='inference_v0', help='inference function to use')

  parser.add_argument('--learning-rate', dest='learning_rate', type=float,
    default=1e-3, help='learning rate for training')
  parser.add_argument('--batch-size', dest='batch_size', type=int,
    default=64, help='batch size for training')
  parser.add_argument('--max-epoches', dest='max_epoches', type=int,
    default=300000, help='max epoches to train')
  parser.add_argument('--display-epoches', dest='display_epoches', type=int,
    default=10, help='epoches to display training result')
  parser.add_argument('--save-epoches', dest='save_epoches', type=int,
    default=10000, help='epoches to save training result')
  parser.add_argument('--summary-epoches', dest='summary_epoches', type=int,
    default=10, help='epoches to save training summary')
  parser.add_argument('--decay-epoches', dest='decay_epoches', type=int,
    default=20000, help='epoches to decay learning rate for training')
  parser.add_argument('--keep-prob', dest='keep_prob', type=float,
    default=0.8, help='keep probability for dropout')
  parser.add_argument('--saving', dest='saving', type=str,
    default='False', help='rather to save the training result')
  args = parser.parse_args()

  train(args)


if __name__ == '__main__':
  main()
