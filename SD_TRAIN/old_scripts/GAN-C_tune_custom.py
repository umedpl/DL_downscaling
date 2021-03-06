# general tools
import sys
import time
import argparse
from glob import glob

# data tools
import numpy as np
from random import shuffle

# deep learning tools
import tensorflow as tf
from tensorflow import keras
import tensorflow.keras.backend as K

# custom tools
sys.path.insert(0, '/glade/u/home/ksha/WORKSPACE/utils/')
sys.path.insert(0, '/glade/u/home/ksha/WORKSPACE/DL_downscaling/utils/')
sys.path.insert(0, '/glade/u/home/ksha/WORKSPACE/DL_downscaling/')
from namelist import *
import data_utils as du
import model_utils as mu
import train_utils as tu

# parse user inputs
parser = argparse.ArgumentParser()
# positionals
parser.add_argument('v', help='Downscaling variable name')
parser.add_argument('s', help='Training seasons summer/winter')
parser.add_argument('c1', help='Number of input channels (1<c<5)')
parser.add_argument('c2', help='Number of output channels (1<c<3)')
parser.add_argument('t', help='Tuning rounds')
args = vars(parser.parse_args())
# parser handling
VAR, seasons, input_flag, output_flag = tu.parser_handler(args)
N_input = int(np.sum(input_flag))
N_output = int(np.sum(output_flag))
if N_output > 1:
    raise ValueError('UNet accepts only one target')
    
# UNet model macros
# # hidden layer numbers (symetrical)
if VAR == 'PCT':
    print('PCT hidden layer setup')
    N = [64, 96, 128, 160]
else:
    print('T2 hidden layer setup')
    N = [48, 96, 192, 384] #[56, 112, 224, 448]

l = [1e-5, 5e-6] # G lr; D lr
epochs = 150
lmd = 1e-3
# early stopping settings
min_del = 0.00001
max_tol = 10 # early stopping with patience
layer_id = [7, 13, 19]

activation='relu' # leakyReLU instead of ReLU
pool=False # stride convolution instead of maxpooling
key = 'SRGAN'

def loss_model(VAR, sea, layer_id):
    model_path = temp_dir+'DAE_{}_{}_self.hdf'.format(VAR, sea) # model checkpoint
    AE = keras.models.load_model(model_path)
    W = AE.get_weights()

    N = [48, 96, 192, 384]
    input_size = (None, None, 1)
    # DAE
    DAE = mu.DAE(N, input_size)
    DAE.set_weights(W)
    # freeze
    DAE.trainable = False
    for layer in DAE.layers:
        layer.trainable = False
    f_sproj = [DAE.layers[i].output for i in layer_id]
    
    loss_models = []
    for single_proj in f_sproj:
        loss_models.append(keras.models.Model(DAE.inputs, single_proj))
    return loss_models

def CLOSS(y_true, y_pred):
    '''
    MAE style content loss
    '''
    return 0.25*K.mean(K.abs(loss_models[0](y_true) - loss_models[0](y_pred)))+\
           0.25*K.mean(K.abs(loss_models[1](y_true) - loss_models[1](y_pred)))+\
           0.25*K.mean(K.abs(loss_models[2](y_true) - loss_models[2](y_pred)))+\
           0.25*K.mean(K.abs(y_true - y_pred))

def dummy_g_loader(VAR, sea, TUNE):
    if TUNE <= 1:
        model_name = '{}_G_{}_{}'.format(key, VAR, sea)
    else:
        model_name = '{}_G_{}_{}_tune{}'.format(key, VAR, sea, TUNE-1)
    model_path = temp_dir+model_name+'.hdf'
    print('Import model: {}'.format(model_name))
    backbone = keras.models.load_model(model_path, compile=False)
    W = backbone.get_weights()
    return W

def dummy_d_loader(VAR, sea, TUNE):
    if TUNE <= 1:
        model_name = '{}_D_{}_{}'.format(key, VAR, sea)
    else:
        model_name = '{}_D_{}_{}_tune{}'.format(key, VAR, sea, TUNE-1)
    model_path = temp_dir+model_name+'.hdf'
    print('Import model: {}'.format(model_name))
    backbone = keras.models.load_model(model_path)
    W = backbone.get_weights()
    return W


for sea in seasons:
    W = dummy_g_loader(N_input, VAR, sea)
    loss_models = loss_model(VAR, sea, layer_id)
    
    # ---------- Generator ---------- #
    # generator
    G = mu.UNET(N, (None, None, N_input), pool=pool, activation=activation)
    # optimizer
    opt_G = keras.optimizers.Adam(lr=l[0])
    print('Compiling G')
    G.compile(loss=CLOSS, optimizer=opt_G)
    G.set_weights(W)
    
    # ---------- Descriminator ---------- #
    W = dummy_d_loader(VAR, sea)
    input_size = (None, None, N_input+1)
    D = mu.vgg_descriminator(N, input_size)
    opt_D = keras.optimizers.Adam(lr=l[1])
    print('Compiling D')
    D.compile(loss=keras.losses.sparse_categorical_crossentropy, optimizer=opt_D)
    D.set_weights(W)

    # ---------- GAN ---------- #
    D.trainable = False
    for layer in D.layers:
        layer.trainable = False
    #
    GAN_IN = keras.layers.Input((None, None, N_input))
    G_OUT = G(GAN_IN)
    D_IN = keras.layers.Concatenate()([G_OUT, GAN_IN])
    D_OUT = D(D_IN)
    GAN = keras.models.Model(GAN_IN, [G_OUT, D_OUT])

    print('Compiling GAN')
    # content_loss + 1e-3 * adversarial_loss
    GAN.compile(loss=[CLOSS, keras.losses.sparse_categorical_crossentropy], 
                loss_weights=[1.0, lmd],
                optimizer=opt_G)

    # ---------- Training settings ---------- #
    # Macros
    input_flag = [False, True, False, False, True, True] # LR T2, HR elev, LR elev
    output_flag = [True, False, False, False, False, False] # HR T2
    inout_flag = [True, True, False, False, True, True]
    labels = ['batch', 'batch'] # input and output labels

    # Filepath
    file_path = BATCH_dir
    trainfiles = glob(file_path+'{}_BATCH_*_TORI_*{}*.npy'.format(VAR, sea)) # e.g., TMAX_BATCH_128_VORIAUG_mam30.npy
    validfiles = glob(file_path+'{}_BATCH_*_VORI_*{}*.npy'.format(VAR, sea))
    # shuffle filenames
    shuffle(trainfiles)
    shuffle(validfiles)
    #
    L_train = len(trainfiles)
    gen_valid = tu.grid_grid_gen(validfiles, labels, input_flag, output_flag)

    # model names
    G_name = '{}_G_{}_{}_tune{}'.format(key, VAR, sea, TUNE)
    D_name = '{}_D_{}_{}_tune{}'.format(key, VAR, sea, TUNE)
    G_path = temp_dir+G_name+'.hdf'
    D_path = temp_dir+D_name+'.hdf'
    hist_path = temp_dir+'{}_LOSS_{}_{}_tune{}.npy'.format(key, VAR, sea, TUNE)

    # loss backup
    GAN_LOSS = np.zeros([int(epochs*L_train), 3])*np.nan
    D_LOSS = np.zeros([int(epochs*L_train)])*np.nan
    V_LOSS = np.zeros([epochs])*np.nan

    tol = 0
    train_size = 100
    for i in range(epochs):
        print('epoch = {}'.format(i))
        if i == 0:
            record = G.evaluate_generator(gen_valid, verbose=1)
            print('Initial validation loss: {}'.format(record))

        start_time = time.time()
        # learning rate schedule

        # shuffling at epoch begin
        shuffle(trainfiles)
        # loop over batches
        for j, name in enumerate(trainfiles):
            inds = du.shuffle_ind(batch_size)
            inds = inds[:train_size]
            # dynamic soft labels
            y_bad = np.ones(train_size)*0.1 + np.random.uniform(-0.02, 0.02, train_size)
            y_good = np.ones(train_size) - y_bad
            dummy_bad = keras.utils.to_categorical(y_bad)
            dummy_good = keras.utils.to_categorical(y_good)
            
            # import batch data
            temp_batch = np.load(name, allow_pickle=True)[()]
            X = temp_batch['batch'][inds, ...]

            # get G_output
            g_in = X[..., input_flag]
            g_out = G.predict([g_in]) # <-- np.array
 
            # test D with G_output
            d_in_fake = np.concatenate((g_out, g_in), axis=-1) # channel last
            d_in_true = X[..., inout_flag]
            
            d_loss1 = D.train_on_batch(d_in_true, dummy_good)
            d_loss2 = D.train_on_batch(d_in_fake, dummy_bad)
            d_loss = d_loss1 + d_loss2

            # G training
            gan_in = X[..., input_flag]
            gan_target = [X[..., output_flag], dummy_good]
            gan_loss = GAN.train_on_batch(gan_in, gan_target)

            # Backup training loss
            D_LOSS[i*L_train+j] = d_loss
            GAN_LOSS[i*L_train+j, :] = gan_loss
            #
            if j%50 == 0:
                print('\t{} step loss = {}'.format(j, gan_loss))
        # on epoch-end
        record_temp = G.evaluate_generator(gen_valid, verbose=1)

        # Backup validation loss
        V_LOSS[i] = record_temp
        # Overwrite loss info
        LOSS = {'GAN_LOSS':GAN_LOSS, 'D_LOSS':D_LOSS, 'V_LOSS':V_LOSS}
        np.save(hist_path, LOSS)

        if record - record_temp > min_del:
            print('Validation loss improved from {} to {}'.format(record, record_temp))
            record = record_temp
            tol = 0
            print('tol: {}'.format(tol))
            # save
            print('save to: {}\n\t{}'.format(G_path, D_path))
            G.save(G_path)
            D.save(D_path)
        else:
            print('Validation loss {} NOT improved'.format(record_temp))
            tol += 1
            print('tol: {}'.format(tol))
            if tol >= max_tol:
                print('Early stopping')
                sys.exit();
            else:
                print('Pass to the next epoch')
                continue;

        print("--- %s seconds ---" % (time.time() - start_time))
        # mannual callbacks