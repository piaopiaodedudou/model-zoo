import tensorflow as tf
import numpy as np
import gym
import logging
import random
import os
from argparse import ArgumentParser
from collections import deque


LEARNING_RATE = 1e-3
TAU = 1e0
DISCOUNT_FACTOR = 0.9
UPDATE_FREQ = 200


logging.basicConfig()
logger = logging.getLogger('CartPole v0')
logger.setLevel(logging.INFO)


class QFunction(object):
  def __init__(self):
    with tf.device('/cpu:0'):
      self.learning_rate = tf.Variable(LEARNING_RATE, trainable=False)
    self._setup_inputs()

    with tf.variable_scope('train'):
      self.train_q_value = self._inference(True)
    with tf.variable_scope('target'):
      self.target_q_value = self._inference(False)
    self._setup_update_ops()

    with tf.name_scope('loss'):
      not_done = tf.cast(tf.logical_not(self.done), tf.float32)
      target = self.reward + DISCOUNT_FACTOR * not_done * self.next_q_values
      train_q = tf.reduce_max(self.action * self.train_q_value, axis=1)
      self.loss = tf.reduce_mean(tf.square(target - train_q))
    with tf.name_scope('train_ops'):
      optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
      self.train_ops = optimizer.minimize(self.loss)

  def _setup_inputs(self):
    self.state = tf.placeholder(dtype=tf.float32,
      shape=[None, 4], name='state')
    self.action = tf.placeholder(dtype=tf.float32,
      shape=[None, 2], name='action')
    self.reward = tf.placeholder(dtype=tf.float32,
      shape=[None], name='reward')
    self.done = tf.placeholder(dtype=tf.bool,
      shape=[None], name='done')
    self.next_q_values = tf.placeholder(dtype=tf.float32,
      shape=[None], name='max_q')

  def _inference(self, trainable):
    with tf.name_scope('hidden1'):
      h = tf.contrib.layers.fully_connected(self.state, 64,
        trainable=trainable,
        weights_initializer=tf.random_normal_initializer(stddev=0.1))

    with tf.name_scope('hidden2'):
      h = tf.contrib.layers.fully_connected(h, 64,
        trainable=trainable,
        weights_initializer=tf.variance_scaling_initializer())

    with tf.name_scope('output'):
      q_value = tf.contrib.layers.fully_connected(h, 2,
        trainable=trainable, activation_fn=None,
        weights_initializer=tf.variance_scaling_initializer())
    return q_value

  def _setup_update_ops(self):
    train_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, 'train')
    target_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, 'target')
    with tf.name_scope('update'):
      self.update_target_vars = []
      for i in range(len(train_vars)):
        self.update_target_vars.append(tf.assign(target_vars[i],
          target_vars[i] * (1.0 - TAU) + train_vars[i] * TAU))
    with tf.name_scope('copy'):
      self.copy_vars = []
      for i in range(len(train_vars)):
        self.update_target_vars.append(
          tf.assign(target_vars[i], train_vars[i]))

  def predict(self, sess, state):
    return sess.run(self.target_q_value, feed_dict={
      self.state: np.expand_dims(state, axis=0)
    })

  def predict_batch(self, sess, state_batch):
    return sess.run(self.target_q_value, feed_dict={
      self.state: state_batch
    })

  def train(self, sess, state, action, reward, done, next_q_values):
    _, loss = sess.run([self.train_ops, self.loss], feed_dict={
      self.state: state,
      self.action: action,
      self.reward: reward,
      self.done: done,
      self.next_q_values: next_q_values
    })
    return loss

  def update_target(self, sess):
    sess.run(self.update_target_vars)

  def copy_weights(self, sess):
    sess.run(self.copy_vars)


def epsilon_greedy(q_value, epsilon):
  action_prob = np.ones(shape=[2], dtype=np.float32) * epsilon / 2.0
  index = np.argmax(q_value)
  action_prob[index] += (1.0 - epsilon)
  return np.random.choice(np.arange(2), p=action_prob)


def setup_checkpoint(model_name):
  index = 0
  model_path = '%s_%d' % (model_name, index)
  while os.path.isdir(model_path):
    index += 1
    model_path = '%s_%d' % (model_name, index)
  logger.info('creating model path: %s ...' % (model_path))
  os.mkdir(model_path)
  return os.path.join(model_path, model_name)


def train(model_name,
    max_epoch, display_epoch, display_episode, save_epoch, max_step, batch_size,
    replay_buffer_size,
    render=False, saving=False):
  q_function = QFunction()

  if saving:
    saver = tf.train.Saver()
    checkpoint_path = setup_checkpoint(model_name)

  config = tf.ConfigProto()
  config.gpu_options.allocator_type = 'BFC'
  with tf.Session(config=config) as sess:
    logger.info('initializing variables...')
    sess.run(tf.global_variables_initializer())

    q_function.copy_weights(sess)

    logger.info('initializing environments...')
    env = gym.make('CartPole-v0')

    epsilon = 0.9
    train_epoch = 0
    replay_buffer = deque()
    logger.info('start training...')
    for epoch in range(max_epoch + 1):
      state = env.reset()
      total_reward = 0
      if epoch % display_episode == 0 and epoch != 0:
        for step in range(max_step):
          q_value = q_function.predict(sess, state)
          action = np.argmax(q_value)
          next_state, reward, done, _ = env.step(action)
          #  replay_buffer.append((state, action, reward, next_state, done))
          total_reward += reward
          state = next_state

          if render:
            env.render()

          if done:
            logger.info('total reward: %d' % (total_reward))
            break
      else:
        for step in range(max_step):
          q_value = q_function.predict(sess, state)
          action = epsilon_greedy(q_value, epsilon)
          next_state, reward, done, _ = env.step(action)
          replay_buffer.append((state, action, reward, next_state, done))
          state = next_state

          if len(replay_buffer) >= batch_size:
            state_batch = []
            action_batch = []
            reward_batch = []
            next_state_batch = []
            done_batch = []

            if train_epoch % save_epoch == 0 and train_epoch != 0:
              epsilon *= 0.9

            for i in range(batch_size):
              batch = random.choice(replay_buffer)
              state_batch.append(batch[0])
              action_batch.append([1 if a == batch[1] else 0
                for a in range(2)])
              reward_batch.append(batch[2])
              done_batch.append(batch[4])
              next_state_batch.append(batch[3])
            state_batch = np.array(state_batch)
            action_batch = np.array(action_batch)
            reward_batch = np.array(reward_batch)
            next_state_batch = np.array(next_state_batch)
            done_batch = np.array(done_batch)

            next_q_values = q_function.predict_batch(sess, next_state_batch)
            #  next_q_values = next_q_values[action_batch == 1]
            next_q_values = np.max(next_q_values, axis=1)
            loss = q_function.train(sess, state_batch, action_batch,
              reward_batch, done_batch, next_q_values)
            train_epoch += 1

            if train_epoch % UPDATE_FREQ == 0:
              q_function.update_target(sess)

            if train_epoch % save_epoch == 0 and train_epoch != 0 and saving:
              logger.info('saving checkpoint %s ...' % (checkpoint_path))
              saver.save(sess, checkpoint_path, global_step=train_epoch)

            while len(replay_buffer) > replay_buffer_size * 3:
              replay_buffer.pop()

            if train_epoch % display_epoch == 0:
              logger.info('%d. episode: %d, loss: %f | QMax: %f, epsilon: %f' %
                (train_epoch, epoch, loss, np.amax(next_q_values), epsilon))

          if done:
            break


def main():
  parser = ArgumentParser()
  parser.add_argument('--max-epoch', dest='max_epoch',
    default=60000, type=int, help='max epoch for training')
  parser.add_argument('--display-episode', dest='display_episode',
    default=10, type=int, help='episode for display')
  parser.add_argument('--save-epoch', dest='save_epoch',
    default=2000, type=int, help='epoch for saving session')
  parser.add_argument('--max-step', dest='max_step',
    default=500, type=int, help='max step for each episode')
  parser.add_argument('--batch-size', dest='batch_size',
    default=128, type=int, help='batch size')
  parser.add_argument('--replay-buffer-size', dest='replay_buffer_size',
    default=1000, type=int, help='replay buffer size')
  parser.add_argument('--render', dest='render',
    default=False, type=bool, help='whether to display animation')
  parser.add_argument('--saving', dest='saving',
    default=False, type=bool, help='whether to save model')
  parser.add_argument('--model-name', dest='model_name',
    default='cart_dqn', help='model name')
  parser.add_argument('--display-epoch', dest='display_epoch',
    default=200, type=int, help='epoch for display')
  args = parser.parse_args()

  train(args.model_name,
    args.max_epoch,
    args.display_epoch, args.display_episode,
    args.save_epoch, args.max_step,
    args.batch_size, args.replay_buffer_size,
    render=args.render, saving=args.saving)


if __name__ == '__main__':
  main()
