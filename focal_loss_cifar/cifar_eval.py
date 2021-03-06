# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datetime import datetime
import math
import time

import numpy as np
import tensorflow as tf
slim = tf.contrib.slim
import cifar

import util
parser = cifar.parser

parser.add_argument('--loss_type', type=str, default = 'focal_loss', 
                    help = 'loss type')

parser.add_argument('--eval_dir', type=str, 
                    help='Directory where to write event logs.')

parser.add_argument('--eval_data', type=str, default='test',
                    help='Either `test` or `train_eval`.')

parser.add_argument('--checkpoint_dir', type=str, 
                    help='Directory where to read model checkpoints.')


parser.add_argument('--num_examples', type=int, default=10000,
                    help='Number of examples to run.')

parser.add_argument('--run_once', type=bool, default=False,
                    help='Whether to run eval only once.')


def eval_once(saver, summary_writer, num_pos, num_tp, num_fp, summary_op):
  """Run Eval once.

  Args:
    saver: Saver.
    summary_writer: Summary writer.
    corrects: Top K op.
    summary_op: Summary op.
  """

  with tf.Session(config = util.tf.gpu_config(allow_growth = True)) as sess:
    ckpt = tf.train.get_checkpoint_state(FLAGS.checkpoint_dir)
    if ckpt and ckpt.model_checkpoint_path:
      # Restores from checkpoint
      saver.restore(sess, ckpt.model_checkpoint_path)
      # Assuming model_checkpoint_path looks something like:
      #   /my-favorite-path/cifar_train/model.ckpt-0,
      # extract global_step from it.
      global_step = ckpt.model_checkpoint_path.split('/')[-1].split('-')[-1]
    else:
      print('No checkpoint file found')
      return

    # Start the queue runners.
    coord = tf.train.Coordinator()
    try:
      threads = []
      for qr in tf.get_collection(tf.GraphKeys.QUEUE_RUNNERS):
        threads.extend(qr.create_threads(sess, coord=coord, daemon=True,
                                         start=True))

      num_iter = int(math.ceil(FLAGS.num_examples / FLAGS.batch_size))
      label_count = 0  # Counts the number of correct predictions.
      tp_count = 0
      fp_count = 0
      
      total_sample_count = num_iter * FLAGS.batch_size
      step = 0
      while step < num_iter and not coord.should_stop():
        n_pos, n_tp, n_fp = sess.run([num_pos, num_tp, num_fp])
        label_count += n_pos
        tp_count += n_tp
        fp_count += n_fp
        step += 1

      # Compute p, r, f
      if tp_count + fp_count > 0:
          precision = tp_count / (tp_count + fp_count)
      else:
          precision = 0.0
      recall = tp_count / label_count
      if precision * recall > 0:
          fmean = 2.0 / (1.0 / precision + 1.0 / recall)
      else:
          fmean = 0
      print('step %r in %s on %s: P = %.3f, R = %.3f, F = %.3f' % (int(global_step), 
                       FLAGS.loss_type, FLAGS.dataset, precision, recall, fmean))

      summary = tf.Summary()
      summary.ParseFromString(sess.run(summary_op))
      summary.value.add(tag='Precision', simple_value=precision)
      summary.value.add(tag='Recall', simple_value=recall)
      summary.value.add(tag='Fmean', simple_value=fmean)
      summary_writer.add_summary(summary, global_step)
    except Exception as e:  # pylint: disable=broad-except
      coord.request_stop(e)

    coord.request_stop()
    coord.join(threads, stop_grace_period_secs=10)


def evaluate():
  """Eval CIFAR-10 for a number of steps."""
  with tf.Graph().as_default() as g:
    # Get images and labels for CIFAR.
    eval_data = FLAGS.eval_data == 'test'
    images, labels = cifar.inputs(eval_data=eval_data, is_cifar10 = FLAGS.dataset == 'cifar-10')

    labels = tf.cast(tf.equal(labels, 1), dtype = tf.int32)

    # Build a Graph that computes the logits predictions from the
    # inference model.
    logits = cifar.inference(images)[:, 0, 0, 0]

    scores = util.tf.sigmoid(logits)
    predicted = tf.cast(scores > 0.5, dtype = tf.int32)
    
    # Calculate predictions.
    predicted_positive = tf.equal(predicted, 1)
    label_positive = tf.equal(labels, 1)
    tp = tf.logical_and(predicted_positive, label_positive)
    fp = tf.logical_and(predicted_positive, tf.logical_not(label_positive))
    
    num_pos = tf.reduce_sum(tf.cast(labels, dtype = tf.float32))
    num_tp = tf.reduce_sum(tf.cast(tp, tf.float32))
    num_fp = tf.reduce_sum(tf.cast(fp, tf.float32))
    
    # Restore the moving average version of the learned variables for eval.
    variable_averages = tf.train.ExponentialMovingAverage(
        cifar.MOVING_AVERAGE_DECAY)
    variables_to_restore = variable_averages.variables_to_restore()
    saver = tf.train.Saver(variables_to_restore)

    # Build the summary operation based on the TF collection of Summaries.
    summary_op = tf.summary.merge_all()

    summary_writer = tf.summary.FileWriter(FLAGS.eval_dir, g)

    while True:
        for _ in util.tf.wait_for_checkpoint(FLAGS.checkpoint_dir):
            eval_once(saver, summary_writer, num_pos, num_tp, num_fp, summary_op)


def main(argv=None):  # pylint: disable=unused-argument
  util.io.mkdir(FLAGS.eval_dir)
  evaluate()


if __name__ == '__main__':
  FLAGS = parser.parse_args()
  util.proc.set_proc_name('eval_on_%s_%s'%(FLAGS.dataset, FLAGS.loss_type))
  tf.app.run()
