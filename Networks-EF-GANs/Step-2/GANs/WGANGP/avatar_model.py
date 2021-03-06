import os
import math
import numpy as np
import tensorflow as tf
from datetime import datetime
from avatar import Avatar
from PIL import Image
import matplotlib.pyplot as pyplot
import scipy.misc
import GPUtil
import psutil
import os

class AvatarModel:

    def __init__(self,dataname,savemodel,ganimage,gannumber,log):
        self.log = log
        self.gannumber = gannumber
        self.dataname = dataname
        self.savemodel = savemodel
        self.ganimage = ganimage
        self.avatar = Avatar(self.dataname)
        # 真实图片shape (height, width, depth)
        self.img_shape = self.avatar.img_shape
        # 一个batch的图片向量shape (batch, height, width, depth)
        self.batch_shape = self.avatar.batch_shape
        # 一个batch包含图片数量
        self.batch_size = self.avatar.batch_size
        # batch数量
        self.chunk_size = self.avatar.chunk_size
        # 噪音图片size
        self.noise_img_size = self.avatar.noise_img_size
        # 卷积转置输出通道数量
        self.gf_size = self.avatar.gf_size
        # 卷积输出通道数量
        self.df_size = self.avatar.df_size
        # 训练循环次数
        self.epoch_size = self.avatar.epoch_size
        # 学习率
        self.learning_rate = self.avatar.learning_rate
        # 优化指数衰减率
        self.beta1 = self.avatar.beta1
        # 生成图片数量
        self.sample_size  = self.avatar.sample_size

    def RandomWeightedAverage(real_imgs, fake_imgs):
        """Provides a (random) weighted average between real and generated image samples"""
        alpha = np.random.uniform(0,1)
        return (alpha * real_imgs) + ((1 - alpha) * fake_imgs)




    # def wasserstein_loss(self, y_true, y_pred):
    #     return tf.mean(y_true * y_pred)

    @staticmethod
    def conv_out_size_same(size, stride):#输入输出尺寸变化--（输入尺寸，步长）
        return int(math.ceil(float(size) / float(stride)))

    @staticmethod
    def linear(images, output_size, stddev=0.02, bias_start=0.0, name='Linear'):#相当于神经元，Y = W * X + B,且可以一次设多个神经元--（输入图片，输出多少个神经元）
        shape = images.get_shape().as_list()

        with tf.variable_scope(name):
            w = tf.get_variable("w", [shape[1], output_size], tf.float32,
                                tf.random_normal_initializer(stddev=stddev))
            b = tf.get_variable("b", [output_size],
                                initializer=tf.constant_initializer(bias_start))
            return tf.matmul(images, w) + b, w, b

    @staticmethod
    def batch_normalizer(x, epsilon=1e-5, momentum=0.9, train=True, name='batch_norm'):#将每一层的输出神经元强行标准化为0-1的标准正态分布
        with tf.variable_scope(name):
            return tf.contrib.layers.batch_norm(x, decay=momentum, updates_collections=None, epsilon=epsilon,
                                                scale=True, is_training=train)


    @staticmethod
    def conv2d(images, output_dim, stddev=0.02, name="conv2d"):# 2维卷积运算--（输入图片，输出尺寸）
        with tf.variable_scope(name):
            # filter : [height, width, in_channels, output_channels]
            # 注意与转置卷积的不同
            filter_shape = [5, 5, images.get_shape()[-1], output_dim]
            # strides
            # 对应每一维的filter移动步长
            strides_shape = [1, 2, 2, 1]

            w = tf.get_variable('w', filter_shape, initializer=tf.truncated_normal_initializer(stddev=stddev))
            b = tf.get_variable('b', [output_dim], initializer=tf.constant_initializer(0.0))

            conv = tf.nn.conv2d(images, w, strides=strides_shape, padding='SAME')
            conv = tf.reshape(tf.nn.bias_add(conv, b), conv.get_shape())

            return conv

    @staticmethod
    def deconv2d(images, output_shape, stddev=0.02, name='deconv2d'):# 2 维反卷积运算--（输入图片，输出尺寸）
        with tf.variable_scope(name):
            # filter : [height, width, output_channels, in_channels]
            # 注意与卷积的不同
            filter_shape = [5, 5, output_shape[-1], images.get_shape()[-1]]
            # strides
            # 对应每一维的filter移动步长
            strides_shape = [1, 2, 2, 1]

            w = tf.get_variable('w', filter_shape, initializer=tf.random_normal_initializer(stddev=stddev))
            b = tf.get_variable('biases', [output_shape[-1]], initializer=tf.constant_initializer(0.0))

            deconv = tf.nn.conv2d_transpose(images, w, output_shape=output_shape, strides=strides_shape)
            deconv = tf.nn.bias_add(deconv, b)

            return deconv, w, b

    @staticmethod
    def lrelu(x, leak=0.2):
        return tf.maximum(x, leak * x)
    def leaky_relu(x,  leak=0.2):
        return tf.maximum(x, leak * x)
    def generator(self, noise_imgs, train=True):
        with tf.variable_scope('generator'):
            # 分别对应每个layer的height, width
            s_h, s_w, _ = self.img_shape
            s_h2, s_w2 = self.conv_out_size_same(s_h, 2), self.conv_out_size_same(s_w, 2)
            s_h4, s_w4 = self.conv_out_size_same(s_h2, 2), self.conv_out_size_same(s_w2, 2)
            s_h8, s_w8 = self.conv_out_size_same(s_h4, 2), self.conv_out_size_same(s_w4, 2)
            s_h16, s_w16 = self.conv_out_size_same(s_h8, 2), self.conv_out_size_same(s_w8, 2)

            # layer 0
            # 对输入噪音图片进行线性变换

            z, h0_w, h0_b = self.linear(noise_imgs, self.gf_size*8*s_h16*s_w16)
            # reshape为合适的输入层格式
            h0 = tf.reshape(z, [-1, s_h16, s_w16, self.gf_size * 8])
            # 对数据进行归一化处理 加快收敛速度
            h0 = self.batch_normalizer(h0, train=train, name='g_bn0')
            # 激活函数
            h0 = tf.nn.relu(h0)

            # layer 1
            # 卷积转置进行上采样
            h1, h1_w, h1_b = self.deconv2d(h0, [self.batch_size, s_h8, s_w8, self.gf_size*4], name='g_h1')
            h1 = self.batch_normalizer(h1, train=train, name='g_bn1')
            h1 = tf.nn.relu(h1)

            # layer 2
            h2, h2_w, h2_b = self.deconv2d(h1, [self.batch_size, s_h4, s_w4, self.gf_size*2], name='g_h2')
            h2 = self.batch_normalizer(h2, train=train, name='g_bn2')
            h2 = tf.nn.relu(h2)

            # layer 3
            h3, h3_w, h3_b = self.deconv2d(h2, [self.batch_size, s_h2, s_w2, self.gf_size*1], name='g_h3')
            h3 = self.batch_normalizer(h3, train=train, name='g_bn3')
            h3 = tf.nn.relu(h3)

            # layer 4
            h4, h4_w, h4_b = self.deconv2d(h3, self.batch_shape, name='g_h4')
            return tf.nn.tanh(h4)

    def discriminator(self, real_imgs, reuse=False):
        with tf.variable_scope("discriminator", reuse=reuse):
            # 64*64*64
            conv1 = tf.layers.conv2d(real_imgs, 64, kernel_size=[5, 5], strides=[2, 2], padding="SAME",
                                     kernel_initializer=tf.truncated_normal_initializer(stddev=0.02),
                                     name='conv1')
            # self.leaky_relu(conv1, n='act1')
            # act1 = self.leaky_relu(conv1, n='act1')
            act1 = self.lrelu(conv1)
            # 32*32*128
            conv2 = tf.layers.conv2d(act1, 128, kernel_size=[5, 5], strides=[2, 2], padding="SAME",
                                     kernel_initializer=tf.truncated_normal_initializer(stddev=0.02),
                                     name='conv2')
            bn2 = tf.contrib.layers.batch_norm(conv2, is_training=True, epsilon=1e-5, decay=0.9,
                                               updates_collections=None, scope='bn2')
            act2 = self.lrelu(bn2)

            # 16*16*256
            conv3 = tf.layers.conv2d(act2, 256, kernel_size=[5, 5], strides=[2, 2], padding="SAME",
                                     kernel_initializer=tf.truncated_normal_initializer(stddev=0.02),
                                     name='conv3')
            bn3 = tf.contrib.layers.batch_norm(conv3, is_training=True, epsilon=1e-5, decay=0.9,
                                               updates_collections=None, scope='bn3')
            act3 = self.lrelu(bn3)

            # 8*8*512
            conv4 = tf.layers.conv2d(act3, 512, kernel_size=[5, 5], strides=[2, 2], padding="SAME",
                                     kernel_initializer=tf.truncated_normal_initializer(stddev=0.02),
                                     name='conv4')
            bn4 = tf.contrib.layers.batch_norm(conv4, is_training=True, epsilon=1e-5, decay=0.9,
                                               updates_collections=None,
                                               scope='bn4')
            act4 = self.lrelu(bn4)

            # start from act4
            dim = int(np.prod(act4.get_shape()[1:]))
            fc1 = tf.reshape(act4, shape=[-1, dim], name='fc1')
            w2 = tf.get_variable('w2', shape=[fc1.shape[-1], 1], dtype=tf.float32,
                                 initializer=tf.truncated_normal_initializer(stddev=0.02))
            b2 = tf.get_variable('b2', shape=[1], dtype=tf.float32,
                                 initializer=tf.constant_initializer(0.0))
            # wgan不适用sigmoid
            logits = tf.add(tf.matmul(fc1, w2), b2, name='logits')

            return logits
            # # layer 0
            #             # # 卷积操作
            #             # h0 = self.conv2d(real_imgs, self.df_size, name='d_h0_conv')
            #             # # 激活函数
            #             # h0 = self.lrelu(h0)
            #             #
            #             # # layer 1
            #             # h1 = self.conv2d(h0, self.df_size*2, name='d_h1_conv')
            #             # h1 = self.batch_normalizer(h1, name='d_bn1')
            #             # h1 = self.lrelu(h1)
            #             #
            #             # # layer 2
            #             # h2 = self.conv2d(h1, self.df_size*4, name='d_h2_conv')
            #             # h2 = self.batch_normalizer(h2, name='d_bn2')
            #             # h2 = self.lrelu(h2)
            #             #
            #             # # layer 3
            #             # h3 = self.conv2d(h2, self.df_size*8, name='d_h3_conv')
            #             # h3 = self.batch_normalizer(h3, name='d_bn3')
            #             # h3 = self.lrelu(h3)
            #             #
            #             # # layer 4
            #             # h4, _, _ = self.linear(tf.reshape(h3, [self.batch_size, -1]), 1, name='d_h4_lin')
            #             #
            #             # return tf.nn.sigmoid(h4), h4

    # # 定义WGAN
    # fake_image = generator(random_input, z_dim, is_train)
    # real_result = discriminator(real_image, is_train)
    # fake_result = discriminator(fake_image, is_train, reuse=True)
    #
    # # 定义损失函数，这是WGAN的改进所在
    # d_loss = tf.reduce_mean(fake_result) - tf.reduce_mean(real_result)  # This optimizes the discriminator.
    # g_loss = -tf.reduce_mean(fake_result)  # This optimizes the generator.
    #

    # # 模型构建完毕------------------------------------------------------------------
    def gradient_penalty_loss(self,  y_pred, averaged_samples):
        """
        Computes gradient penalty based on prediction and weighted real / fake samples
        """
        gradients = tf.gradients(y_pred, averaged_samples)[0]
        # compute the euclidean norm by squaring ...
        gradients_sqr = tf.square(gradients)
        #   ... summing over the rows ...
        gradients_sqr_sum = tf.reduce_sum(gradients_sqr,
                                  axis=np.arange(1, len(gradients_sqr.shape)))
        #   ... and sqrt
        gradient_l2_norm = tf.sqrt(gradients_sqr_sum)
        # compute lambda * (1 - ||grad||)^2 still for each single sample
        gradient_penalty = tf.square(1 - gradient_l2_norm)
        # return the mean as loss over all the batch samples
        return tf.reduce_mean(gradient_penalty)
    @staticmethod
    def loss_graph(real_logits, fake_logits):
        # 生成器图片loss
        # 生成器希望判别器判断出来的标签为1
        gen_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=fake_logits, labels=tf.ones_like(fake_logits)))
        # 判别器识别生成器图片loss
        # 判别器希望识别出来的标签为0
        fake_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=fake_logits, labels=tf.zeros_like(fake_logits)))
        # 判别器识别真实图片loss
        # 判别器希望识别出来的标签为1
        real_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=real_logits, labels=tf.ones_like(real_logits)))
        # 判别器总loss
        dis_loss = tf.add(fake_loss, real_loss)
        return gen_loss, fake_loss, real_loss, dis_loss

    # # 定义方差
    # t_vars = tf.trainable_variables()
    # d_vars = [var for var in t_vars if 'discriminator' in var.name]
    # g_vars = [var for var in t_vars if 'generator' in var.name]
    #
    # # 定义优化器，这里使用RMSProp
    # trainer_d = tf.train.RMSPropOptimizer(learning_rate=0.0002).minimize(d_loss, var_list=d_vars)
    # trainer_g = tf.train.RMSPropOptimizer(learning_rate=0.0002).minimize(g_loss, var_list=g_vars)
    #
    # # 权值裁剪至[-0.01, 0.01]
    # d_clip = [v.assign(tf.clip_by_value(v, -0.01, 0.01)) for v in d_vars]
    @staticmethod
    def optimizer_graph(gen_loss, dis_loss, learning_rate,partial_gp_loss_validity_interpolated):
        # 所有定义变量
        train_vars = tf.trainable_variables()
        # 生成器变量
        gen_vars = [var for var in train_vars if var.name.startswith('generator')]
        # 判别器变量
        dis_vars = [var for var in train_vars if var.name.startswith('discriminator')]
        # optimizer
        # 生成器与判别器作为两个网络需要分别优化
        gen_optimizer = tf.train.RMSPropOptimizer(learning_rate=learning_rate).minimize(gen_loss, var_list=gen_vars)
        dis_optimizer = tf.train.RMSPropOptimizer(learning_rate=learning_rate).minimize(dis_loss, var_list=dis_vars)
        interpolated_optimizer = tf.train.RMSPropOptimizer(learning_rate=learning_rate).minimize(partial_gp_loss_validity_interpolated, var_list=dis_vars)
        return gen_optimizer, dis_optimizer , interpolated_optimizer

    def train(self):
        # 真实图片
        real_imgs = tf.placeholder(tf.float32, self.batch_shape, name='real_images')
        # 噪声图片
        noise_imgs = tf.placeholder(tf.float32, [None, self.noise_img_size], name='noise_images')

        # 生成器图片
        fake_imgs = self.generator(noise_imgs)

        # 判别器
        # real_outputs, real_logits = self.discriminator(real_imgs)
        # fake_outputs, fake_logits = self.discriminator(fake_imgs, reuse=True)
        real_logits = self.discriminator(real_imgs)
        fake_logits = self.discriminator(fake_imgs, reuse=True)

        # Construct weighted average between real and fake images
        alpha = tf.random.uniform((self.batch_size,1,1,1),0, 1)
        interpolated_img = (alpha * real_imgs) + ((1 - alpha) * fake_imgs)
        # Determine validity of weighted sample
        validity_interpolated = self.discriminator(interpolated_img, reuse=True)

        # 损失
        gen_loss, fake_loss, real_loss, dis_loss = self.loss_graph(real_logits, fake_logits)
        partial_gp_loss_validity_interpolated = self.gradient_penalty_loss(validity_interpolated,interpolated_img)
        # 优化
        gen_optimizer, dis_optimizer ,interpolated_optimizer= self.optimizer_graph(gen_loss, dis_loss, self.learning_rate, partial_gp_loss_validity_interpolated)

        # 开始训练
        saver = tf.train.Saver()
        step = 0
        # 指定占用GPU比例
        # tensorflow默认占用全部GPU显存 防止在机器显存被其他程序占用过多时可能在启动时报错
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.8)
        with tf.Session(config=tf.ConfigProto(gpu_options=gpu_options)) as sess:
            sess.run(tf.global_variables_initializer())
            for epoch in range(self.epoch_size):
                batches = self.avatar.batches()
                for batch_imgs in batches:

                    # generator的输入噪声
                    noises = np.random.uniform(-1, 1, size=(self.batch_size, self.noise_img_size)).astype(np.float32)
                    # 优化
                    _ = sess.run(dis_optimizer, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
                    _ = sess.run(gen_optimizer, feed_dict={noise_imgs: noises})
                    # _ = sess.run(gen_optimizer, feed_dict={noise_imgs: noises})
                    _ = sess.run(interpolated_optimizer, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
                    step += 1

                    # print(datetime.now().strftime('%c'), epoch, step)
                # 每一轮结束计算loss
                # 判别器损失
                loss_dis = sess.run(dis_loss, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
                # 判别器对真实图片
                loss_real = sess.run(real_loss, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
                # 判别器对生成器图片
                loss_fake = sess.run(fake_loss, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
                # 生成器损失
                loss_gen = sess.run(gen_loss, feed_dict={noise_imgs: noises})
                loss_partial_gp = sess.run(partial_gp_loss_validity_interpolated, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
                GPUs = GPUtil.getGPUs()
                info = psutil.virtual_memory()
                print(datetime.now().strftime('%c'), ' epoch:', epoch + 1, ' step:', step, ' loss_dis:', loss_dis,
                      ' loss_real:', loss_real, ' loss_fake:', loss_fake, ' loss_gen:', loss_gen)
                # print(' epoch:', epoch)
                # print(datetime.now().strftime('%c'), ' epoch:', epoch, ' step:', step, ' loss_dis:', loss_dis,
                #       ' loss_real:', loss_real, ' loss_fake:', loss_fake, ' loss_gen:', loss_gen,'partial_gp_loss:',partial_gp_loss)
                # GPUtil.showUtilization()
                # print( '总显存：', '{:.2f}'.format(GPUs[0].memoryTotal/1024) ,'G  ', '\t显存使用：', '{:.2f}'.format(GPUs[0].memoryUsed/1024),'G  ', '\t显存空闲：', '{:.2f}'.format(GPUs[0].memoryFree/1024),'G  ' )
                # print('总内存：', '{:.2f}'.format(info.total/1024/1024/1024),'G  ','\t内存使用：', '{:.2f}'.format(psutil.Process(os.getpid()).memory_info().rss/1024/1024/1024),'G  ',
                # '\t内存占比：', (info.percent),'%  ','\ncpu个数：', psutil.cpu_count(),' \tcpu使用率：',psutil.cpu_percent(None),'%  ')
                filename = self.log
                with open(filename, 'a+') as file_object:
                    s1 = "epoch:{} \nloss_dis:{}\nloss_real:{}\nloss_fake:{}\nloss_gen:{}\nloss_partial_gp:{}".format(epoch + 1,
                                                                                                  '{:.4f}'.format(
                                                                                                      loss_dis),
                                                                                                  '{:.4f}'.format(
                                                                                                      loss_real),
                                                                                                  '{:.4f}'.format(
                                                                                                      loss_fake),
                                                                                                  '{:.4f}'.format(
                                                                                                      loss_gen),
                                                                                                   '{:.4f}'.format(loss_partial_gp))


                    s2 = "总显存：{}G\t显存使用{}G\t显存空闲：{}G".format('{:.2f}'.format(GPUs[0].memoryTotal / 1024),
                                                             '{:.2f}'.format(GPUs[0].memoryUsed / 1024),
                                                             '{:.2f}'.format(GPUs[0].memoryFree / 1024))
                    # s3 = ('总显存：', '{:.2f}'.format(GPUs[0].memoryTotal / 1024), 'G  ', '\t显存使用：',
                    #       '{:.2f}'.format(GPUs[0].memoryUsed / 1024), 'G  ', '\t显存空闲：',
                    #       '{:.2f}'.format(GPUs[0].memoryFree / 1024), 'G  ')
                    s3 = "总内存：{}G\t内存使用：{}G\t内存占比：{}%\ncpu个数：{}\tcpu使用率：{}%" \
                        .format('{:.2f}'.format(info.total / 1024 / 1024 / 1024),
                                '{:.2f}'.format(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024 / 1024),
                                (info.percent),
                                psutil.cpu_count(),
                                psutil.cpu_percent(None))
                    # s4 = ('总内存：', '{:.2f}'.format(info.total / 1024 / 1024 / 1024), 'G  ', '\t内存使用：',
                    #       '{:.2f}'.format(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024 / 1024), 'G  ',
                    #       '\t内存占比：', (info.percent), '%  ', '\ncpu个数：', psutil.cpu_count(), ' \tcpu使用率：',
                    #       psutil.cpu_percent(None), '%  ')
                    file_object.write(s1 + '\n')
                    file_object.write(s2 + '\n')
                    file_object.write(s3 + '\n')


            model_path = self.savemodel + "wgangp.model"
            saver.save(sess, model_path + ".ckpt")
        # fake_imgs = self.generator(noise_imgs)
        # iim=fake_imgs[1,:,:,:]
        # r = Image.fromarray(iim[:, :, 0]).astype('uint8').convert('L')
        # g = Image.fromarray(iim[:, :, 1]).astype('uint8').convert('L')
        # b = Image.fromarray(iim[:, :, 2]).astype('uint8').convert('L')
        #
        # ###merge the three channels###
        # image11 = Image.merge("RGB", (r, g, b))
        # pyplot.imshow(image11)
        # pyplot.show()

    def gen(self):
        # 生成图片
        noise_imgs = tf.placeholder(tf.float32, [None, self.noise_img_size], name='noise_imgs')
        sample_imgs = self.generator(noise_imgs, train=False)
        saver = tf.train.Saver()
        for i in range(self.gannumber // self.sample_size if self.gannumber // self.sample_size > 0 else 1):

            with tf.Session() as sess:
                sess.run(tf.global_variables_initializer())
                saver.restore(sess, self.savemodel  + "wgangp.model.ckpt")

                sample_noise = np.random.uniform(-1, 1, size=(self.sample_size, self.noise_img_size))
                samples = sess.run(sample_imgs, feed_dict={noise_imgs: sample_noise})
            for num in range(len(samples)):
                self.avatar.save_img(scipy.misc.imresize(samples[num],[96,192,3]), self.ganimage  + 'epoch' + str(
                                         self.epoch_size) + '-' + str(num + 1+i*self.sample_size) + '.jpg')

# import os
# import math
# import numpy as np
# import tensorflow as tf
# from datetime import datetime
# from avatarDcgan.avatar import Avatar
# from PIL import Image
# import matplotlib.pyplot as pyplot
# class AvatarModel:
#
#     def __init__(self):
#         self.avatar = Avatar()
#         # 真实图片shape (height, width, depth)
#         self.img_shape = self.avatar.img_shape
#         # 一个batch的图片向量shape (batch, height, width, depth)
#         self.batch_shape = self.avatar.batch_shape
#         # 一个batch包含图片数量
#         self.batch_size = self.avatar.batch_size
#         # batch数量
#         self.chunk_size = self.avatar.chunk_size
#
#         # 噪音图片size
#         self.noise_img_size = self.avatar.noise_img_size
#         # 卷积转置输出通道数量
#         self.gf_size = self.avatar.gf_size
#         # 卷积输出通道数量
#         self.df_size = self.avatar.df_size
#         # 训练循环次数
#         self.epoch_size = self.avatar.epoch_size
#         # 学习率
#         self.learning_rate = self.avatar.learning_rate
#         # 优化指数衰减率
#         self.beta1 = self.avatar.beta1
#         # 生成图片数量
#         self.sample_size  = self.avatar.sample_size
#
#     @staticmethod
#     def conv_out_size_same(size, stride):#输入输出尺寸变化--（输入尺寸，步长）
#         return int(math.ceil(float(size) / float(stride)))
#
#     @staticmethod
#     def linear(images, output_size, stddev=0.02, bias_start=0.0, name='Linear'):#相当于神经元，Y = W * X + B,且可以一次设多个神经元--（输入图片，输出多少个神经元）
#         shape = images.get_shape().as_list()
#
#         with tf.variable_scope(name):
#             w = tf.get_variable("w", [shape[1], output_size], tf.float32,
#                                 tf.random_normal_initializer(stddev=stddev))
#             b = tf.get_variable("b", [output_size],
#                                 initializer=tf.constant_initializer(bias_start))
#             return tf.matmul(images, w) + b, w, b
#
#     @staticmethod
#     def batch_normalizer(x, epsilon=1e-5, momentum=0.9, train=True, name='batch_norm'):#将每一层的输出神经元强行标准化为0-1的标准正态分布
#         with tf.variable_scope(name):
#             return tf.contrib.layers.batch_norm(x, decay=momentum, updates_collections=None, epsilon=epsilon,
#                                                 scale=True, is_training=train)
#
#     @staticmethod
#     def conv2d(images, output_dim, stddev=0.02, name="conv2d"):# 2维卷积运算--（输入图片，输出尺寸）
#         with tf.variable_scope(name):
#             # filter : [height, width, in_channels, output_channels]
#             # 注意与转置卷积的不同
#             filter_shape = [5, 5, images.get_shape()[-1], output_dim]
#             # strides
#             # 对应每一维的filter移动步长
#             strides_shape = [1, 2, 2, 1]
#
#             w = tf.get_variable('w', filter_shape, initializer=tf.truncated_normal_initializer(stddev=stddev))
#             b = tf.get_variable('b', [output_dim], initializer=tf.constant_initializer(0.0))
#
#             conv = tf.nn.conv2d(images, w, strides=strides_shape, padding='SAME')
#             conv = tf.reshape(tf.nn.bias_add(conv, b), conv.get_shape())
#
#             return conv
#
#     @staticmethod
#     def deconv2d(images, output_shape, stddev=0.02, name='deconv2d'):# 2 维反卷积运算--（输入图片，输出尺寸）
#         with tf.variable_scope(name):
#             # filter : [height, width, output_channels, in_channels]
#             # 注意与卷积的不同
#             filter_shape = [5, 5, output_shape[-1], images.get_shape()[-1]]
#             # strides
#             # 对应每一维的filter移动步长
#             strides_shape = [1, 2, 2, 1]
#
#             w = tf.get_variable('w', filter_shape, initializer=tf.random_normal_initializer(stddev=stddev))
#             b = tf.get_variable('biases', [output_shape[-1]], initializer=tf.constant_initializer(0.0))
#
#             deconv = tf.nn.conv2d_transpose(images, w, output_shape=output_shape, strides=strides_shape)
#             deconv = tf.nn.bias_add(deconv, b)
#
#             return deconv, w, b
#
#     @staticmethod
#     def lrelu(x, leak=0.2):
#         return tf.maximum(x, leak * x)
#
#     def generator(self, noise_imgs,type = 1 , train=True):
#         with tf.variable_scope('generator'):
#             # 分别对应每个layer的height, width
#             s_h, s_w, _ = self.img_shape
#             s_h2, s_w2 = self.conv_out_size_same(s_h, 2), self.conv_out_size_same(s_w, 2)
#             s_h4, s_w4 = self.conv_out_size_same(s_h2, 2), self.conv_out_size_same(s_w2, 2)
#             s_h8, s_w8 = self.conv_out_size_same(s_h4, 2), self.conv_out_size_same(s_w4, 2)
#             s_h16, s_w16 = self.conv_out_size_same(s_h8, 2), self.conv_out_size_same(s_w8, 2)
#
#             # layer 0
#             # 对输入噪音图片进行线性变换
#             noise_imgs = tf.concat([noise_imgs, type], axis=1)
#             z, h0_w, h0_b = self.linear(noise_imgs, self.gf_size*8*s_h16*s_w16)
#             # reshape为合适的输入层格式
#             h0 = tf.reshape(z, [-1, s_h16, s_w16, self.gf_size * 8])
#             # 对数据进行归一化处理 加快收敛速度
#             h0 = self.batch_normalizer(h0, train=train, name='g_bn0')
#             # 激活函数
#             h0 = tf.nn.relu(h0)
#
#             # layer 1
#             # 卷积转置进行上采样
#             h1, h1_w, h1_b = self.deconv2d(h0, [self.batch_size, s_h8, s_w8, self.gf_size*4], name='g_h1')
#             h1 = self.batch_normalizer(h1, train=train, name='g_bn1')
#             h1 = tf.nn.relu(h1)
#
#             # layer 2
#             h2, h2_w, h2_b = self.deconv2d(h1, [self.batch_size, s_h4, s_w4, self.gf_size*2], name='g_h2')
#             h2 = self.batch_normalizer(h2, train=train, name='g_bn2')
#             h2 = tf.nn.relu(h2)
#
#             # layer 3
#             h3, h3_w, h3_b = self.deconv2d(h2, [self.batch_size, s_h2, s_w2, self.gf_size*1], name='g_h3')
#             h3 = self.batch_normalizer(h3, train=train, name='g_bn3')
#             h3 = tf.nn.relu(h3)
#
#             # layer 4
#             h4, h4_w, h4_b = self.deconv2d(h3, self.batch_shape, name='g_h4')
#             return tf.nn.tanh(h4)
#
#     def discriminator(self, real_imgs, reuse=False):
#         with tf.variable_scope("discriminator", reuse=reuse):
#             # layer 0
#             # 卷积操作
#             h0 = self.conv2d(real_imgs, self.df_size, name='d_h0_conv')
#             # 激活函数
#             h0 = self.lrelu(h0)
#
#             # layer 1
#             h1 = self.conv2d(h0, self.df_size*2, name='d_h1_conv')
#             h1 = self.batch_normalizer(h1, name='d_bn1')
#             h1 = self.lrelu(h1)
#
#             # layer 2
#             h2 = self.conv2d(h1, self.df_size*4, name='d_h2_conv')
#             h2 = self.batch_normalizer(h2, name='d_bn2')
#             h2 = self.lrelu(h2)
#
#             # layer 3
#             h3 = self.conv2d(h2, self.df_size*8, name='d_h3_conv')
#             h3 = self.batch_normalizer(h3, name='d_bn3')
#             h3 = self.lrelu(h3)
#
#             # layer 4
#             h4, _, _ = self.linear(tf.reshape(h3, [self.batch_size, -1]), 1, name='d_h4_lin')
#             Y_ = tf.layers.dense(h4, units=5)
#             return tf.nn.sigmoid(h4), h4,Y_
#
#     @staticmethod
#     def loss_graph(real_logits, fake_logits):
#         # 生成器图片loss
#         # 生成器希望判别器判断出来的标签为1
#         gen_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=fake_logits, labels=tf.ones_like(fake_logits)))
#         # 判别器识别生成器图片loss
#         # 判别器希望识别出来的标签为0
#         fake_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=fake_logits, labels=tf.zeros_like(fake_logits)))
#         # 判别器识别真实图片loss
#         # 判别器希望识别出来的标签为1
#         real_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=real_logits, labels=tf.ones_like(real_logits)))
#         # 判别器总loss
#         dis_loss = tf.add(fake_loss, real_loss)
#         return gen_loss, fake_loss, real_loss, dis_loss
#
#     @staticmethod
#     def optimizer_graph(gen_loss, dis_loss, learning_rate, beta1):
#         # 所有定义变量
#         train_vars = tf.trainable_variables()
#         # 生成器变量
#         gen_vars = [var for var in train_vars if var.name.startswith('generator')]
#         # 判别器变量
#         dis_vars = [var for var in train_vars if var.name.startswith('discriminator')]
#         # optimizer
#         # 生成器与判别器作为两个网络需要分别优化
#         gen_optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate, beta1=beta1).minimize(gen_loss, var_list=gen_vars)
#         dis_optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate, beta1=beta1).minimize(dis_loss, var_list=dis_vars)
#         return gen_optimizer, dis_optimizer
#
#     def train(self):
#         # 真实图片
#         real_imgs = tf.placeholder(tf.float32, self.batch_shape, name='real_images')
#         # 噪声图片
#         noise_imgs = tf.placeholder(tf.float32, [None, self.noise_img_size], name='noise_images')
#
#         # 生成器图片
#         fake_imgs = self.generator(noise_imgs,np.arange(self.sample_size)%21+1)
#
#         # 判别器
#         real_outputs, real_logits,Y_ = self.discriminator(real_imgs)
#         fake_outputs, fake_logits,Y_ = self.discriminator(fake_imgs, reuse=True)
#
#         # 损失
#         gen_loss, fake_loss, real_loss, dis_loss = self.loss_graph(real_logits, fake_logits)
#         # 优化
#         gen_optimizer, dis_optimizer = self.optimizer_graph(gen_loss, dis_loss, self.learning_rate, self.beta1)
#
#         # 开始训练
#         saver = tf.train.Saver()
#         step = 0
#         # 指定占用GPU比例
#         # tensorflow默认占用全部GPU显存 防止在机器显存被其他程序占用过多时可能在启动时报错
#         gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.8)
#         with tf.Session(config=tf.ConfigProto(gpu_options=gpu_options)) as sess:
#             sess.run(tf.global_variables_initializer())
#             for epoch in range(self.epoch_size):
#                 batches = self.avatar.batches()
#                 for batch_imgs in batches:
#                     # generator的输入噪声
#                     noises = np.random.uniform(-1, 1, size=(self.batch_size, self.noise_img_size)).astype(np.float32)
#                     # 优化
#                     _ = sess.run(dis_optimizer, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
#                     _ = sess.run(gen_optimizer, feed_dict={noise_imgs: noises})
#                     _ = sess.run(gen_optimizer, feed_dict={noise_imgs: noises})
#                     step += 1
#                     # print(datetime.now().strftime('%c'), epoch, step)
#                 # 每一轮结束计算loss
#                 # 判别器损失
#                 loss_dis = sess.run(dis_loss, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
#                 # 判别器对真实图片
#                 loss_real = sess.run(real_loss, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
#                 # 判别器对生成器图片
#                 loss_fake = sess.run(fake_loss, feed_dict={real_imgs: batch_imgs, noise_imgs: noises})
#                 # 生成器损失
#                 loss_gen = sess.run(gen_loss, feed_dict={noise_imgs: noises})
#
#                 print(datetime.now().strftime('%c'), ' epoch:', epoch, ' step:', step, ' loss_dis:', loss_dis,
#                       ' loss_real:', loss_real, ' loss_fake:', loss_fake, ' loss_gen:', loss_gen)
#
#             model_path = os.getcwd() + os.sep + "avatar.model"
#             saver.save(sess, model_path, global_step=step)
#         # fake_imgs = self.generator(noise_imgs)
#         # iim=fake_imgs[1,:,:,:]
#         # r = Image.fromarray(iim[:, :, 0]).astype('uint8').convert('L')
#         # g = Image.fromarray(iim[:, :, 1]).astype('uint8').convert('L')
#         # b = Image.fromarray(iim[:, :, 2]).astype('uint8').convert('L')
#         #
#         # ###merge the three channels###
#         # image11 = Image.merge("RGB", (r, g, b))
#         # pyplot.imshow(image11)
#         # pyplot.show()
#
#     def gen(self):
#         # 生成图片
#         noise_imgs = tf.placeholder(tf.float32, [None, self.noise_img_size], name='noise_imgs')
#         sample_imgs = self.generator(noise_imgs,type, train=False)
#         saver = tf.train.Saver()
#         with tf.Session() as sess:
#             sess.run(tf.global_variables_initializer())
#             saver.restore(sess, tf.train.latest_checkpoint('.'))
#             sample_noise = np.random.uniform(-1, 1, size=(self.sample_size, self.noise_img_size))
#             samples = sess.run(sample_imgs, feed_dict={noise_imgs: sample_noise,type:np.arange(self.sample_size)%21+1})
#         for num in range(len(samples)):
#             self.avatar.save_img(samples[num], '6'+os.sep+'epoch'+str(self.epoch_size)+'-'+str(num+1)+'.jpg')
