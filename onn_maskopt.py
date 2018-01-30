import model
import layers.optics as optics
from layers.utils import *

import os
os.environ["CUDA_VISIBLE_DEVICES"]="1"

import numpy as np
import tensorflow as tf

from glob import glob

from datetime import datetime

class PhaseMaskModel(model.Model):
    def __init__(self,
                 dim, dims,
                 wavelength,
                 pixel_size,
                 n,
                 ckpt_path):

        self.dim = dim
        self.wave_resolution = dims
        self.wavelength = wavelength
        self.pixel_size = pixel_size
        self.n = n
        self.r_NA = 35

        super(PhaseMaskModel, self).__init__(name='PhaseMask_ONN', ckpt_path=ckpt_path)

    def _build_graph(self, x_train, hm_reg_scale, hm_init_type='random_normal'):
        with tf.device('/device:GPU:0'):
            sensordims = (self.dim,self.dim)
            # Start with input image
            input_img = x_train/tf.reduce_sum(x_train)
            tf.summary.image('input_image', x_train)
            
            doAmplitudeMask=False
            output_img = optical_conv_layer(input_img, hm_reg_scale, self.r_NA, n=self.n, wavelength=self.wavelength,
                       coherent=False, amplitude_mask=doAmplitudeMask, name='maskopt')
            
            # output_img = optics.Sensor(input_is_intensities=False, resolution=sensordims)(output_img)
            
            # Attach images to summary
            # tf.summary.image('output_image', output_img)
            return output_img

    def _get_data_loss(self, model_output, ground_truth):
        model_output = tf.cast(model_output, tf.float32)
        ground_truth = tf.cast(ground_truth, tf.float32)
        
        # model_output /= tf.reduce_max(model_output)
        ground_truth /= tf.reduce_sum(ground_truth)
        with tf.name_scope('data_loss'):
            optics.attach_img('model_output', model_output)
            optics.attach_img('ground_truth', ground_truth)
        loss = tf.reduce_mean(tf.abs(model_output - ground_truth))
        return loss

    def _get_training_queue(self, batch_size, num_threads=4):
        dim = self.dim
        
        file_list = tf.matching_files('/media/data/onn/mnistpadded/im_*.png')
        filename_queue = tf.train.string_input_producer(file_list)
      
        image_reader = tf.WholeFileReader()

        _, image_file = image_reader.read(filename_queue)
        image = tf.image.decode_png(image_file,
                                   channels=1,
                                   dtype=tf.uint8)
        image = tf.cast(image, tf.float32) # Shape [height, width, 1]
        image = tf.expand_dims(image, 0)
        image /= 255.

        # Get the ratio of the patch size to the smallest side of the image
        img_height_width = tf.cast(tf.shape(image)[1:3], tf.float32)
        size_ratio = dim/tf.reduce_min(img_height_width)

        # Extract a glimpse from the image
        #offset_center = tf.random_uniform([1,2], minval=0.0 + size_ratio/2, maxval=1.0-size_ratio/2, dtype=tf.float32)
        offset_center = tf.random_uniform([1,2], minval=0, maxval=0, dtype=tf.float32)
        offset_center = offset_center * img_height_width

        image = tf.image.extract_glimpse(image, size=[dim,dim], offsets=offset_center, centered=True, normalized=False)
        image = tf.squeeze(image, 0)

        convolved_image = tf.expand_dims(image, 0)
        psf = tf.convert_to_tensor(np.load('maskopt/opticalcorrelator_w-conv1.npy'), tf.float32)
        psf /= tf.reduce_sum(psf)
        optics.attach_img('gt_psf', tf.expand_dims(tf.expand_dims(tf.squeeze(psf), 0), -1))
        
        psf = tf.expand_dims(tf.expand_dims(tf.squeeze(psf), -1), -1)
        # psf = tf.transpose(psf, [1,2,0,3])
        
        pad = int(dim/2)
        convolved_image = tf.abs(optics.fft_conv2d(fftpad(convolved_image, pad), fftpad_psf(psf, pad)))
        convolved_image = fftunpad(convolved_image, pad)
        convolved_image = tf.squeeze(convolved_image,axis=0)
        convolved_image /= tf.reduce_sum(convolved_image)

        image_batch, convolved_img_batch = tf.train.batch([image, convolved_image],
                                                          shapes=[[dim,dim,1], [dim,dim,1]],
                                                          batch_size=batch_size,
                                                          num_threads=4,
                                                          capacity=4*batch_size)

        return image_batch, convolved_img_batch


if __name__=='__main__':
    tf.reset_default_graph()

    dim = 84
    dims = np.array((dim,dim))
    wavelength = 532e-9
    pixel_size = 10.8*1e-6
    n=1.48
    num_steps = 30000

    phasemask = PhaseMaskModel(dim, dims, wavelength, pixel_size, n, ckpt_path=None)

    # now = datetime.now()
    run_id = 'opticalcorrelator/'
    log_dir = os.path.join('/media/data/checkpoints/onn/maskopt/', run_id)
    if tf.gfile.Exists(log_dir):
        tf.gfile.DeleteRecursively(log_dir)
    tf.gfile.MakeDirs(log_dir)
    
    phasemask.fit(model_params = {'hm_reg_scale':1e-1},
                   opt_type = 'sgd_with_momentum',
                   #opt_params = {'beta1':0.8, 'beta2':0.999, 'epsilon':1.},
                   opt_params = {'momentum':0.5, 'use_nesterov':True},
                   decay_type = 'polynomial',
                   decay_params = {'decay_steps':num_steps, 'end_learning_rate':1e-9},
                   batch_size=1,
                   starter_learning_rate = 5e-3,
                   num_steps_until_save=500,
                   num_steps_until_summary=50,
                   logdir = log_dir,
                   num_steps = num_steps)
