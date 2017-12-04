import tensorflow as tf
import numpy as np
import logging
import os
from argparse import ArgumentParser

from plant_loader import PlantLoader


logging.basicConfig()
logger = logging.getLogger('plants')
logger.setLevel(logging.INFO)


class PlantRecognizer(object):
  def __init__(self, learning_rate, input_width, input_height, output_size):
    self.learning_rate = tf.Variable(learning_rate, trainable=False)
    self.input_width = input_width
    self.input_height = input_height
    self.output_size = output_size

    self._setup_inputs()

    with tf.variable_scope('inference'):
      logits, self.outputs = self._inference(self.inputs)

    with tf.name_scope('loss'):
      self.loss = tf.reduce_mean(
        tf.nn.softmax_cross_entropy_with_logits(logits=logits,
        labels=self.labels))
      tf.summary.scalar(name='loss', tensor=self.loss)

    with tf.name_scope('optimization'):
      self.global_step = tf.contrib.framework.get_or_create_global_step()
      optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
      self.train_ops = optimizer.minimize(self.loss,
        global_step=self.global_step)

    with tf.variable_scope('inference', reuse=True):
      _, self.validation_outputs = self._inference(self.validation_inputs)

    with tf.name_scope('evalutation'):
      self.validation_accuracy = self.evaluate(self.validation_outputs,
        self.validation_labels)
      self.accuracy = self.evaluate(self.outputs, self.labels)

      tf.summary.scalar(name='validation_accuracy',
        tensor=self.validation_accuracy)
      tf.summary.scalar(name='training_accuracy', tensor=self.accuracy)

    with tf.name_scope('summary'):
      self.summary = tf.summary.merge_all()

  def evaluate(self, outputs, labels):
    prediction = tf.argmax(outputs, axis=1)
    answer = tf.argmax(labels, axis=1)
    accuracy = tf.reduce_mean(
      tf.cast(tf.equal(prediction, answer), tf.float32)) * 100.0
    return accuracy

  def _setup_inputs(self):
    with tf.device('/:cpu0'):
      self.inputs = tf.placeholder(dtype=tf.float32, name='image_inputs',
        shape=[None, self.input_height, self.input_width, 3])
      self.labels = tf.placeholder(dtype=tf.float32, name='labels',
        shape=[None, self.output_size])

      self.validation_inputs = tf.placeholder(dtype=tf.float32,
        name='valid_inputs',
        shape=[None, self.input_height, self.input_width, 3])
      self.validation_labels = tf.placeholder(dtype=tf.float32,
        name='valid_labels', shape=[None, self.output_size])
      tf.summary.image(name='input', tensor=self.inputs)
      tf.summary.image(name='validation_input', tensor=self.validation_inputs)

  def _inference(self, inputs):
    with tf.name_scope('conv1'):
      conv1 = tf.contrib.layers.conv2d(inputs, 32, stride=1, kernel_size=5,
        weights_initializer=tf.random_normal_initializer(stddev=0.0004))
      pool1 = tf.contrib.layers.max_pool2d(conv1, 2)

    with tf.name_scope('conv2'):
      conv2 = tf.contrib.layers.conv2d(pool1, 64, stride=1, kernel_size=5,
        weights_initializer=tf.random_normal_initializer(stddev=0.03))
      pool2 = tf.contrib.layers.max_pool2d(conv2, kernel_size=2)

    with tf.name_scope('conv3'):
      conv3 = tf.contrib.layers.conv2d(pool2, 128, stride=1, kernel_size=5,
        weights_initializer=tf.random_normal_initializer(stddev=0.03))
      pool3 = tf.contrib.layers.max_pool2d(conv3, kernel_size=2)

    with tf.name_scope('fully_connected'):
      connect_shape = pool3.get_shape().as_list()
      connect_size = connect_shape[1] * connect_shape[2] * connect_shape[3]
      fc = tf.contrib.layers.fully_connected(
        tf.reshape(pool3, [-1, connect_size]), 4096,
        weights_initializer=tf.random_normal_initializer(stddev=0.02))

    with tf.name_scope('output'):
      logits = tf.contrib.layers.fully_connected(fc, self.output_size,
        activation_fn=None)
      outputs = tf.nn.softmax(logits)
    return logits, outputs


def prepare_folder():
  index = 0
  folder = 'plant-recognizer_%d' % index
  while os.path.isdir(folder):
    index += 1
    folder = 'plant-recognizer_%d' % index
  os.mkdir(folder)
  return folder


def train(dbname, args):
  loader = PlantLoader(dbname)
  recognizer = PlantRecognizer(
    args.learning_rate, loader.get_width(), loader.get_height(),
    loader.get_output_size())

  if args.load_all:
    training_data = loader.get_data()
    training_labels = loader.get_label()
  else:
    training_data = loader.get_training_data()
    training_labels = loader.get_training_labels()

  validation_data = loader.get_validation_data()
  validation_labels = loader.get_validation_labels()

  if args.saving:
    saver = tf.train.Saver()
    folder = prepare_folder()
    checkpoint = os.path.join(folder, 'plant-recognizer')

    summary_writer = tf.summary.FileWriter(os.path.join(folder, 'summary'),
      graph=tf.get_default_graph())

  with tf.Session() as sess:
    sess.run(tf.global_variables_initializer())

    data_size = len(training_data)
    batch_size = args.batch_size
    for epoch in range(args.max_epoches + 1):
      offset = epoch % (data_size - batch_size)

      data_batch = training_data[offset:offset+batch_size, :]
      label_batch = training_labels[offset:offset+batch_size, :]
      #  data_batch = validation_data[offset:offset+batch_size, :]
      #  label_batch = validation_labels[offset:offset+batch_size, :]

      if epoch % args.display_epoch == 0:
        o = epoch % (len(validation_data) - batch_size)
        tensor = [recognizer.loss, recognizer.accuracy,
          recognizer.validation_accuracy]
        loss, train_accuracy, valid_accuarcy = sess.run(tensor,
          feed_dict={
            recognizer.inputs: data_batch,
            recognizer.labels: label_batch,
            recognizer.validation_inputs: validation_data[o:o+batch_size, :],
            recognizer.validation_labels: validation_labels[o:o+batch_size, :],
          })
        logger.info('%d. loss: %f | training : %f | validation: %f' %
          (epoch, loss, train_accuracy, valid_accuarcy))

      sess.run(recognizer.train_ops, feed_dict={
        recognizer.inputs: data_batch,
        recognizer.labels: label_batch,
      })

      if epoch % args.save_epoch == 0 and args.saving and epoch != 0:
        saver.save(sess, checkpoint, global_step=epoch)
        summary = sess.run(recognizer.summary,
          feed_dict={
            recognizer.inputs: data_batch,
            recognizer.labels: label_batch,
            recognizer.validation_inputs: validation_data[o:o+batch_size, :],
            recognizer.validation_labels: validation_labels[o:o+batch_size, :],
          })
        summary_writer.add_summary(summary, global_step=epoch)


def main():
  parser = ArgumentParser()
  parser.add_argument('--dbname', dest='dbname', default='plants.sqlite3',
    type=str, help='db to load')
  parser.add_argument('--mode', dest='mode', default='train',
    type=str, help='train/test')

  parser.add_argument('--load-all', dest='load_all', default=False,
    type=bool, help='load all data to train')

  parser.add_argument('--learning-rate', dest='learning_rate', default=1e-3,
    type=float, help='learning rate to train model')
  parser.add_argument('--max-epoches', dest='max_epoches', default=60000,
    type=int, help='max epoches to train model')
  parser.add_argument('--display-epoches', dest='display_epoch', default=100,
    type=int, help='epoches to evaluation')
  parser.add_argument('--save-epoches', dest='save_epoch', default=1000,
    type=int, help='epoches to save model')
  parser.add_argument('--batch-size', dest='batch_size', default=64,
    type=int, help='batch size to train model')
  parser.add_argument('--saving', dest='saving', default=False,
    type=bool, help='rather to save model or not')
  args = parser.parse_args()

  if args.mode == 'train':
    train(args.dbname, args)


if __name__ == '__main__':
  main()